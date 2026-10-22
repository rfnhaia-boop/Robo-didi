"""Pesquisa de estrategias em barras DIARIAS (18 anos), com custos e split treino/teste cego.
Nada aqui otimiza em cima do periodo de teste: parametros sao grade pequena e fixa."""
import os, sys, pickle, math, itertools
import numpy as np, pandas as pd
sys.path.insert(0, os.environ['REPO'])
from backend.engine import montar_indicadores

SP = os.path.dirname(__file__)
cache = pickle.load(open(os.path.join(SP, 'hist_cache.pkl'), 'rb'))
CORTE = pd.Timestamp('2020-01-01')          # <2020 = treino/IS ; >=2020 = teste cego/OOS

# custo de ida-e-volta como fracao do preco (spread real de varejo x1.5 de folga, igual ao projeto)
SPREAD_FRAC = {'EUR/USD': 0.00010, 'GBP/USD': 0.00012, 'USD/JPY': 0.00010, 'AUD/USD': 0.00014,
               'USD/CAD': 0.00014, 'USD/CHF': 0.00014, 'NZD/USD': 0.00018, 'EUR/JPY': 0.00016,
               'GBP/JPY': 0.00020, 'EUR/GBP': 0.00014, 'AUD/JPY': 0.00020,
               'XAU/USD': 0.00015, 'BTC/USD': 0.00030, 'ETH/USD': 0.00040}
CUSTO_X = 1.5

SYMS = [s for (s, i), v in cache.items() if i == '1day' and v is not None]


def dados(sym):
    d = cache[(sym, '1day')].copy().set_index('datetime')
    d = d[~d.index.duplicated()].sort_index()
    return d


# ---------------------------------------------------------------- sinais (posicao desejada no fechamento de t)
def sig_donchian(d, n_in, n_out):
    up = d.high.rolling(n_in).max().shift(1); dn = d.low.rolling(n_in).min().shift(1)
    xl = d.low.rolling(n_out).min().shift(1); xs = d.high.rolling(n_out).max().shift(1)
    pos = np.zeros(len(d)); p = 0
    c = d.close.values
    for i in range(len(d)):
        if p == 0:
            if c[i] > up.iloc[i]: p = 1
            elif c[i] < dn.iloc[i]: p = -1
        elif p == 1 and c[i] < xl.iloc[i]:
            p = -1 if c[i] < dn.iloc[i] else 0
        elif p == -1 and c[i] > xs.iloc[i]:
            p = 1 if c[i] > up.iloc[i] else 0
        pos[i] = p
    return pd.Series(pos, d.index)


def sig_sma(d, n):
    return np.sign(d.close - d.close.rolling(n).mean()).fillna(0)


def sig_tsmom(d, L):
    return np.sign(d.close / d.close.shift(L) - 1).fillna(0)


def didi_flags(d):
    ind = montar_indicadores(d.reset_index()[['datetime', 'open', 'high', 'low', 'close']]).set_index('datetime')
    cu, lo = ind.didi_curta, ind.didi_longa
    cruz_c = (cu.shift(2) <= lo.shift(2)) & (cu.shift(1) > lo.shift(1)) & (cu > lo)
    cruz_v = (cu.shift(2) >= lo.shift(2)) & (cu.shift(1) < lo.shift(1)) & (cu < lo)
    trix_c = (ind.trix > ind.trix_sinal) & (ind.trix > ind.trix.shift(1)); trix_v = (ind.trix < ind.trix_sinal) & (ind.trix < ind.trix.shift(1))
    est_c = (ind.estoc_k > ind.estoc_d) & (ind.estoc_k < 80); est_v = (ind.estoc_k < ind.estoc_d) & (ind.estoc_k > 20)
    expd = ind.bb_bw > ind.bb_bw.shift(1)
    adx_f = (ind.adx > 25) & (ind.adx > ind.adx.shift(1))
    di_c = ind.di_plus > ind.di_minus; di_v = ind.di_minus > ind.di_plus
    conf_c = trix_c.astype(int) + est_c + expd + adx_f + di_c
    conf_v = trix_v.astype(int) + est_v + expd + adx_f + di_v
    ok_adx = ind.adx >= 20
    return ind, (cruz_c & (conf_c >= 3) & di_c & ok_adx), (cruz_v & (conf_v >= 3) & di_v & ok_adx)


