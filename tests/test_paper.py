import json
import os
import tempfile
import unittest
from pathlib import Path
import pandas as pd
import sqlite3

from backend import paper


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        paper.DB = str(Path(self.tmp.name) / 'paper.db')

    def ligar_a(self):
        paper.set_config('A', {'ativo': True})   # A/B/C nascem suspensas; os testes do torneio ligam a A

    def tearDown(self):
        self.tmp.cleanup()

    def candles(self, rows):
        d = pd.DataFrame(rows, columns=['datetime','open','high','low','close'])
        d['datetime'] = pd.to_datetime(d.datetime)
        return d

    def test_processar_never_opens_estrategia_a_directly(self):
        self.ligar_a()
        # Estrategia A (Didi) so abre via o torneio multi-par (candidato_torneio
        # + comprometer_torneio), nunca dentro de processar() — isso garante que
        # o scanner sempre compara os candidatos de todos os pares antes de abrir.
        rows=[]
        for ts in pd.date_range('2026-09-09 05:00', periods=60, freq='30min'):
            rows.append((ts,100,102,98,100))
        df=self.candles(rows)
        original=paper._sinal_entrada
        paper._sinal_entrada=lambda *a: {'direcao':'compra','entrada':100,'sl':98}
        try:
            events=paper.processar('EUR/USD','30min',{},df,10)
        finally:
            paper._sinal_entrada=original
        # B suspensa e C exclui EUR/USD -> nada abre; A nunca abre por aqui.
        self.assertEqual(events,[])

    def test_torneio_nao_abre_duas_vezes_no_mesmo_par(self):
        self.ligar_a()
        rows=[]
        for ts in pd.date_range('2026-09-09 05:00', periods=60, freq='30min'):
            rows.append((ts,100,102,98,100))
        df=self.candles(rows)
        aval={'estagio':3,'direcao':'compra'}
        original=paper.entry_signal
        paper.entry_signal=lambda cfg,aval,df: {'direcao':'compra','entrada':100,'sl':98} if cfg.get('tipo')=='didi' else None
        try:
            candidato=paper.candidato_torneio('EUR/USD','30min',aval,df,10)
            self.assertIsNotNone(candidato)
            primeiro=paper.comprometer_torneio(candidato)
            self.assertEqual(sum(e['evento']=='abriu' for e in primeiro),1)
            # com a posicao ja aberta, nao ha mais candidato pro mesmo par
            self.assertIsNone(paper.candidato_torneio('EUR/USD','30min',aval,df,10))
        finally:
            paper.entry_signal=original

    def test_scanner_so_abre_no_candidato_de_maior_nota(self):
        self.ligar_a()
        # Simula o cenario que o Rafael pediu: varios pares com sinal valido
        # no mesmo ciclo, so entra no que tiver a nota mais alta (padrao mais
        # claro / menos chance de erro).
        rows=[]
        for ts in pd.date_range('2026-09-09 05:00', periods=60, freq='30min'):
            rows.append((ts,100,102,98,100))
        df=self.candles(rows)
        candidatos=[
            dict(estrategia='A',symbol='EUR/USD',tf='30min',ts=str(df.datetime.iloc[-1]),hora_brt=10,
                 signal={'direcao':'compra','entrada':100,'sl':98},score=40.0),   # abaixo do minimo (55)
            dict(estrategia='A',symbol='GBP/USD',tf='30min',ts=str(df.datetime.iloc[-1]),hora_brt=10,
                 signal={'direcao':'compra','entrada':100,'sl':98},score=61.0),   # vencedor
            dict(estrategia='A',symbol='XAU/USD',tf='30min',ts=str(df.datetime.iloc[-1]),hora_brt=10,
                 signal={'direcao':'compra','entrada':100,'sl':98},score=58.0),   # perde pro GBP
        ]
        cfg=paper.get_config('A')
        minimo=cfg['nota_minima']
        elegiveis=sorted([c for c in candidatos if c['score']>=minimo],key=lambda c:c['score'],reverse=True)
        self.assertEqual([c['symbol'] for c in elegiveis],['GBP/USD','XAU/USD'])
        vencedor=paper.comprometer_torneio(elegiveis[0])
        self.assertEqual(sum(e['evento']=='abriu' for e in vencedor),1)
        self.assertEqual(vencedor[0]['symbol'],'GBP/USD')
        # XAU nao foi commitado (so o main.py chama comprometer_torneio pro
        # vencedor de cada rodada); confirma que o candidato de EUR (nota
        # baixa) nem entraria na lista de elegiveis.
        self.assertNotIn('EUR/USD',[c['symbol'] for c in elegiveis])

    def test_disabled_strategy_still_resolves_exit(self):
        c=paper._conn()
        cfg=paper.get_config('A',c); cfg['ativo']=False
        c.execute('UPDATE contas SET config=? WHERE estrategia=?',(json.dumps(cfg),'A'))
        c.execute('''INSERT INTO posicoes
          (estrategia,aberta_em,ts_candle,symbol,tf,direcao,entrada,sl,sl_original,tp,
           risco_dinheiro,tamanho,ultimo_candle,custo,config_entrada,versao)
          VALUES ('A','2026-09-10','2026-09-10 10:00','EUR/USD','30min','compra',100,90,90,130,100,10,'2026-09-10 10:00',0,?,'v2')''',(json.dumps(cfg),))
        c.commit(); c.close()
        df=self.candles([('2026-09-10 10:30',100,101,89,90)])
        events=paper.processar('EUR/USD','30min',{},df,11)
        self.assertEqual(events[0]['evento'],'fechou')

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            paper.set_config('A',{'risco_pct':20})

    def test_legacy_schema_is_archived_without_data_loss(self):
        c=sqlite3.connect(paper.DB)
        c.execute('CREATE TABLE posicoes (id INTEGER PRIMARY KEY, resultado REAL)')
        c.execute('CREATE TABLE equity (id INTEGER PRIMARY KEY, banca REAL)')
        c.execute('INSERT INTO posicoes VALUES (1,-10)')
        c.execute('INSERT INTO equity VALUES (1,9990)')
        c.commit(); c.close()
        c=paper._conn()
        self.assertEqual(c.execute('SELECT resultado FROM posicoes_legacy_v1').fetchone()[0],-10)
        self.assertEqual(c.execute('SELECT banca FROM equity_legacy_v1').fetchone()[0],9990)
        self.assertIn('estrategia',{r[1] for r in c.execute('PRAGMA table_info(posicoes)')})
        c.close()

    # ---------------------------------------------------------------- Estrategia T (tendencia diaria)
    def daily(self, n=300, passo=1.0, fim='2026-10-16'):
        import numpy as np
        close = 100 + np.arange(n) * passo
        idx = pd.date_range(end=fim, periods=n, freq='D')
        return pd.DataFrame({'datetime': idx, 'open': close, 'high': close + 1, 'low': close - 1,
                             'close': close, 'atr': 2.0})

    def test_tendencia_opens_on_friday_daily_bar_for_gold(self):
        # barra diaria de sexta: o calendario intradiario nao pode bloquear (so vale pra 30min/1h)
        ev = paper.processar('XAU/USD', '1day', {}, self.daily(), 10)
        abriu = [e for e in ev if e['evento'] == 'abriu']
        self.assertEqual(len(abriu), 1)
        self.assertEqual((abriu[0]['estrategia'], abriu[0]['direcao'], abriu[0]['trail']), ('T', 'compra', 6.0))
        c = paper._conn()
        row = dict(c.execute("SELECT * FROM posicoes WHERE estrategia='T'").fetchone()); c.close()
        self.assertEqual(row['sl'], row['entrada'] - 6.0)
        self.assertGreater(row['tamanho'], 0)
        # risco = 1% da banca, e a mesma barra nao abre de novo
        self.assertEqual(row['risco_dinheiro'], 100.0)
        self.assertEqual(paper.processar('XAU/USD', '1day', {}, self.daily(), 10), [])

    def test_tendencia_ignores_pairs_outside_its_universe(self):
        self.assertEqual(paper.processar('EUR/USD', '1day', {}, self.daily(), 10), [])

    def test_tendencia_trailing_stop_closes_with_profit(self):
        paper.processar('XAU/USD', '1day', {}, self.daily(), 10)
        d = self.daily()
        ultimo = d.iloc[-1]
        extra = pd.DataFrame([{'datetime': ultimo.datetime + pd.Timedelta(days=1), 'open': ultimo.close + 5, 'high': ultimo.close + 20,
                               'low': ultimo.close + 4, 'close': ultimo.close + 19, 'atr': 2.0},
                              {'datetime': ultimo.datetime + pd.Timedelta(days=2), 'open': ultimo.close + 10, 'high': ultimo.close + 11,
                               'low': ultimo.close + 5, 'close': ultimo.close + 6, 'atr': 2.0}])
        ev = paper.processar('XAU/USD', '1day', {}, pd.concat([d, extra], ignore_index=True), 10)
        fechou = [e for e in ev if e['evento'] == 'fechou']
        self.assertEqual(len(fechou), 1)
        self.assertEqual(fechou[0]['status'], 'trail')
        self.assertGreater(fechou[0]['resultado'], 0)       # stop subiu junto com o preco: saiu no lucro

    def test_tendencia_signal_exit_closes_at_bar_close(self):
        paper.processar('XAU/USD', '1day', {}, self.daily(), 10)
        d = self.daily()
        ultimo = d.iloc[-1]
        nova = pd.DataFrame([{'datetime': ultimo.datetime + pd.Timedelta(days=1), 'open': ultimo.close, 'high': ultimo.close + 1,
                              'low': ultimo.close - 1, 'close': ultimo.close, 'atr': 2.0}])
        original = paper.tendencia_score
        paper.tendencia_score = lambda df: pd.Series(-0.6, index=df.index)   # maioria virou pra baixa
        try:
            ev = paper.processar('XAU/USD', '1day', {}, pd.concat([d, nova], ignore_index=True), 10)
        finally:
            paper.tendencia_score = original
        fechou = [e for e in ev if e['evento'] == 'fechou']
        self.assertEqual([e['status'] for e in fechou], ['sinal'])
        self.assertEqual(fechou[0]['saida'], float(ultimo.close))

    def test_a_b_c_start_suspended_and_hidden_until_used(self):
        st = paper.estado()
        self.assertEqual(list(st['estrategias']), ['T'])  # so a T esta ligada e visivel (A/B/C suspensas e vazias)
        self.assertEqual((st['principal'], st['estrategia']), ('T', 'T'))
        self.ligar_a()
        self.assertEqual(list(paper.estado()['estrategias']), ['T', 'A'])   # A ligada passa a aparecer

    def test_tendencia_config_bounds(self):
        with self.assertRaises(ValueError):
            paper.set_config('T', {'atr_stop': 0.5})
        with self.assertRaises(ValueError):
            paper.set_config('T', {'entrada_min': 1.5})
        self.assertEqual(paper.set_config('T', {'somente_compra': True})['somente_compra'], True)


if __name__ == '__main__':
    unittest.main()
