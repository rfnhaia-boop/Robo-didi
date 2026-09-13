"""Closed-bar signals, shared by online paper and offline research."""
import pandas as pd
from backend.engine import sinal_smc


def market_open(symbol, timestamp):
    if symbol=='BTC/USD':
        return True
    ts=pd.Timestamp(timestamp)
    ts=ts.tz_localize('UTC') if ts.tzinfo is None else ts.tz_convert('UTC')
    ny=ts.tz_convert('America/New_York')
    return not (ny.weekday()==5 or (ny.weekday()==4 and ny.hour>=17) or (ny.weekday()==6 and ny.hour<17))


def entry_signal(cfg, aval, df):
    if cfg['tipo']=='smc':
        return sinal_smc(df,cfg.get('lookback',10))
    if cfg['tipo']=='trend':
        # Research challenger only. No claim that a short-term breakout has
        # the same evidence as diversified long-horizon trend following.
        if len(df)<55:
            return None
        c=df.iloc[-1]
        window=df.iloc[-21:-1]
        ma=df.close.rolling(50).mean().iloc[-1]
        atr=float(c['atr'])
        if c.close>window.high.max() and c.close>ma:
            return dict(direcao='compra',entrada=float(c.close),sl=float(c.close-2*atr))
        if c.close<window.low.min() and c.close<ma:
            return dict(direcao='venda',entrada=float(c.close),sl=float(c.close+2*atr))
        return None
    if (aval.get('direcao') not in ('compra','venda') or aval.get('estagio',0)<cfg.get('estagio_min',3)
        or aval.get('contra_tendencia') or not aval.get('contexto_confirmado',False)
        or (aval.get('adx_valor') or 0)<20 or len(df)<6):
        return None
    direction=aval['direcao']
    return dict(direcao=direction,entrada=float(df.close.iloc[-1]),
                sl=float(df.low.iloc[-5:].min() if direction=='compra' else df.high.iloc[-5:].max()))
