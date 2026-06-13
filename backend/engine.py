import datetime as dt
import numpy as np
import pandas as pd
import requests

from backend.config import (
    TWELVE_DATA_API_KEY, OUTPUTSIZE,
    DIDI, BB, TRIX, ESTOC,
    BW_LOOKBACK, BW_PERCENTIL,
    INTERVALO_CONTEXTO,
)


# ==========================================================================
#  INDICADORES
# ==========================================================================

def didi_index(close, curta=3, media=8, longa=20):
    sma_c = close.rolling(curta).mean()
    sma_m = close.rolling(media).mean()
    sma_l = close.rolling(longa).mean()
    return pd.DataFrame({
        "didi_curta": (sma_c / sma_m - 1) * 100.0,
        "didi_longa": (sma_l / sma_m - 1) * 100.0,
    })


def bollinger(close, periodo=20, desvios=2.0):
    mid = close.rolling(periodo).mean()
    std = close.rolling(periodo).std(ddof=0)
    upper = mid + desvios * std
    lower = mid - desvios * std
    bandwidth = (upper - lower) / mid * 100.0
    return pd.DataFrame({"bb_mid": mid, "bb_upper": upper,
                         "bb_lower": lower, "bb_bw": bandwidth})


def trix_ind(close, periodo=9, sinal=9):
    e1 = close.ewm(span=periodo, adjust=False).mean()
    e2 = e1.ewm(span=periodo, adjust=False).mean()
    e3 = e2.ewm(span=periodo, adjust=False).mean()
    linha = e3.pct_change() * 100.0
    return pd.DataFrame({"trix": linha,
                         "trix_sinal": linha.ewm(span=sinal, adjust=False).mean()})


def estocastico(high, low, close, k=14, suav_k=3, d=3):
    ll = low.rolling(k).min()
    hh = high.rolling(k).max()
    raw_k = (close - ll) / (hh - ll) * 100.0
    slow_k = raw_k.rolling(suav_k).mean()
    slow_d = slow_k.rolling(d).mean()
    return pd.DataFrame({"estoc_k": slow_k, "estoc_d": slow_d})


