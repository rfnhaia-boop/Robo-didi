"""Baixa historico longo da Twelve Data (paginando por end_date), com cache em disco
e ritmo <= 7 req/min pra nao estourar o limite gratis nem atrapalhar o robo ao vivo."""
import os, sys, time, json, pickle
import requests, pandas as pd

KEY = os.environ['TDK']
SP = os.path.dirname(__file__)
CACHE = os.path.join(SP, 'hist_cache.pkl')
cache = pickle.load(open(CACHE, 'rb')) if os.path.exists(CACHE) else {}
_ultimo = [0.0]


def get(symbol, interval, end_date=None, outputsize=5000):
    espera = 8.7 - (time.time() - _ultimo[0])
    if espera > 0:
        time.sleep(espera)
    p = dict(symbol=symbol, interval=interval, outputsize=outputsize, apikey=KEY, timezone='UTC', format='JSON', order='DESC')
    if end_date:
        p['end_date'] = end_date
    r = requests.get('https://api.twelvedata.com/time_series', params=p, timeout=40)
    _ultimo[0] = time.time()
    j = r.json()
    if j.get('status') == 'error' or 'values' not in j:
        return None, j.get('message', str(j))[:160]
    df = pd.DataFrame(j['values'])
    for c in ['open', 'high', 'low', 'close']:
        df[c] = df[c].astype(float)
    df['datetime'] = pd.to_datetime(df['datetime'])
    return df.sort_values('datetime').reset_index(drop=True), None


def baixar(symbol, interval, paginas=1):
    chave = (symbol, interval)
    if chave in cache and cache[chave] is not None and len(cache[chave]) > 0:
        return cache[chave]
    partes, end = [], None
    for _ in range(paginas):
        df, err = get(symbol, interval, end)
        if df is None:
            print(f'  ! {symbol} {interval}: {err}', file=sys.stderr)
            break
        partes.append(df)
        if len(df) < 4900:
            break
        end = (df.datetime.min() - pd.Timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S')
    if not partes:
        cache[chave] = None
        return None
    out = pd.concat(partes).drop_duplicates('datetime').sort_values('datetime').reset_index(drop=True)
    cache[chave] = out
    pickle.dump(cache, open(CACHE, 'wb'))
    print(f'  {symbol:8} {interval:6} {len(out):5} barras  {out.datetime.min().date()} -> {out.datetime.max().date()}', file=sys.stderr)
    return out


if __name__ == '__main__':
    plano = json.loads(sys.argv[1])  # lista de [symbol, interval, paginas]
    for s, i, p in plano:
        baixar(s, i, p)
