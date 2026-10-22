import warnings; warnings.filterwarnings('ignore')
import os, sys, pickle, glob
sys.path.insert(0, os.environ['REPO'])
import pandas as pd
from backend.backtest import rodar_backtest_tendencia

SP = os.path.dirname(__file__)
cache = pickle.load(open(os.path.join(SP, 'hist_cache.pkl'), 'rb'))
SF = {'XAU/USD': 0.00015, 'BTC/USD': 0.00030, 'ETH/USD': 0.00040}    # custo = fracao do preco da epoca
U = ['XAU/USD', 'BTC/USD', 'ETH/USD']
mult = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
print(f'custo x{mult}  | risco 1%/posicao, stop 3xATR movel, entrada >=4 de 5 sinais, saida na virada da maioria\n')
res = {}
for s in U:
    df = cache[(s, '1day')].copy()
    r = rodar_backtest_tendencia(s, df, spread_frac=SF[s], custo_multiplicador=mult)
    res[s] = r
    print(f"{s}: {r['periodo']} | ret total {r['retorno_pct']:7.1f}% ({r['retorno_anual_pct']:5.1f}%/ano) | trades {r['trades']:3d} ({r['trades_por_ano']}/ano) "
          f"acerto {r['taxa_acerto']}% PF {r['fator_lucro']} | DD {r['drawdown_max_pct']}% {'(PAROU POR DD)' if r['parou_por_drawdown'] else ''}")
    anos = r['por_ano']
    print('   por ano %:', {k: v for k, v in anos.items()})

# carteira (3 ativos, cada um com banca propria de 10k, soma dos retornos /3 = carteira de 10k dividida igualmente)
print('\nCARTEIRA (1/3 da banca em cada ativo, risco 1% da fatia):')
tot = {}
for s in U:
    for a, v in res[s]['por_ano'].items():
        tot.setdefault(a, []).append(v)
print({a: round(sum(v) / 3, 2) for a, v in sorted(tot.items())})
# teste cego 2020+
cego = {a: sum(v) / 3 for a, v in tot.items() if a >= 2020}
print('soma simples 2020-2026 (teste cego), %:', round(sum(cego.values()), 1), '| anos negativos desde 2020:', [a for a, v in cego.items() if v < 0])
tr = [t for s in U for t in res[s]['todos_trades']]
w = [t for t in tr if t['resultado'] > 0]
print(f"trades totais {len(tr)} | acerto {len(w)/len(tr)*100:.1f}% | ganho medio {sum(t['resultado_r'] for t in w)/len(w):.2f}R | perda media {sum(t['resultado_r'] for t in tr if t['resultado']<=0)/(len(tr)-len(w)):.2f}R")
print('motivos de saida:', pd.Series([t['motivo'] for t in tr]).value_counts().to_dict())
