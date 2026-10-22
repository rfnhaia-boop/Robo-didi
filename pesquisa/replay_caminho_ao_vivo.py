"""Repete N dias de dados reais pelo caminho do robo AO VIVO (paper.processar + SQLite, janela de 600
barras, indicadores recalculados a cada dia) e compara com o backtest. Se divergir, ha bug."""
import warnings; warnings.filterwarnings('ignore')
import os, sys, pickle, tempfile
sys.path.insert(0, os.environ['REPO'])
import pandas as pd
from backend import paper
from backend.engine import montar_indicadores
from backend.backtest import rodar_backtest_tendencia

SP = os.path.dirname(__file__)
cache = pickle.load(open(os.path.join(SP, 'hist_cache.pkl'), 'rb'))
SYM = sys.argv[1] if len(sys.argv) > 1 else 'XAU/USD'
N = int(sys.argv[2]) if len(sys.argv) > 2 else 500

tmp = tempfile.mkdtemp()
paper.DB = os.path.join(tmp, 'replay.db')
# isola um unico ativo pra comparar banca com banca (no backtest cada ativo tem a sua)
paper.set_config('T', {'symbols': [SYM]})

df = cache[(SYM, '1day')].copy().reset_index(drop=True)
k0 = len(df) - N
eventos = []
import datetime as dt
relogio = [None]
paper.agora = lambda: relogio[0]          # cada iteracao = 1 dia de verdade (zera os contadores diarios)
for i in range(k0, len(df)):
    relogio[0] = (df.datetime.iloc[i] + pd.Timedelta(days=1, minutes=5)).to_pydatetime().replace(tzinfo=dt.timezone.utc).astimezone(paper.BRT)
    janela = montar_indicadores(df.iloc[max(0, i - 599): i + 1][['datetime', 'open', 'high', 'low', 'close']].reset_index(drop=True))
    eventos += paper.processar(SYM, '1day', {}, janela, 10, permitir_entradas=True)

c = paper._conn()
fech = c.execute("SELECT resultado, resultado_r, status FROM posicoes WHERE estrategia='T' AND status!='aberta'").fetchall()
aberta = c.execute("SELECT direcao, entrada, sl FROM posicoes WHERE estrategia='T' AND status='aberta'").fetchall()
banca = c.execute("SELECT banca FROM contas WHERE estrategia='T'").fetchone()[0]
c.close()
print(f'LIVE-REPLAY {SYM} ultimos {N} dias: trades fechados {len(fech)} | aberta agora: {[tuple(a) for a in aberta]} | banca {banca:.2f}')
print('   saidas:', pd.Series([f[2] for f in fech]).value_counts().to_dict(), '| eventos abriu:', sum(e['evento'] == 'abriu' for e in eventos))

bt = rodar_backtest_tendencia(SYM, df.iloc[k0 - 260:].reset_index(drop=True), risco_pct=1.0)
print(f"BACKTEST    {SYM}: trades {bt['trades']} | banca final {bt['banca_final']:.2f}")
print('diferenca de banca %:', round((banca / bt['banca_final'] - 1) * 100, 2))