def adx_ind(high, low, close, periodo=14):
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    tr = pd.concat([high - low,
                    (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / periodo, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / periodo, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / periodo, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=1 / periodo, adjust=False).mean()
    return pd.DataFrame({"adx": adx})


def rsi_ind(close, periodo=14):
    delta = close.diff()
    ganho = delta.clip(lower=0).ewm(alpha=1 / periodo, adjust=False).mean()
    perda = (-delta.clip(upper=0)).ewm(alpha=1 / periodo, adjust=False).mean()
    rs = ganho / perda
    return pd.DataFrame({"rsi": 100 - 100 / (1 + rs)})


def atr_ind(high, low, close, periodo=14):
    tr = pd.concat([high - low,
                    (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / periodo, adjust=False).mean()
    return pd.DataFrame({"atr": atr, "atr_pct": atr / close * 100})


def montar_indicadores(df):
    out = df.copy()
    out = out.join(didi_index(out["close"], **DIDI))
    out = out.join(bollinger(out["close"], **BB))
    out = out.join(trix_ind(out["close"], **TRIX))
    out = out.join(estocastico(out["high"], out["low"], out["close"], **ESTOC))
    out = out.join(adx_ind(out["high"], out["low"], out["close"]))
    out = out.join(rsi_ind(out["close"]))
    out = out.join(atr_ind(out["high"], out["low"], out["close"]))
    return out


# ==========================================================================
#  CONFLUENCIA
# ==========================================================================

def _cruzou_cima(a, b):
    return (a.iloc[-2] <= b.iloc[-2]) and (a.iloc[-1] > b.iloc[-1])


def _cruzou_baixo(a, b):
    return (a.iloc[-2] >= b.iloc[-2]) and (a.iloc[-1] < b.iloc[-1])


def avaliar(df):
    if len(df) < BW_LOOKBACK + 5:
        return {"direcao": None, "estagio": 0, "componentes": {}, "preco": None}

    c, p = df.iloc[-1], df.iloc[-2]

    agulhada_compra = _cruzou_cima(df["didi_curta"], df["didi_longa"])
    agulhada_venda  = _cruzou_baixo(df["didi_curta"], df["didi_longa"])

    dist_atual = abs(c["didi_curta"] - c["didi_longa"])
    dist_ant   = abs(p["didi_curta"] - p["didi_longa"])
    convergindo = dist_atual < dist_ant
    std_didi = df["didi_curta"].rolling(20).std().iloc[-1]
    perto = dist_atual < (std_didi * 0.6 if std_didi and not np.isnan(std_didi) else 1e9)

    bw = df["bb_bw"]
    limiar = np.nanpercentile(bw.iloc[-BW_LOOKBACK:], BW_PERCENTIL)
    em_squeeze = c["bb_bw"] <= limiar
    expandindo = c["bb_bw"] > p["bb_bw"]

    trix_compra = (c["trix"] > c["trix_sinal"]) and (c["trix"] > p["trix"])
    trix_venda  = (c["trix"] < c["trix_sinal"]) and (c["trix"] < p["trix"])

    estoc_compra = (c["estoc_k"] > c["estoc_d"]) and (c["estoc_k"] < 80)
    estoc_venda  = (c["estoc_k"] < c["estoc_d"]) and (c["estoc_k"] > 20)

    adx_forte = (not np.isnan(c["adx"])) and (c["adx"] > 25) and (c["adx"] > p["adx"])

    componentes = {
        "agulhada": "compra" if agulhada_compra else ("venda" if agulhada_venda else "nao"),
        "bollinger": "afunilado" if em_squeeze else ("expandindo" if expandindo else "neutro"),
        "trix": "compra" if trix_compra else ("venda" if trix_venda else "neutro"),
        "estocastico": "compra" if estoc_compra else ("venda" if estoc_venda else "neutro"),
        "adx": "forte" if adx_forte else "fraco",
    }

    direcao, estagio = None, 0
    conf_compra = sum([trix_compra, estoc_compra, expandindo, adx_forte])
    conf_venda  = sum([trix_venda, estoc_venda, expandindo, adx_forte])

    if agulhada_compra:
        direcao = "compra"
        estagio = 3 if conf_compra >= 3 else 2
    elif agulhada_venda:
        direcao = "venda"
        estagio = 3 if conf_venda >= 3 else 2
    elif convergindo and perto and em_squeeze:
        direcao = "compra" if c["didi_curta"] < c["didi_longa"] else "venda"
        estagio = 1

    # Previsao: quantos candles ate as linhas do Didi se cruzarem
    # (extrapolacao linear da velocidade de aproximacao)
    previsao_candles = None
    if estagio >= 2:
        previsao_candles = 0  # entrada agora
    else:
        taxa = dist_ant - dist_atual  # quanto fechou por candle
        if taxa > 0:
            n = dist_atual / taxa
            if n <= 30:
                previsao_candles = max(1, round(n))

    return {"direcao": direcao, "estagio": estagio,
            "componentes": componentes, "preco": float(c["close"]),
            "previsao_candles": previsao_candles,
            "rsi": round(float(c["rsi"]), 1) if not np.isnan(c["rsi"]) else None,
            "atr_pct": round(float(c["atr_pct"]), 3) if not np.isnan(c["atr_pct"]) else None,
            "adx_valor": round(float(c["adx"]), 1) if not np.isnan(c["adx"]) else None}


# ==========================================================================
#  TRAVAS — semaforo de operacao
# ==========================================================================

JANELA_INICIO = 8    # horario validado no walk-forward (BRT)
JANELA_FIM = 13


def montar_travas(aval, hora_brt):
    """Avalia condicoes que dizem 'NAO opere agora' e devolve um semaforo.
    verde = pode operar | amarelo = cautela | vermelho = espere."""
    travas = []

    dentro_janela = JANELA_INICIO <= hora_brt < JANELA_FIM
    if not dentro_janela:
        travas.append(f"Fora da janela validada ({JANELA_INICIO}h-{JANELA_FIM}h BRT) — "
                      "a estatistica mostra que sinais fora dela perdem dinheiro")

    if aval.get("contra_tendencia"):
        travas.append("Sinal contra a tendencia do M30 — taxa de acerto cai muito")

    if aval.get("componentes", {}).get("adx") == "fraco":
        travas.append("ADX fraco — mercado sem tendencia, agulhada tende a falhar")

    rsi = aval.get("rsi")
    if rsi is not None and aval.get("direcao"):
        if aval["direcao"] == "compra" and rsi > 70:
            travas.append(f"RSI sobrecomprado ({rsi}) — comprar agora e entrar esticado")
        elif aval["direcao"] == "venda" and rsi < 30:
            travas.append(f"RSI sobrevendido ({rsi}) — vender agora e entrar esticado")

    estagio = aval.get("estagio", 0)
    if 0 < estagio < 2:
        travas.append("Setup ainda em formacao — espere a agulhada confirmar (estagio 2+)")

    # semaforo
    if estagio >= 2 and not travas:
        cor, msg = "verde", "Condicoes alinhadas — setup valido dentro das regras"
    elif estagio >= 2 and len(travas) == 1 and dentro_janela:
        cor, msg = "amarelo", "Setup ativo, mas com ressalva — opere menor ou espere"
    elif estagio >= 2:
        cor, msg = "vermelho", "Setup ativo, mas FORA das regras — melhor esperar o formato certo"
    else:
        cor, msg = "cinza", "Sem setup confirmado — aguarde o mercado armar"

    return {"semaforo": cor, "mensagem": msg, "travas": travas,
            "dentro_janela": dentro_janela}


def filtro_contexto(aval_op, aval_ctx):
    if aval_op["direcao"] is None or aval_ctx["direcao"] is None:
        aval_op["contra_tendencia"] = False
        return aval_op
    contra = aval_op["direcao"] != aval_ctx["direcao"]
    aval_op["contra_tendencia"] = contra
    if contra and aval_op["estagio"] > 1:
        aval_op["estagio"] -= 1
    return aval_op


# ==========================================================================
#  DADOS
# ==========================================================================

def buscar_candles(symbol, interval, outputsize=OUTPUTSIZE):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_API_KEY,
        "timezone": "UTC",
        "format": "JSON",
    }
    r = requests.get(url, params=params, timeout=20)
    data = r.json()
    if data.get("status") == "error" or "values" not in data:
        raise RuntimeError(f"Twelve Data: {data.get('message', data)}")

    df = pd.DataFrame(data["values"])
    df = df.astype({"open": float, "high": float, "low": float, "close": float})
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    df = df.iloc[:-1].reset_index(drop=True)
    return df[["datetime", "open", "high", "low", "close"]]


def carregar_avaliacao(symbol, interval):
    df = buscar_candles(symbol, interval)
    df_ind = montar_indicadores(df)
    aval = avaliar(df_ind)
    ts_ultimo = df["datetime"].iloc[-1]
    return aval, ts_ultimo, df, df_ind


def df_para_candles(df, df_ind):
    """Serializa candles + indicadores para JSON."""
    rows = []
    for i, row in df.iterrows():
        ind = df_ind.iloc[i]
        rows.append({
            "time": int(row["datetime"].timestamp()),
            "open":  round(float(row["open"]),  5),
            "high":  round(float(row["high"]),  5),
            "low":   round(float(row["low"]),   5),
            "close": round(float(row["close"]), 5),
            "bb_upper": round(float(ind["bb_upper"]), 5) if not np.isnan(ind["bb_upper"]) else None,
            "bb_mid":   round(float(ind["bb_mid"]),   5) if not np.isnan(ind["bb_mid"])   else None,
            "bb_lower": round(float(ind["bb_lower"]), 5) if not np.isnan(ind["bb_lower"]) else None,
            "didi_curta": round(float(ind["didi_curta"]), 4) if not np.isnan(ind["didi_curta"]) else None,
            "didi_longa": round(float(ind["didi_longa"]), 4) if not np.isnan(ind["didi_longa"]) else None,
            "trix":       round(float(ind["trix"]),       4) if not np.isnan(ind["trix"])        else None,
            "trix_sinal": round(float(ind["trix_sinal"]), 4) if not np.isnan(ind["trix_sinal"])  else None,
            "estoc_k": round(float(ind["estoc_k"]), 2) if not np.isnan(ind["estoc_k"]) else None,
            "estoc_d": round(float(ind["estoc_d"]), 2) if not np.isnan(ind["estoc_d"]) else None,
        })
    return rows
