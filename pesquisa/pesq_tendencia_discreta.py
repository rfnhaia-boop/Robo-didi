import warnings; warnings.filterwarnings('ignore')
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from pesq_diaria import *
from backend.engine import montar_indicadores


def score_tend(d):
    """5 sinais SEM estado (reprodutiveis em janela curta): SMA100, SMA200, TSMOM120, TSMOM250, meio do canal 100d."""
    c = d.close
    s = [np.sign(c - c.rolling(100).mean()), np.sign(c - c.rolling(200).mean()),
         np.sign(c / c.shift(120) - 1), np.sign(c / c.shift(250) - 1),
         np.sign(c - (d.high.rolling(100).max() + d.low.rolling(100).min()) / 2)]
    return sum(x.fillna(0) for x in s) / 5.0


def sig_tend(d, ent=0.6, sai=-0.2, k=3.0, long_only=False):
    sc = score_tend(d).values
    atr = montar_indicadores(d.reset_index()[['datetime', 'open', 'high', 'low', 'close']]).atr.values
    c, h, l = d.close.values, d.high.values, d.low.values
    pos = np.zeros(len(d)); p = 0; ext = 0.0
    for i in range(len(d)):
        if p == 1:
            ext = max(ext, h[i])
            if sc[i] <= sai or (k and c[i] < ext - k * atr[i]): p = 0
        elif p == -1:
            ext = min(ext, l[i])
            if sc[i] >= -sai or (k and c[i] > ext + k * atr[i]): p = 0
        if p == 0 and i >= 250:
            if sc[i] >= ent: p, ext = 1, h[i]
            elif sc[i] <= -ent and not long_only: p, ext = -1, l[i]
        pos[i] = p
    return pd.Series(pos, d.index)


U = ['XAU/USD', 'BTC/USD', 'ETH/USD']
print('=== universo ouro+BTC+ETH, variantes discretas (vol-alvo 10%/ativo) ===')
for nome, kw in [('L/S  sem trail', dict(k=0)), ('L/S  trail 3x', dict(k=3.0)), ('L/S  trail 5x', dict(k=5.0)),
                 ('LONG sem trail', dict(k=0, long_only=True)), ('LONG trail 3x', dict(k=3.0, long_only=True)),
                 ('LONG trail 5x', dict(k=5.0, long_only=True)),
                 ('L/S trail 3x ent .2', dict(k=3.0, ent=0.2, sai=-0.2)), ('L/S trail 3x ent 1.0', dict(k=3.0, ent=1.0, sai=0.2))]:
    rodar(nome, lambda d, kw=kw: sig_tend(d, **kw), U)

print('\n=== mesma regra (L/S trail 3x) em TODO o universo, so p/ confirmar que FX nao tem edge ===')
rodar('todos 14', lambda d: sig_tend(d, k=3.0))
rodar('so FX', lambda d: sig_tend(d, k=3.0), [s for s in SYMS if s not in U])

print('\n=== por simbolo (L/S trail 3x): IS | OOS | trades/ano ===')
for s in U:
    d = dados(s); sg = sig_tend(d, k=3.0)
    liq, expo = simular(d, sg, s)
    a, b = stats(liq[liq.index < CORTE]), stats(liq[liq.index >= CORTE])
    tr = (sg.diff().abs() > 0).sum() / ((d.index[-1] - d.index[0]).days / 365.0)
    print(f"{s}: IS sharpe {a['sharpe']:.2f} ret {a['ret']*100:.1f}% | OOS sharpe {b['sharpe']:.2f} ret {b['ret']*100:.1f}% dd {b['dd']*100:.1f}% | mudancas de posicao/ano {tr:.1f}")

port, df, a, b = rodar('x', lambda d: sig_tend(d, k=3.0), U, imprimir=False)
print('\nretorno anual do portfolio ouro+BTC+ETH (L/S trail 3x):')
print(((port.groupby(port.index.year).apply(lambda r: (1 + r).prod() - 1)) * 100).round(1).to_string())
