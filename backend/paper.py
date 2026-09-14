"""Versioned paper trading. No broker I/O; disabling only blocks entries."""
import os
import json
import math
import logging
import sqlite3
import datetime as dt
import pandas as pd
from backend.config import SPREADS
from backend.engine import sinal_smc, score_sinal
from backend.strategy import entry_signal, market_open
from backend.execution import VERSION, INTERVAL_SECONDS, validate_candles, size_position, advance, pnl

log = logging.getLogger(__name__)
BRT = dt.timezone(dt.timedelta(hours=-3))
PASTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'dados')
DB = os.environ.get('PAPER_DB', os.path.join(PASTA, 'paper.db'))
BANCA_INICIAL = 10000.0
_BASE = dict(ativo=True, tf='30min', rr=3.0, risco_pct=0.25,
             hora_ini=8, hora_fim=13, respeitar_horario=True, breakeven=True,
             cb_ativo=True, max_trades_dia=3, perda_max_dia_pct=1.0,
             max_posicoes=3, exposicao_max_pct=1.0, drawdown_max_pct=10.0,
             spread_stop_max=0.20, slippage_spreads=0.5,
             # nota_minima: piso de qualidade (0-100, engine.score_sinal) do
             # torneio multi-par. So usada pela Estrategia A (Didi) — as
             # outras ignoram, mas fica na base pra simplificar a validacao.
             nota_minima=55.0)
ESTRATEGIAS = {
    'A': {**_BASE, 'nome':'Didi (Agulhada)', 'tipo':'didi', 'estagio_min':3},
    'B': {**_BASE, 'ativo':False, 'nome':'Smart Money (Sweep) — suspensa', 'tipo':'smc', 'lookback':10},
    'C': {**_BASE, 'nome':'Tendencia (Rompimento)', 'tipo':'trend', 'rr':2.0,
          'breakeven':False, 'symbols':['XAU/USD','BTC/USD']},
}
CHAVES_VALIDAS = set(_BASE) | {'nome','tipo','estagio_min','lookback','symbols','nota_minima'}


def agora():
    return dt.datetime.now(BRT)


def _spread(symbol):
    if symbol not in SPREADS:
        raise ValueError('Spread nao configurado: '+symbol)
    return SPREADS[symbol]


