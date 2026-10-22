"""Shared OHLC execution. Stops win ambiguity; breakeven starts next bar.
Gapped stops fill at the worse opening price. Costs are charged once on exit.
"""
import math
import pandas as pd

VERSION = 'paper-v2.1'
INTERVAL_SECONDS = {'5min':300, '15min':900, '30min':1800, '1h':3600, '4h':14400, '1day':86400}


def validate_candles(df):
    required = ['datetime','open','high','low','close']
    if df is None or df.empty or any(k not in df for k in required):
        raise ValueError('Candles ausentes ou incompletos')
    out = df.copy()
    out['datetime'] = pd.to_datetime(out['datetime'], utc=True).dt.tz_localize(None)
    if out['datetime'].isna().any() or out['datetime'].duplicated().any():
        raise ValueError('Timestamp ausente ou duplicado')
    if not out['datetime'].is_monotonic_increasing:
        raise ValueError('Candles fora de ordem')
    prices = out[['open','high','low','close']]
    if not prices.apply(lambda s: s.map(lambda x: math.isfinite(float(x)) and float(x)>0)).all().all():
        raise ValueError('Preco invalido')
    if ((out.high < out[['open','close','low']].max(axis=1)) |
            (out.low > out[['open','close','high']].min(axis=1))).any():
        raise ValueError('OHLC inconsistente')
    return out


def size_position(entry, stop, budget, cost):
    distance = abs(entry-stop)
    if not all(math.isfinite(v) for v in (entry,stop,budget,cost)):
        raise ValueError('Risco nao finito')
    if distance<=0 or budget<=0 or cost<0:
        raise ValueError('Risco invalido')
    return budget/(distance+cost)


def advance(position, bar, breakeven=True):
    p = dict(position)
    long = p['direcao']=='compra'
    op,hi,lo = (float(bar[k]) for k in ('open','high','low'))
    hit_stop = lo<=p['sl'] if long else hi>=p['sl']
    trail = float(p.get('trail') or 0)
    if hit_stop:
        price = min(op,p['sl']) if long else max(op,p['sl'])
        if p.get('breakeven'):
            status = 'breakeven'
        elif trail>0 and p['sl']!=p.get('sl_original',p['sl']):
            status = 'trail'          # stop movel que ja subiu (ou desceu) com o preco
        else:
            status = 'stop'
        return p, (status,price)
    if (hi>=p['tp'] if long else lo<=p['tp']):
        return p, ('alvo',p['tp'])
    if trail>0:
        # Stop movel (chandelier): acompanha a maxima/minima, so no sentido favoravel,
        # e vale a partir da barra seguinte (mesma regra conservadora do breakeven).
        p['sl'] = max(p['sl'],hi-trail) if long else min(p['sl'],lo+trail)
    distance = abs(p['entrada']-p['sl_original'])
    if breakeven and not p.get('breakeven') and distance>0:
        hit = hi>=p['entrada']+distance if long else lo<=p['entrada']-distance
        if hit:
            p['sl'],p['breakeven'] = p['entrada'],1
    return p,None


def pnl(position, exit_price, cost):
    move = exit_price-position['entrada']
    if position['direcao']=='venda':
        move = -move
    result = (move-cost)*position['tamanho']
    return result,result/position['risco_dinheiro']
