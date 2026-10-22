import unittest
from backend import engine


class FakeResp:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload


class BuscarCandlesTests(unittest.TestCase):
    def test_duplicate_vendor_bar_is_collapsed_not_fatal(self):
        valores = [{'datetime': '2026-10-05', 'open': '4140', 'high': '4150', 'low': '4130', 'close': '4145'},
                   {'datetime': '2026-10-04', 'open': '4137.5', 'high': '4137.7', 'low': '4137.4', 'close': '4137.51'},
                   {'datetime': '2026-10-04', 'open': '4137.5', 'high': '4137.7', 'low': '4137.4', 'close': '4137.63'},
                   {'datetime': '2026-10-03', 'open': '4130', 'high': '4141', 'low': '4125', 'close': '4137'}]
        antigo_get, antiga_chave = engine.requests.get, engine.TWELVE_DATA_API_KEY
        engine.requests.get = lambda *a, **k: FakeResp({'status': 'ok', 'values': valores})
        engine.TWELVE_DATA_API_KEY = 'x'
        try:
            df = engine.buscar_candles('XAU/USD', '1day', 10)
        finally:
            engine.requests.get, engine.TWELVE_DATA_API_KEY = antigo_get, antiga_chave
        self.assertEqual(len(df), 3)
        self.assertTrue(df.datetime.is_unique and df.datetime.is_monotonic_increasing)


if __name__ == '__main__':
    unittest.main()
