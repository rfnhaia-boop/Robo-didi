"""Research simulator using the same entry and OHLC exit functions as paper.

Single-instrument research, not a multi-instrument portfolio forecast.
Entry is modelled at signal close plus explicit costs; not a broker fill.
"""
import pandas as pd
from backend.engine import buscar_candles, montar_indicadores, avaliar, filtro_contexto, contexto_tendencia
from backend.execution import VERSION, INTERVAL_SECONDS, validate_candles, advance, size_position, pnl
from backend.strategy import entry_signal, market_open
from backend.config import SPREADS


def prepare(df,interval):
    raw=validate_candles(df)
    # Never treat out-of-session vendor bars as actual FX executions.
    ind=montar_indicadores(raw[['datetime','open','high','low','close']])
    context=raw.set_index('datetime').resample('1h',label='left',closed='left').agg(
        {'open':'first','high':'max','low':'min','close':'last'})
    counts=raw.set_index('datetime').close.resample('1h').count()
    expected=max(1,3600//INTERVAL_SECONDS[interval])
    context=context[counts>=expected].dropna().reset_index()
    ctx=montar_indicadores(context)
    signals=[]
    for i in range(len(ind)):
        if i<60:
            signals.append({})
            continue
        prefix=ind.iloc[:i+1]
        end=ind.datetime.iloc[i]+pd.Timedelta(seconds=INTERVAL_SECONDS[interval])
        eligible=ctx[ctx.datetime+pd.Timedelta(hours=1)<=end]
        av=avaliar(prefix)
        av=filtro_contexto(av,contexto_tendencia(eligible))
        signals.append(av)
    return ind,signals


def rodar_backtest(symbol,interval,estagio_min=3,rr=3.0,risco_pct=0.25,
                  hora_ini=8,hora_fim=13,usar_adx=True,outputsize=5000,
                  breakeven=True,df_pronto=None,spread_preco=None,
                  tipo='didi',prepared=None,start_index=60,end_index=None,
                  custo_multiplicador=1.0):
    if symbol not in SPREADS or interval not in INTERVAL_SECONDS:
        raise ValueError('Ativo ou timeframe sem custos configurados')
    if not 0.01<=risco_pct<=2 or not 0.5<=rr<=10 or custo_multiplicador<1:
        raise ValueError('Parametros de risco invalidos')
    if not usar_adx:
        raise ValueError('Filtro ADX obrigatorio para comparabilidade do Didi')
    spread=SPREADS[symbol] if spread_preco is None else spread_preco
    cost=spread*1.5*custo_multiplicador
    if prepared is None:
        df=df_pronto if df_pronto is not None else buscar_candles(symbol,interval,outputsize)
        df=validate_candles(df)
        df=df[[market_open(symbol,t) for t in df.datetime]].reset_index(drop=True)
        ind,signals=prepare(df,interval)
    else:
        ind,signals=prepared
    end_index=len(ind) if end_index is None else min(end_index,len(ind))
    if end_index-start_index<2:
        raise ValueError('Historico insuficiente depois do aquecimento')
    cfg=dict(tipo=tipo,estagio_min=estagio_min,lookback=10)
    bank=peak=10000.0
    equity=[dict(time=int(ind.datetime.iloc[start_index].timestamp()),value=bank)]
    trades=[]
    position=None
    day=None
    day_start=bank
    daily_trades=0
    halted=False
    def close(bar,price,status):
        nonlocal bank,position,peak,halted
        result,r=pnl(position,price,cost)
        bank=round(bank+round(result,2),2)
        peak=max(peak,bank)
        halted=halted or (peak-bank)/peak>=0.10
        trades.append(dict(data=str(bar.datetime),dir=position['direcao'],entrada=position['entrada'],
                           saida=price,resultado=round(result,2),resultado_r=round(r,4),motivo=status,
                           estagio=position.get('estagio',0)))
        equity.append(dict(time=int(bar.datetime.timestamp()),value=bank))
        position=None
    for i in range(max(60,start_index),end_index):
        bar=ind.iloc[i]
        end=bar.datetime+pd.Timedelta(seconds=INTERVAL_SECONDS[interval])
        local=end-pd.Timedelta(hours=3)
        if local.date()!=day:
            day=local.date()
            day_start=bank
            daily_trades=0
        if position is not None:
            position,event=advance(position,bar,breakeven)
            if event:
                close(bar,event[1],event[0])
        if position or halted or not market_open(symbol,end):
            continue
        if hora_ini is not None and hora_fim is not None and not hora_ini<=local.hour<hora_fim:
            continue
        budget=round(bank*risco_pct/100,2)
        if daily_trades>=3 or day_start-bank+budget>=day_start*0.01-1e-8 or budget>bank*0.01:
            continue
        signal=entry_signal(cfg,signals[i],ind.iloc[:i+1])
        if not signal:
            continue
        distance=signal['entrada']-signal['sl'] if signal['direcao']=='compra' else signal['sl']-signal['entrada']
        if distance<=0 or spread/distance>0.20:
            continue
        position={**signal,'sl_original':signal['sl'],'breakeven':0,'risco_dinheiro':budget,
                  'tamanho':size_position(signal['entrada'],signal['sl'],budget,cost),
                  'tp':signal['entrada']+(1 if signal['direcao']=='compra' else -1)*rr*distance,
                  'estagio':signals[i].get('estagio',0)}
        daily_trades+=1
    if position:
        close(ind.iloc[end_index-1],float(ind.close.iloc[end_index-1]),'fim do periodo')
    wins=[t['resultado'] for t in trades if t['resultado']>0]
    losses=[t['resultado'] for t in trades if t['resultado']<0]
    high=10000.0
    dd=0
    for point in equity:
        high=max(high,point['value'])
        dd=max(dd,(high-point['value'])/high*100)
    monthly={}
    for t in trades:
        key=t['data'][:7]
        m=monthly.setdefault(key,dict(mes=key,trades=0,vitorias=0,resultado=0))
        m['trades']+=1
        m['vitorias']+=int(t['resultado']>0)
        m['resultado']=round(m['resultado']+t['resultado'],2)
    return dict(symbol=symbol,interval=interval,versao_motor=VERSION,
                periodo=f'{ind.datetime.iloc[start_index]} ate {ind.datetime.iloc[end_index-1]}',candles=end_index-start_index,
                parametros=dict(tipo=tipo,estagio_min=estagio_min,rr=rr,risco_pct=risco_pct,hora_ini=hora_ini,hora_fim=hora_fim,breakeven=breakeven),
                banca_inicial=10000,banca_final=bank,retorno_pct=round((bank/10000-1)*100,2),
                trades=len(trades),vitorias=len(wins),derrotas=len(losses),taxa_acerto=round(len(wins)/len(trades)*100,1) if trades else 0,
                fator_lucro=round(sum(wins)/abs(sum(losses)),3) if losses else None,drawdown_max_pct=round(dd,2),
                spread_usado=spread,custo_total_por_unidade=cost,mensal=list(monthly.values()),equity=equity,lista_trades=trades[-100:],
                todos_trades=trades,parou_por_drawdown=halted,
                limitacoes=['Simulacao de fechamento, nao execucao em corretora','Custos estimados fixos; sem swap/funding',
                            'Teste por ativo; nao reproduz concorrencia entre ativos da carteira','DD realizado, nao intrabar'])
