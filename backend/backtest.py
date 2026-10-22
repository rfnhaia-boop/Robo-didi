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


def rodar_backtest_tendencia(symbol, df, cfg=None, risco_pct=1.0, spread_frac=None,
                             custo_multiplicador=1.0, drawdown_max_pct=15.0):
    """Backtest da Estrategia T (tendencia diaria) com as MESMAS funcoes do paper ao vivo:
    strategy.tendencia_entrada/saida, execution.advance (stop movel), size_position, pnl.
    Ordem em cada barra (igual ao paper): stop/stop movel -> saida por sinal -> entrada no fechamento.
    spread_frac: custo como fracao do preco (evita distorcer 18 anos de historico com spread absoluto
    de hoje); sem ele usa SPREADS[symbol]. Equity marcada a mercado todo dia (drawdown real)."""
    from backend.strategy import tendencia_score, tendencia_entrada, tendencia_saida, TEND_MIN_BARRAS
    cfg = {'entrada_min': 0.6, 'saida_max': -0.2, 'atr_stop': 3.0, 'somente_compra': False, **(cfg or {})}
    if spread_frac is None and symbol not in SPREADS:
        raise ValueError('Ativo sem custo configurado')
    df = validate_candles(df)
    ind = montar_indicadores(df[['datetime', 'open', 'high', 'low', 'close']])
    score = tendencia_score(ind).values
    bank = peak = 10000.0
    position, halted = None, False
    trades, equity = [], []

    def custo(preco):
        spread = spread_frac * preco if spread_frac is not None else SPREADS[symbol]
        return spread * 1.5 * custo_multiplicador

    def fechar(bar, price, status):
        nonlocal bank, position, peak, halted
        result, r = pnl(position, price, position['custo'])
        bank = round(bank + round(result, 2), 2)
        peak = max(peak, bank)
        halted = halted or (peak - bank) / peak * 100 >= drawdown_max_pct
        trades.append(dict(entrada_em=position['entrada_em'], data=str(bar.datetime), dir=position['direcao'],
                           entrada=position['entrada'], saida=price, resultado=round(result, 2),
                           resultado_r=round(r, 4), motivo=status))
        position = None

    for i in range(TEND_MIN_BARRAS, len(ind)):
        bar = ind.iloc[i]
        if position is not None:
            position, event = advance(position, bar, False)
            if event:
                fechar(bar, event[1], event[0])
        if position is not None and tendencia_saida(cfg, position['direcao'], score[i]):
            fechar(bar, float(bar.close), 'sinal')
        if position is None and not halted:
            sig = tendencia_entrada(cfg, float(score[i]), float(bar.close), float(bar.atr))
            if sig:
                budget = round(bank * risco_pct / 100, 2)
                c = custo(sig['entrada'])
                dist = abs(sig['entrada'] - sig['sl'])
                if budget > 0 and dist > 0:
                    position = {**sig, 'sl_original': sig['sl'], 'breakeven': 0, 'risco_dinheiro': budget, 'custo': c,
                                'tamanho': size_position(sig['entrada'], sig['sl'], budget, c),
                                'tp': sig['entrada'] + (1 if sig['direcao'] == 'compra' else -1) * 10 * dist,
                                'entrada_em': str(bar.datetime)}
        mtm = bank + (pnl(position, float(bar.close), position['custo'])[0] if position else 0.0)
        equity.append((bar.datetime, mtm))
    if position:
        fechar(ind.iloc[-1], float(ind.close.iloc[-1]), 'fim do periodo')
    eq = pd.Series([v for _, v in equity], index=[d for d, _ in equity])
    dd = ((eq / eq.cummax() - 1).min() * 100) if len(eq) else 0.0
    wins = [t['resultado'] for t in trades if t['resultado'] > 0]
    losses = [t['resultado'] for t in trades if t['resultado'] < 0]
    por_ano = {}
    if len(eq):
        fim_ano = eq.groupby(eq.index.year).last()
        ini = 10000.0
        for ano, v in fim_ano.items():
            por_ano[int(ano)] = round((v / ini - 1) * 100, 2)
            ini = v
    anos = max((ind.datetime.iloc[-1] - ind.datetime.iloc[TEND_MIN_BARRAS]).days / 365.25, 0.01)
    return dict(symbol=symbol, periodo=f'{ind.datetime.iloc[TEND_MIN_BARRAS].date()} ate {ind.datetime.iloc[-1].date()}',
                parametros=dict(cfg=cfg, risco_pct=risco_pct), banca_final=round(bank, 2),
                retorno_pct=round((bank / 10000 - 1) * 100, 2),
                retorno_anual_pct=round(((bank / 10000) ** (1 / anos) - 1) * 100, 2),
                trades=len(trades), trades_por_ano=round(len(trades) / anos, 1),
                taxa_acerto=round(len(wins) / len(trades) * 100, 1) if trades else 0,
                fator_lucro=round(sum(wins) / abs(sum(losses)), 3) if losses else None,
                drawdown_max_pct=round(abs(dd), 2), por_ano=por_ano, parou_por_drawdown=halted,
                todos_trades=trades)
