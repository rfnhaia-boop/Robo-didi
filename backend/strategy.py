"""Closed-bar signals, shared by online paper and offline research."""
import numpy as np
import pandas as pd
from backend.engine import sinal_smc

CRIPTO_24_7 = ('BTC/USD', 'ETH/USD')


def market_open(symbol, timestamp):
    if symbol in CRIPTO_24_7:
        return True
    ts=pd.Timestamp(timestamp)
    ts=ts.tz_localize('UTC') if ts.tzinfo is None else ts.tz_convert('UTC')
    ny=ts.tz_convert('America/New_York')
    return not (ny.weekday()==5 or (ny.weekday()==4 and ny.hour>=17) or (ny.weekday()==6 and ny.hour<17))


# ---------------------------------------------------------------------------
#  Tendencia diaria (estrategia T) — a unica familia com vantagem estatistica
#  encontrada na pesquisa de 18 anos (ouro + BTC + ETH, treino 2008-2019 e
#  teste cego 2020-2026, positiva nos dois). Funcoes puras: o paper ao vivo e o
#  backtest chamam exatamente as mesmas.
# ---------------------------------------------------------------------------
TEND_MIN_BARRAS = 260   # TSMOM de 250 dias + folga


def tendencia_score(df):
    """Nota de tendencia em [-1, 1]: media de 5 sinais independentes, todos sem
    estado (por isso dao o mesmo valor numa janela curta ao vivo e no historico
    inteiro): preco vs SMA100, vs SMA200, retorno de 120d, retorno de 250d e
    preco vs meio do canal de 100 dias. +1 = os 5 apontam alta, -1 = os 5 baixa."""
    c=df['close']
    sinais=[np.sign(c-c.rolling(100).mean()),
            np.sign(c-c.rolling(200).mean()),
            np.sign(c/c.shift(120)-1),
            np.sign(c/c.shift(250)-1),
            np.sign(c-(df['high'].rolling(100).max()+df['low'].rolling(100).min())/2)]
    return sum(s.fillna(0) for s in sinais)/5.0


def tendencia_entrada(cfg, score, close, atr):
    """Sinal de entrada (ou None). Exige >=4 dos 5 sinais na mesma direcao."""
    if not (np.isfinite(score) and np.isfinite(atr) and atr>0 and close>0):
        return None
    k=cfg.get('atr_stop',3.0)
    minimo=cfg.get('entrada_min',0.6)
    if score>=minimo:
        return dict(direcao='compra',entrada=float(close),sl=float(close-k*atr),trail=float(k*atr))
    if score<=-minimo and not cfg.get('somente_compra',False):
        return dict(direcao='venda',entrada=float(close),sl=float(close+k*atr),trail=float(k*atr))
    return None


def tendencia_saida(cfg, direcao, score):
    """True se a tendencia que sustentava a posicao acabou (maioria virou)."""
    if not np.isfinite(score):
        return False
    limite=cfg.get('saida_max',-0.2)
    return score<=limite if direcao=='compra' else score>=-limite


def entry_signal(cfg, aval, df):
    if cfg['tipo']=='tendencia':
        if len(df)<TEND_MIN_BARRAS or 'atr' not in df.columns:
            return None
        return tendencia_entrada(cfg,float(tendencia_score(df).iloc[-1]),float(df['close'].iloc[-1]),float(df['atr'].iloc[-1]))
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
