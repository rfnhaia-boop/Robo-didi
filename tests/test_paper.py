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

    def test_same_candle_does_not_open_twice(self):
        rows=[]
        for ts in pd.date_range('2026-09-09 05:00', periods=60, freq='30min'):
            rows.append((ts,100,102,98,100))
        df=self.candles(rows)
        original=paper._sinal_entrada
        paper._sinal_entrada=lambda *a: {'direcao':'compra','entrada':100,'sl':98}
        try:
            first=paper.processar('EUR/USD','30min',{},df,10)
            second=paper.processar('EUR/USD','30min',{},df,10)
        finally:
            paper._sinal_entrada=original
        # A opens. B is suspended and C excludes EUR/USD.
        self.assertEqual(sum(e['evento']=='abriu' for e in first),1)
        self.assertEqual(second,[])

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
