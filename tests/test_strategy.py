import unittest
import numpy as np
import pandas as pd

from backend.strategy import (tendencia_score, tendencia_entrada, tendencia_saida,
                              entry_signal, market_open, TEND_MIN_BARRAS)


def serie(n, passo):
    close = 100 + np.arange(n) * passo
    idx = pd.date_range('2025-01-01', periods=n, freq='D')
    return pd.DataFrame({'datetime': idx, 'open': close, 'high': close + 1, 'low': close - 1,
                         'close': close, 'atr': 2.0})


class TendenciaTests(unittest.TestCase):
    def test_uptrend_scores_plus_one_and_downtrend_minus_one(self):
        self.assertEqual(tendencia_score(serie(300, 1.0)).iloc[-1], 1.0)
        self.assertEqual(tendencia_score(serie(300, -0.2)).iloc[-1], -1.0)

    def test_score_is_causal(self):
        # o valor numa janela curta e igual ao do historico inteiro (sem olhar o futuro)
        d = serie(400, 0.5)
        d.loc[300:, 'close'] = d.loc[300:, 'close'] - np.arange(100) * 1.5
        d['high'] = d['close'] + 1; d['low'] = d['close'] - 1
        cheio = tendencia_score(d)
        for i in (280, 320, 399):
            self.assertAlmostEqual(cheio.iloc[i], tendencia_score(d.iloc[:i + 1]).iloc[-1])

    def test_entry_long_short_and_sizes_stop_by_atr(self):
        cfg = dict(tipo='tendencia')
        compra = tendencia_entrada(cfg, 0.6, 100.0, 2.0)
        self.assertEqual((compra['direcao'], compra['sl'], compra['trail']), ('compra', 94.0, 6.0))
        venda = tendencia_entrada(cfg, -1.0, 100.0, 2.0)
        self.assertEqual((venda['direcao'], venda['sl']), ('venda', 106.0))
        self.assertIsNone(tendencia_entrada(cfg, 0.2, 100.0, 2.0))          # so 3 de 5: nao entra
        self.assertIsNone(tendencia_entrada({**cfg, 'somente_compra': True}, -1.0, 100.0, 2.0))
        self.assertIsNone(tendencia_entrada(cfg, float('nan'), 100.0, 2.0))
        self.assertIsNone(tendencia_entrada(cfg, 1.0, 100.0, 0.0))

    def test_exit_when_majority_turns_with_hysteresis(self):
        cfg = {}
        self.assertFalse(tendencia_saida(cfg, 'compra', 0.2))    # ainda 3 de 5 a favor: segura
        self.assertTrue(tendencia_saida(cfg, 'compra', -0.2))    # maioria virou: sai
        self.assertFalse(tendencia_saida(cfg, 'venda', -0.2))
        self.assertTrue(tendencia_saida(cfg, 'venda', 0.2))

    def test_entry_signal_needs_history_and_atr(self):
        cfg = dict(tipo='tendencia')
        self.assertIsNone(entry_signal(cfg, {}, serie(TEND_MIN_BARRAS - 1, 1.0)))
        self.assertIsNotNone(entry_signal(cfg, {}, serie(300, 1.0)))
        self.assertIsNone(entry_signal(cfg, {}, serie(300, 1.0).drop(columns=['atr'])))

    def test_crypto_is_24_7_but_fx_and_gold_close_on_weekends(self):
        sabado = '2026-10-17 12:00:00'
        self.assertTrue(market_open('BTC/USD', sabado))
        self.assertTrue(market_open('ETH/USD', sabado))
        self.assertFalse(market_open('XAU/USD', sabado))


if __name__ == '__main__':
    unittest.main()
