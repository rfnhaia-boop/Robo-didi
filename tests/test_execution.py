import unittest
import pandas as pd

from backend.execution import advance, pnl, size_position, validate_candles


class ExecutionTests(unittest.TestCase):
    def position(self, direction='compra'):
        if direction == 'compra':
            return dict(direcao='compra', entrada=100.0, sl=90.0,
                        sl_original=90.0, tp=130.0, breakeven=0,
                        risco_dinheiro=100.0, tamanho=size_position(100, 90, 100, 1))
        return dict(direcao='venda', entrada=100.0, sl=110.0,
                    sl_original=110.0, tp=70.0, breakeven=0,
                    risco_dinheiro=100.0, tamanho=size_position(100, 110, 100, 1))

    def test_breakeven_only_applies_to_following_bar(self):
        p, event = advance(self.position(), {'open':100,'high':111,'low':95}, True)
        self.assertIsNone(event)
        self.assertEqual(p['sl'], 100)
        p, event = advance(p, {'open':110,'high':115,'low':109}, True)
        self.assertIsNone(event)

    def test_ambiguous_bar_uses_stop_first(self):
        _, event = advance(self.position(), {'open':100,'high':131,'low':89}, True)
        self.assertEqual(event, ('stop', 90.0))

    def test_stop_gap_uses_worse_open(self):
        _, event = advance(self.position(), {'open':85,'high':91,'low':84}, True)
        self.assertEqual(event, ('stop', 85.0))
        _, event = advance(self.position('venda'), {'open':115,'high':116,'low':109}, True)
        self.assertEqual(event, ('stop', 115.0))

    def test_position_size_caps_normal_stop_loss_including_cost(self):
        p = self.position()
        result, r = pnl(p, 90, 1)
        self.assertAlmostEqual(result, -100)
        self.assertAlmostEqual(r, -1)

    def test_rejects_bad_ohlc(self):
        df = pd.DataFrame([{'datetime':'2026-01-01','open':10,'high':9,'low':8,'close':10}])
        with self.assertRaises(ValueError):
            validate_candles(df)


if __name__ == '__main__':
    unittest.main()