def sig_didi(d, k_trail):
    """Didi estagio 3 (cruzamento confirmado + confluencia>=3 + DI + ADX>=20), saida por stop de ATR movel."""
    ind, buy, sell = didi_flags(d)
    atr = ind.atr.values; c = ind.close.values; h = ind.high.values; l = ind.low.values
    pos = np.zeros(len(d)); p = 0; ext = 0.0
    for i in range(len(d)):
        if p == 1:
            ext = max(ext, h[i])
            if c[i] < ext - k_trail * atr[i]: p = 0
        elif p == -1:
            ext = min(ext, l[i])
            if c[i] > ext + k_trail * atr[i]: p = 0
        if p == 0 or (p == 1 and sell.iloc[i]) or (p == -1 and buy.iloc[i]):
            if buy.iloc[i]: p, ext = 1, h[i]
            elif sell.iloc[i]: p, ext = -1, l[i]
        pos[i] = p
    return pd.Series(pos, d.index)


# ---------------------------------------------------------------- simulacao com vol-target e custo
def simular(d, sinal, sym, vol_alvo=0.10, lev_max=3.0):
    ret = d.close.pct_change()
    bars_ano = 365.0 if sym in ('BTC/USD', 'ETH/USD') else 252.0
    vol = ret.ewm(span=30, min_periods=30).std().shift(1) * math.sqrt(bars_ano)
    lev = (vol_alvo / vol).clip(upper=lev_max)
    expo = sinal.shift(1) * lev                       # exposicao mantida durante a barra t
    bruto = expo * ret
    custo = expo.diff().abs() * SPREAD_FRAC[sym] * CUSTO_X
    liq = (bruto - custo.fillna(0)).dropna()
    return liq, expo


def stats(r, ano=252.0):
    r = r.dropna()
    if len(r) < 50 or r.std() == 0:
        return dict(sharpe=np.nan, ret=np.nan, dd=np.nan, n=len(r))
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    return dict(sharpe=r.mean() / r.std() * math.sqrt(ano), ret=r.mean() * ano, dd=dd, n=len(r))


def portfolio(series_dict):
    df = pd.concat(series_dict, axis=1)
    return df.mean(axis=1, skipna=True), df


def rodar(nome, fabrica, syms=None, imprimir=True):
    syms = syms or SYMS
    ser = {}
    for s in syms:
        d = dados(s)
        liq, _ = simular(d, fabrica(d), s)
        ser[s] = liq
    port, df = portfolio(ser)
    is_, oos = port[port.index < CORTE], port[port.index >= CORTE]
    a, b = stats(is_), stats(oos)
    pos_syms = sum(1 for s in syms if stats(ser[s][ser[s].index >= CORTE])['sharpe'] > 0)
    if imprimir:
        print(f"{nome:28} IS sharpe {a['sharpe']:5.2f} ret {a['ret']*100:5.1f}% | OOS sharpe {b['sharpe']:5.2f} ret {b['ret']*100:5.1f}% "
              f"dd {b['dd']*100:6.1f}% | simbolos OOS>0: {pos_syms}/{len(syms)}")
    return port, df, a, b


if __name__ == '__main__':
    print('simbolos:', SYMS, '\n')
    rodar('donchian 20/10', lambda d: sig_donchian(d, 20, 10))
    rodar('donchian 55/20', lambda d: sig_donchian(d, 55, 20))
    rodar('donchian 100/50', lambda d: sig_donchian(d, 100, 50))
    rodar('SMA 100', lambda d: sig_sma(d, 100))
    rodar('SMA 200', lambda d: sig_sma(d, 200))
    rodar('TSMOM 60d', lambda d: sig_tsmom(d, 60))
    rodar('TSMOM 120d', lambda d: sig_tsmom(d, 120))
    rodar('TSMOM 250d', lambda d: sig_tsmom(d, 250))
    rodar('Didi est3 trail 2x', lambda d: sig_didi(d, 2.0))
    rodar('Didi est3 trail 3x', lambda d: sig_didi(d, 3.0))
    rodar('Didi est3 trail 5x', lambda d: sig_didi(d, 5.0))
