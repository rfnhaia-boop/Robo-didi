import warnings; warnings.filterwarnings('ignore')
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from pesq_diaria import *

pd.set_option('display.width', 220)

# --- 1) por simbolo: Sharpe IS / OOS de 3 sistemas simples
cands = {'SMA200': lambda d: sig_sma(d, 200), 'TSMOM250': lambda d: sig_tsmom(d, 250), 'Donch100': lambda d: sig_donchian(d, 100, 50),
         'Didi3x': lambda d: sig_didi(d, 3.0)}
print('=== Sharpe por simbolo (IS<2020 | OOS>=2020) ===')
linhas = []
for s in SYMS:
    d = dados(s); lin = {'sym': s, 'ini': d.index[0].year}
    for n, f in cands.items():
        liq, _ = simular(d, f(d), s)
        a, b = stats(liq[liq.index < CORTE]), stats(liq[liq.index >= CORTE])
        lin[n + '_IS'] = round(a['sharpe'], 2) if a['sharpe'] == a['sharpe'] else None
        lin[n + '_OOS'] = round(b['sharpe'], 2)
    linhas.append(lin)
print(pd.DataFrame(linhas).set_index('sym').to_string())

# --- 2) ensemble de tendencia (media das posicoes) + variantes
def ens(d):
    return (sig_sma(d, 100) + sig_sma(d, 200) + sig_tsmom(d, 120) + sig_tsmom(d, 250) + sig_donchian(d, 100, 50)) / 5.0

def ens_long(d):
    return ens(d).clip(lower=0)

print('\n=== Ensemble de tendencia (5 sinais) ===')
rodar('ensemble L/S', ens)
rodar('ensemble so-comprado', ens_long)
fx = [s for s in SYMS if s not in ('XAU/USD', 'BTC/USD', 'ETH/USD')]
print('--- so FX (sem ouro/cripto) ---')
rodar('ensemble L/S  FX', ens, fx)
print('--- so ouro+cripto ---')
rodar('ensemble L/S  XAU+cripto', ens, ['XAU/USD', 'BTC/USD', 'ETH/USD'])
rodar('ensemble so-comprado XAU+cripto', ens_long, ['XAU/USD', 'BTC/USD', 'ETH/USD'])

# --- 3) por ano do ensemble L/S (consistencia)
port, df, a, b = rodar('x', ens, imprimir=False)
anual = port.groupby(port.index.year).apply(lambda r: (1 + r).prod() - 1)
print('\nretorno por ano do ensemble L/S (vol-alvo 10%/ativo):')
print((anual * 100).round(1).to_string())
