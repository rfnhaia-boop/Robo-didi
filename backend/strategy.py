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
    direction=aval.get('direcao')
    if (direction not in ('compra','venda') or aval.get('estagio',0)<cfg.get('estagio_min',3)
        or aval.get('contra_tendencia') or not aval.get('contexto_confirmado',False)
        or (aval.get('adx_valor') or 0)<20 or len(df)<6
        or aval.get('di_dominante')!=direction):     # so opera a favor de quem manda no DI+/DI-
        return None
    entrada=float(df.close.iloc[-1])
    sl5=float(df.low.iloc[-5:].min() if direction=='compra' else df.high.iloc[-5:].max())
    # piso de stop por ATR: em mercado parado o range de 5 candles fica
    # microscopico e o spread sozinho estoura o stop. Garante distancia
    # minima de 1x ATR pra o risco fazer sentido perto do custo da operacao.
    atr=float(df['atr'].iloc[-1]) if 'atr' in df.columns and pd.notna(df['atr'].iloc[-1]) else 0.0
    piso=atr*1.0
    if direction=='compra':
        sl=min(sl5,entrada-piso) if piso>0 else sl5
    else:
        sl=max(sl5,entrada+piso) if piso>0 else sl5
    return dict(direcao=direction,entrada=entrada,sl=sl)
