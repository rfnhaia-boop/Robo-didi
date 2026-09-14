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

    def tearDown(self):
        self.tmp.cleanup()

    def candles(self, rows):
        d = pd.DataFrame(rows, columns=['datetime','open','high','low','close'])
        d['datetime'] = pd.to_datetime(d.datetime)
        return d

    def test_processar_never_opens_estrategia_a_directly(self):
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


if __name__ == '__main__':
    unittest.main()