def _conn():
    os.makedirs(os.path.dirname(os.path.abspath(DB)), exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    # Preserve the pre A/B schema under explicit archive table names. It cannot
    # be mixed safely with versioned campaigns, but no historical row is lost.
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'posicoes' in tables:
        old={r[1] for r in c.execute('PRAGMA table_info(posicoes)')}
        if 'estrategia' not in old:
            if 'posicoes_legacy_v1' in tables:
                raise RuntimeError('Migracao antiga ambigua: posicoes_legacy_v1 ja existe')
            c.execute('ALTER TABLE posicoes RENAME TO posicoes_legacy_v1')
    if 'equity' in tables:
        old={r[1] for r in c.execute('PRAGMA table_info(equity)')}
        if 'estrategia' not in old:
            if 'equity_legacy_v1' in tables:
                raise RuntimeError('Migracao antiga ambigua: equity_legacy_v1 ja existe')
            c.execute('ALTER TABLE equity RENAME TO equity_legacy_v1')
    c.execute('''CREATE TABLE IF NOT EXISTS contas (
        estrategia TEXT PRIMARY KEY, banca REAL, config TEXT, criado_em TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS posicoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, estrategia TEXT,
        aberta_em TEXT, ts_candle TEXT, symbol TEXT, tf TEXT,
        direcao TEXT, entrada REAL, sl REAL, sl_original REAL, tp REAL,
        risco_dinheiro REAL, tamanho REAL, breakeven INTEGER DEFAULT 0,
        status TEXT DEFAULT 'aberta', fechada_em TEXT, preco_saida REAL,
        resultado REAL, resultado_r REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS equity (
        id INTEGER PRIMARY KEY AUTOINCREMENT, estrategia TEXT, ts INTEGER, banca REAL)''')
    columns = {r[1] for r in c.execute('PRAGMA table_info(posicoes)')}
    for name, kind in [('ultimo_candle','TEXT'),('custo','REAL'),('config_entrada','TEXT'),
                       ('versao','TEXT'),('erro_dados','TEXT'),('candle_saida','TEXT')]:
        if name not in columns:
            c.execute(f'ALTER TABLE posicoes ADD COLUMN {name} {kind}')
    c.execute('''CREATE TABLE IF NOT EXISTS ciclos (
        estrategia TEXT, symbol TEXT, tf TEXT, ts TEXT,
        PRIMARY KEY (estrategia,symbol,tf))''')
    for e, cfg in ESTRATEGIAS.items():
        c.execute('INSERT OR IGNORE INTO contas VALUES (?,?,?,?)',
                  (e, BANCA_INICIAL, json.dumps(cfg), agora().isoformat()))
    c.commit()
    return c


def get_config(estr, c=None):
    own = c is None
    c = c or _conn()
    try:
        row = c.execute('SELECT config FROM contas WHERE estrategia=?',(estr,)).fetchone()
        return {**ESTRATEGIAS[estr], **(json.loads(row[0]) if row else {})}
    finally:
        if own:
            c.close()


def validate_config(cfg):
    for k in ['ativo','respeitar_horario','breakeven','cb_ativo']:
        if type(cfg[k]) is not bool:
            raise ValueError(k+': esperado booleano')
    bounds = {'risco_pct':(0.01,2), 'rr':(0.5,10), 'perda_max_dia_pct':(0.1,5),
              'exposicao_max_pct':(0.1,5), 'drawdown_max_pct':(1,20),
              'spread_stop_max':(0.01,0.5), 'slippage_spreads':(0,5),
              'max_trades_dia':(1,20), 'max_posicoes':(1,10),
              'hora_ini':(0,23), 'hora_fim':(1,24), 'nota_minima':(0,100)}
    for k, (lo,hi) in bounds.items():
        v=cfg[k]
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:
            raise ValueError(k+': fora dos limites')
    for k in ['max_trades_dia','max_posicoes','hora_ini','hora_fim']:
        if int(cfg[k])!=cfg[k]:
            raise ValueError(k+': esperado inteiro')
    if cfg['hora_ini']>=cfg['hora_fim'] or cfg['tf'] not in INTERVAL_SECONDS:
        raise ValueError('Horario ou timeframe invalido')
    if cfg['tipo'] not in ('didi','smc','trend') or cfg.get('estagio_min',3) not in (2,3):
        raise ValueError('Estrategia invalida')
    lookback=cfg.get('lookback',10)
    if type(lookback) is not int or not 3<=lookback<=100:
        raise ValueError('Lookback invalido')


def set_config(estr, novo):
    if estr not in ESTRATEGIAS:
        raise ValueError('Estrategia invalida')
    c=_conn()
    try:
        cfg={**get_config(estr,c), **{k:v for k,v in novo.items() if k in CHAVES_VALIDAS}}
        validate_config(cfg)
        c.execute('UPDATE contas SET config=? WHERE estrategia=?',(json.dumps(cfg),estr))
        c.commit()
        return cfg
    finally:
        c.close()


def resetar(estr=None):
    raise ValueError('Reset desativado: preserve o historico e inicie campanha em PAPER_DB separado')


def _banca(c, estr):
    return c.execute('SELECT banca FROM contas WHERE estrategia=?',(estr,)).fetchone()[0]


def _stats_dia(c, estr, symbol=None):
    day=agora().date().isoformat()
    extra=' AND symbol=?' if symbol else ''
    args=(estr,day,symbol) if symbol else (estr,day)
    n=c.execute("SELECT COUNT(*) FROM posicoes WHERE estrategia=? AND substr(aberta_em,1,10)=?"+extra,args).fetchone()[0]
    r=c.execute("SELECT COALESCE(SUM(resultado),0) FROM posicoes WHERE estrategia=? AND substr(fechada_em,1,10)=? AND status!='aberta'"+extra,args).fetchone()[0]
    return n,float(r)


def _risk_state(c, estr):
    bank=_banca(c,estr)
    _,realised=_stats_dia(c,estr)
    start=bank-realised
    exposure=0.0
    for row in c.execute("SELECT * FROM posicoes WHERE estrategia=? AND status='aberta'",(estr,)):
        p=dict(row)
        cost=p['custo'] if p['custo'] is not None else _spread(p['symbol'])
        loss,_=pnl(p,p['sl'],cost)
        exposure+=max(0,-loss)
    peak=max(BANCA_INICIAL,c.execute('SELECT COALESCE(MAX(banca),0) FROM equity WHERE estrategia=?',(estr,)).fetchone()[0])
    return bank,start,realised,exposure,(peak-bank)/peak*100


def _circuit_breaker(c, estr, cfg, symbol=None, novo_risco=0):
    # One global scope for both the panel and entry execution.
    n,_=_stats_dia(c,estr)
    bank,start,realised,exposure,dd=_risk_state(c,estr)
    if bank<=0:
        return True,'Banca esgotada'
    if dd>=cfg['drawdown_max_pct']:
        return True,'Drawdown maximo: campanha exige revisao'
    if exposure+novo_risco>bank*cfg['exposicao_max_pct']/100+1e-8:
        return True,'Limite de risco agregado das posicoes'
    if cfg['cb_ativo']:
        if n>=cfg['max_trades_dia']:
            return True,f"Limite global de {cfg['max_trades_dia']} trades/dia"
        if -realised+exposure+novo_risco>=start*cfg['perda_max_dia_pct']/100-1e-8:
            return True,'Orcamento diario de perda comprometido (inclui posicoes abertas)'
    return False,None


def _sinal_entrada(estr,cfg,aval,df):
    return entry_signal(cfg,aval,df)


def _elegivel_para_entrada(c,estr,symbol,tf,cfg,hora_brt,ts):
    """Todas as travas que valem no momento de abrir (sem tocar no banco).
    Usada tanto pela entrada direta (smc/trend) quanto pelo torneio (didi)."""
    if not cfg['ativo'] or tf!=cfg['tf']:
        return False
    if cfg['respeitar_horario'] and not cfg['hora_ini']<=hora_brt<cfg['hora_fim']:
        return False
    if cfg.get('symbols') and symbol not in cfg['symbols']:
        return False
    if not market_open(symbol,pd.Timestamp(ts)+pd.Timedelta(seconds=INTERVAL_SECONDS[tf])):
        return False
    if c.execute("SELECT 1 FROM posicoes WHERE estrategia=? AND status='aberta' AND (symbol=? OR erro_dados IS NOT NULL)",(estr,symbol)).fetchone():
        return False
    count=c.execute("SELECT COUNT(*) FROM posicoes WHERE estrategia=? AND status='aberta'",(estr,)).fetchone()[0]
    if count>=cfg['max_posicoes']:
        return False
    return True


def _comprometer_entrada(c,estr,symbol,tf,cfg,signal,ts):
    """Grava a posicao no banco (budget + circuit breaker + insert).
    Chamada so depois que a elegibilidade ja foi confirmada."""
    entry,stop=signal['entrada'],signal['sl']
    distance=entry-stop if signal['direcao']=='compra' else stop-entry
    spread=_spread(symbol)
    if distance<=0 or spread/distance>cfg['spread_stop_max']:
        return []
    budget=round(_banca(c,estr)*cfg['risco_pct']/100,2)
    blocked,reason=_circuit_breaker(c,estr,cfg,novo_risco=budget)
    if blocked:
        return [dict(evento='bloqueado',estrategia=estr,nome=cfg['nome'],symbol=symbol,tf=tf,motivo=reason,banca=_banca(c,estr))]
    cost=spread*(1+cfg['slippage_spreads'])
    size=size_position(entry,stop,budget,cost)
    target=entry+cfg['rr']*distance if signal['direcao']=='compra' else entry-cfg['rr']*distance
    c.execute('''INSERT INTO posicoes
      (estrategia,aberta_em,ts_candle,symbol,tf,direcao,entrada,sl,sl_original,tp,risco_dinheiro,tamanho,
       ultimo_candle,custo,config_entrada,versao) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
      (estr,agora().isoformat(),ts,symbol,tf,signal['direcao'],entry,stop,stop,target,budget,size,ts,cost,json.dumps(cfg),VERSION))
    return [dict(evento='abriu',estrategia=estr,nome=cfg['nome'],symbol=symbol,tf=tf,direcao=signal['direcao'],entrada=entry,sl=stop,tp=target,risco=budget)]


def candidato_torneio(symbol,tf,aval,df,hora_brt):
    """Scanner multi-par da Estrategia A (Didi): avalia se este par/candle
    tem um sinal valido, SEM abrir nada. So calcula e devolve a nota de
    qualidade (score_sinal) pra comparar contra os outros pares do mesmo
    ciclo. Quem chama (main.py) junta os candidatos de todos os instrumentos
    e so confirma (comprometer_torneio) o de maior nota — assim o robo entra
    so no padrao mais claro entre varios pares, nao no primeiro que aparecer."""
    if df is None or not len(df):
        return None
    c=_conn()
    try:
        cfg=get_config('A',c)
        ts=str(df.datetime.iloc[-1])
        if not _elegivel_para_entrada(c,'A',symbol,tf,cfg,hora_brt,ts):
            return None
        signal=entry_signal(cfg,aval,df)
        if not signal:
            return None
        return dict(estrategia='A',symbol=symbol,tf=tf,ts=ts,hora_brt=hora_brt,
                    signal=signal,score=score_sinal(aval))
    finally:
        c.close()


def comprometer_torneio(candidato):
    """Abre o candidato vencedor do torneio. Reavalia a elegibilidade na
    hora (o estado pode ter mudado entre a coleta e a decisao do ciclo)."""
    c=_conn()
    try:
        cfg=get_config('A',c)
        symbol,tf,ts,hora_brt=candidato['symbol'],candidato['tf'],candidato['ts'],candidato['hora_brt']
        c.execute('BEGIN IMMEDIATE')
        try:
            if not _elegivel_para_entrada(c,'A',symbol,tf,cfg,hora_brt,ts):
                c.commit()
                return []
            events=_comprometer_entrada(c,'A',symbol,tf,cfg,candidato['signal'],ts)
            c.commit()
            return events
        except Exception:
            c.rollback()
            raise
    finally:
        c.close()


def _processar_estrategia(c,estr,symbol,tf,aval,df,hora_brt,permitir_entradas=True):
    cfg=get_config(estr,c)
    events=[]
    for row in c.execute("SELECT * FROM posicoes WHERE status='aberta' AND estrategia=? AND symbol=? AND tf=?",(estr,symbol,tf)).fetchall():
        p=dict(row)
        cursor=p['ultimo_candle'] or p['ts_candle']
        legacy=p['versao'] is None
        if legacy:
            p['sl']=p['sl_original'] if p['sl_original'] is not None else p['sl']
            p['sl_original']=p['sl']
            p['breakeven']=0
            cursor=p['ts_candle']
        new=df[df.datetime>pd.Timestamp(cursor)]
        if new.empty:
            continue
        if pd.Timestamp(cursor)<df.datetime.iloc[0]-pd.Timedelta(seconds=INTERVAL_SECONDS[tf]):
            c.execute('UPDATE posicoes SET erro_dados=? WHERE id=?',('Historico insuficiente; recuperar candles antes de resolver',p['id']))
            events.append(dict(evento='erro_dados',estrategia=estr,symbol=symbol,tf=tf))
            continue
        stored=json.loads(p['config_entrada']) if p['config_entrada'] else cfg
        cost=p['custo'] if p['custo'] is not None else _spread(symbol)
        exit_event=None
        for _,bar in new.iterrows():
            p,exit_event=advance(p,bar,stored['breakeven'])
            cursor=str(bar.datetime)
            if exit_event:
                break
        c.execute('UPDATE posicoes SET sl=?,breakeven=?,ultimo_candle=?,versao=?,erro_dados=NULL WHERE id=?',
                  (p['sl'],p['breakeven'],cursor,'legacy-replayed-v2' if legacy else p['versao'],p['id']))
        if exit_event:
            status,price=exit_event
            value,rr=pnl(p,price,cost)
            value=round(value,2)
            bank=round(_banca(c,estr)+value,2)
            c.execute('''UPDATE posicoes SET status=?,fechada_em=?,preco_saida=?,resultado=?,resultado_r=?,candle_saida=? WHERE id=?''',
                      (status,agora().isoformat(),price,value,round(rr,4),cursor,p['id']))
            c.execute('UPDATE contas SET banca=? WHERE estrategia=?',(bank,estr))
            c.execute('INSERT INTO equity (estrategia,ts,banca) VALUES (?,?,?)',(estr,int(agora().timestamp()),bank))
            events.append(dict(evento='fechou',estrategia=estr,nome=cfg['nome'],symbol=symbol,tf=tf,
                               direcao=p['direcao'],status=status,resultado=value,resultado_r=round(rr,4),saida=price,banca=bank))
    ts=str(df.datetime.iloc[-1])
    previous=c.execute('SELECT ts FROM ciclos WHERE estrategia=? AND symbol=? AND tf=?',(estr,symbol,tf)).fetchone()
    fresh=not previous or pd.Timestamp(previous[0])<pd.Timestamp(ts)
    if fresh:
        c.execute('INSERT OR REPLACE INTO ciclos VALUES (?,?,?,?)',(estr,symbol,tf,ts))
    if not (fresh and permitir_entradas):
        return events
    # Estrategia A (Didi) NAO abre aqui: o scanner multi-par (candidato_torneio/
    # comprometer_torneio, chamado pelo main.py depois de rodar todos os
    # instrumentos do ciclo) decide qual par tem o sinal mais claro e so abre
    # nele. As outras estrategias continuam com entrada direta, por par.
    if cfg.get('tipo')=='didi':
        return events
    if not _elegivel_para_entrada(c,estr,symbol,tf,cfg,hora_brt,ts):
        return events
    signal=_sinal_entrada(estr,cfg,aval,df)
    if not signal:
        return events
    events+=_comprometer_entrada(c,estr,symbol,tf,cfg,signal,ts)
    return events


def processar(symbol,tf,aval,df,hora_brt,permitir_entradas=True):
    df=validate_candles(df)
    c=_conn()
    events=[]
    try:
        for estr in ESTRATEGIAS:
            try:
                c.execute('BEGIN IMMEDIATE')
                current=_processar_estrategia(c,estr,symbol,tf,aval,df,hora_brt,permitir_entradas)
                c.commit()
                events.extend(current)
            except Exception:
                c.rollback()
                log.exception('Falha paper %s %s %s',estr,symbol,tf)
                raise
    finally:
        c.close()
    return events


def _estado_estrategia(c,estr):
    cfg=get_config(estr,c)
    bank,start,realised,exposure,dd=_risk_state(c,estr)
    opens=[dict(r) for r in c.execute("SELECT symbol,tf,direcao,entrada,sl,tp,risco_dinheiro AS risco,ts_candle,breakeven,erro_dados,versao FROM posicoes WHERE estrategia=? AND status='aberta' ORDER BY id DESC",(estr,))]
    closed=[dict(r) for r in c.execute("SELECT symbol,tf,direcao,entrada,preco_saida AS saida,resultado,resultado_r,status,fechada_em,ts_candle,versao FROM posicoes WHERE estrategia=? AND status!='aberta' ORDER BY id DESC LIMIT 100",(estr,))]
    n,wins,gains,losses=c.execute("SELECT COUNT(*), COALESCE(SUM(resultado>0),0), COALESCE(SUM(CASE WHEN resultado>0 THEN resultado ELSE 0 END),0), COALESCE(SUM(CASE WHEN resultado<0 THEN -resultado ELSE 0 END),0) FROM posicoes WHERE estrategia=? AND status!='aberta'",(estr,)).fetchone()
    equity=[dict(time=r[0],value=r[1]) for r in c.execute('SELECT ts,banca FROM equity WHERE estrategia=? ORDER BY id',(estr,))]
    blocked,reason=_circuit_breaker(c,estr,cfg)
    count,_=_stats_dia(c,estr)
    return dict(estrategia=estr,nome=cfg['nome'],tipo=cfg['tipo'],config=cfg,
                banca_inicial=BANCA_INICIAL,banca=round(bank,2),retorno_pct=round((bank/BANCA_INICIAL-1)*100,2),
                abertas=opens,n_abertas=len(opens),fechadas=closed,total_operacoes=n,vitorias=wins,
                taxa_acerto=round(wins/n*100,1) if n else 0,fator_lucro=round(gains/losses,3) if losses else None,
                equity=equity or [dict(time=int(agora().timestamp()),value=BANCA_INICIAL)],
                versao_motor=VERSION,historico_limite=100,risco_aberto=round(exposure,2),drawdown_pct=round(dd,2),
                circuit_breaker=dict(ativo=cfg['cb_ativo'],trades_hoje=count,max_trades_dia=cfg['max_trades_dia'],resultado_hoje=round(realised,2),
                    perda_max_dia_pct=cfg['perda_max_dia_pct'],bloqueado=blocked,motivo=reason,escopo='estrategia',banca_inicio_dia=start))


def estado():
    c=_conn()
    try:
        states={e:_estado_estrategia(c,e) for e in ESTRATEGIAS}
        return {**states['A'],'estrategias':states}
    finally:
        c.close()
