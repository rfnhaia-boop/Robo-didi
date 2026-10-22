import warnings; warnings.filterwarnings('ignore')
import os, sys, pickle
sys.path.insert(0, os.environ['REPO'])
from backend.backtest import rodar_backtest, prepare
from backend.strategy import market_open
SP = os.path.dirname(__file__)
cache = pickle.load(open(os.path.join(SP, 'hist_cache.pkl'), 'rb'))
print(f"{'ativo':8} {'cfg':22} {'trades':>6} {'acerto':>7} {'PF':>5} {'ret%':>6} {'dd%':>5}")
tot = {}
for s in ['EUR/USD', 'GBP/USD', 'XAU/USD', 'BTC/USD']:
    df = cache[(s, '1h')].copy()
    df = df[[market_open(s, t) for t in df.datetime]].reset_index(drop=True)
    prep = None
    for nome, kw in [('est3 janela 8-13h', dict(hora_ini=8, hora_fim=13)), ('est3 sem janela', dict(hora_ini=None, hora_fim=None))]:
        r = rodar_backtest(s, '1h', estagio_min=3, rr=3.0, risco_pct=0.25, breakeven=True, df_pronto=df, prepared=prep, **kw)
        if prep is None:
            from backend.backtest import prepare as _p
        pf = r['fator_lucro']
        print(f"{s:8} {nome:22} {r['trades']:6d} {r['taxa_acerto']:6.1f}% {pf if pf is not None else float('nan'):5.2f} {r['retorno_pct']:6.2f} {r['drawdown_max_pct']:5.2f}")
        tot.setdefault(nome, []).append(r['retorno_pct'])
print({k: round(sum(v), 2) for k, v in tot.items()}, ' (soma dos retornos % dos 4 ativos, 2 anos)')
