"""
==============================================================================
  ROBO DIDI FOREX  -  Monitor de setup + alertas (Telegram + PC)
==============================================================================
Setup: Didi Index + Bandas de Bollinger + TRIX + Estocastico.
Avalia em ESTAGIOS de confluencia e avisa quando uma entrada esta proxima
ou confirmada. NAO executa ordens - so le o mercado e alerta.

------------------------------------------------------------------------------
COMO COLOCAR PRA RODAR (passo a passo)
------------------------------------------------------------------------------
1) Instale o Python 3.10+ e as dependencias:
       pip install pandas numpy requests plyer

   (plyer e opcional - serve para o popup no PC. Sem ele, o alerta de PC
    cai para som + mensagem no console.)

2) Pegue uma chave de API gratuita da Twelve Data:
       https://twelvedata.com/  ->  cadastro  ->  copie a API key
   Cole em TWELVE_DATA_API_KEY abaixo.

3) Crie um bot no Telegram para receber os alertas no celular:
       - No Telegram, fale com o @BotFather  ->  /newbot  ->  siga os passos
       - Ele te da um TOKEN. Cole em TELEGRAM_BOT_TOKEN.
       - Mande qualquer mensagem para o seu bot (uma vez), depois abra:
             https://api.telegram.org/bot<SEU_TOKEN>/getUpdates
         e copie o numero em "chat":{"id": ...}. Cole em TELEGRAM_CHAT_ID.

4) Teste a configuracao (faz uma leitura unica e manda um alerta de teste):
       python robo_didi_forex.py --check

5) Rode pra valer:
       python robo_didi_forex.py
   Deixe a janela aberta (ou rode numa VPS) para o monitoramento continuo.
==============================================================================
"""

import sys
import time
import datetime as dt

import numpy as np
import pandas as pd
import requests


# ==========================================================================
#  CONFIGURACAO  -  edite aqui
# ==========================================================================

TWELVE_DATA_API_KEY = "b8a2f2f6f54a47cdab5b8b314da1f9cd"

TELEGRAM_BOT_TOKEN = "COLE_O_TOKEN_DO_BOT_AQUI"
TELEGRAM_CHAT_ID   = "COLE_O_CHAT_ID_AQUI"

# Par a monitorar. Para euro/dolar troque por "EUR/USD".
SYMBOL = "EUR/BRL"

# Timeframes operacionais (geram alerta) e o de contexto (so filtra direcao).
INTERVALOS_OPERACIONAIS = ["5min", "15min"]
INTERVALO_CONTEXTO      = "30min"

# Estagio minimo que dispara alerta: 1=Vigiar, 2=Preparar, 3=Entrar.
ESTAGIO_MINIMO_ALERTA = 1

# Ligar/desligar canais de alerta
ALERTA_TELEGRAM = False
ALERTA_PC       = True

# Parametros dos indicadores (padroes classicos do setup)
DIDI = dict(curta=3, media=8, longa=20)
BB   = dict(periodo=20, desvios=2.0)
TRIX = dict(periodo=9, sinal=9)
ESTOC = dict(k=14, suav_k=3, d=3)

# Bollinger squeeze: candle e considerado "afunilado" se a largura estiver
# abaixo do percentil X das ultimas N barras.
BW_LOOKBACK = 50
BW_PERCENTIL = 30

# Quantos candles puxar por requisicao
OUTPUTSIZE = 200

# Intervalo do loop principal (segundos)
POLL_SEGUNDOS = 30


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


def montar_indicadores(df):
    out = df.copy()
    out = out.join(didi_index(out["close"], **DIDI))
    out = out.join(bollinger(out["close"], **BB))
    out = out.join(trix_ind(out["close"], **TRIX))
    out = out.join(estocastico(out["high"], out["low"], out["close"], **ESTOC))
    return out


# ==========================================================================
#  CONFLUENCIA (estagios)
# ==========================================================================

def _cruzou_cima(a, b):
    return (a.iloc[-2] <= b.iloc[-2]) and (a.iloc[-1] > b.iloc[-1])


def _cruzou_baixo(a, b):
    return (a.iloc[-2] >= b.iloc[-2]) and (a.iloc[-1] < b.iloc[-1])


def avaliar(df):
    """Avalia o ultimo candle FECHADO do df e devolve direcao/estagio."""
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

    componentes = {
        "agulhada": "compra" if agulhada_compra else ("venda" if agulhada_venda else "nao"),
        "bollinger": "afunilado" if em_squeeze else ("expandindo" if expandindo else "neutro"),
        "trix": "compra" if trix_compra else ("venda" if trix_venda else "neutro"),
        "estocastico": "compra" if estoc_compra else ("venda" if estoc_venda else "neutro"),
    }

    direcao, estagio = None, 0
    conf_compra = sum([trix_compra, estoc_compra, expandindo])
    conf_venda  = sum([trix_venda, estoc_venda, expandindo])

    if agulhada_compra:
        direcao = "compra"
        estagio = 3 if conf_compra >= 3 else 2
    elif agulhada_venda:
        direcao = "venda"
        estagio = 3 if conf_venda >= 3 else 2
    elif convergindo and perto and em_squeeze:
        direcao = "compra" if c["didi_curta"] < c["didi_longa"] else "venda"
        estagio = 1

    return {"direcao": direcao, "estagio": estagio,
            "componentes": componentes, "preco": float(c["close"])}


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
#  FONTE DE DADOS (Twelve Data)
# ==========================================================================

def buscar_candles(symbol, interval, outputsize=OUTPUTSIZE):
    """Retorna DataFrame OHLC ordenado do mais antigo ao mais novo.
    O ultimo candle (em formacao) e descartado para evitar repaint."""
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

    df = pd.DataFrame(data["values"]).copy()
    for col in ["open", "high", "low", "close"]:
        df.loc[:, col] = pd.to_numeric(df[col], errors="coerce")
    df.loc[:, "datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    # descarta o candle ainda em formacao (o mais recente)
    df = df.iloc[:-1].reset_index(drop=True)
    return df[["datetime", "open", "high", "low", "close"]]


# ==========================================================================
#  ALERTAS
# ==========================================================================

def alerta_telegram(msg):
    if not ALERTA_TELEGRAM:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg,
                                 "parse_mode": "HTML"}, timeout=15)
    except Exception as e:
        print(f"[telegram] falhou: {e}")


def alerta_pc(titulo, msg):
    if not ALERTA_PC:
        return
    # som
    try:
        import winsound
        winsound.MessageBeep()
    except Exception:
        print("\a", end="", flush=True)  # bell do terminal
    # popup (opcional, via plyer)
    try:
        from plyer import notification
        notification.notify(title=titulo, message=msg, timeout=10)
    except Exception:
        pass  # sem plyer, fica so o som + console


NOMES_ESTAGIO = {1: "VIGIAR", 2: "PREPARAR", 3: "ENTRAR"}
EMOJI_DIR = {"compra": "🟢", "venda": "🔴"}


def formata_mensagem(timeframe, aval):
    est = aval["estagio"]
    dirr = aval["direcao"]
    comp = aval["componentes"]
    contra = aval.get("contra_tendencia", False)
    cab = f"{EMOJI_DIR.get(dirr,'')} <b>{NOMES_ESTAGIO.get(est,'?')}</b> | {dirr.upper()} | {SYMBOL} {timeframe}"
    linhas = [
        cab,
        f"Preco: {aval['preco']}",
        f"Agulhada Didi: {comp['agulhada']}",
        f"Bollinger: {comp['bollinger']}",
        f"TRIX: {comp['trix']}",
        f"Estocastico: {comp['estocastico']}",
    ]
    if contra:
        linhas.append("⚠️ contra a tendencia do M30 (cautela)")
    linhas.append(dt.datetime.now().strftime("%d/%m %H:%M:%S"))
    return "\n".join(linhas)


# ==========================================================================
#  LOOP PRINCIPAL
# ==========================================================================

SEGUNDOS_POR_INTERVALO = {"1min": 60, "5min": 300, "15min": 900,
                          "30min": 1800, "45min": 2700, "1h": 3600}


def carregar_avaliacao(symbol, interval):
    df = buscar_candles(symbol, interval)
    df = montar_indicadores(df)
    aval = avaliar(df)
    ts_ultimo = df["datetime"].iloc[-1]
    return aval, ts_ultimo


def rodar():
    print(f"== Robo Didi Forex == {SYMBOL} | op={INTERVALOS_OPERACIONAIS} | ctx={INTERVALO_CONTEXTO}")
    print("Monitorando... (Ctrl+C para parar)\n")

    ultimo_fetch = {tf: 0.0 for tf in INTERVALOS_OPERACIONAIS + [INTERVALO_CONTEXTO]}
    ultimo_candle_alertado = {tf: None for tf in INTERVALOS_OPERACIONAIS}
    aval_contexto = {"direcao": None, "estagio": 0}

    while True:
        try:
            agora = time.time()

            # atualiza contexto (M30) quando devido
            if agora - ultimo_fetch[INTERVALO_CONTEXTO] >= SEGUNDOS_POR_INTERVALO[INTERVALO_CONTEXTO]:
                aval_contexto, _ = carregar_avaliacao(SYMBOL, INTERVALO_CONTEXTO)
                ultimo_fetch[INTERVALO_CONTEXTO] = agora
                print(f"[ctx {INTERVALO_CONTEXTO}] direcao={aval_contexto['direcao']} estagio={aval_contexto['estagio']}")

            # timeframes operacionais
            for tf in INTERVALOS_OPERACIONAIS:
                if agora - ultimo_fetch[tf] < SEGUNDOS_POR_INTERVALO[tf]:
                    continue
                aval, ts = carregar_avaliacao(SYMBOL, tf)
                ultimo_fetch[tf] = agora

                aval = filtro_contexto(aval, aval_contexto)
                novo_candle = (ultimo_candle_alertado[tf] != ts)

                marca = aval["direcao"] or "-"
                print(f"[{tf}] {ts}  dir={marca} estagio={aval['estagio']}")

                if aval["estagio"] >= ESTAGIO_MINIMO_ALERTA and novo_candle and aval["direcao"]:
                    msg = formata_mensagem(tf, aval)
                    print("  --> ALERTA\n" + "  " + msg.replace("\n", "\n  "))
                    alerta_telegram(msg)
                    alerta_pc(f"{NOMES_ESTAGIO[aval['estagio']]} {SYMBOL} {tf}", msg)
                    ultimo_candle_alertado[tf] = ts

            time.sleep(POLL_SEGUNDOS)

        except KeyboardInterrupt:
            print("\nEncerrado.")
            break
        except Exception as e:
            print(f"[erro] {e} - tentando de novo em 30s")
            time.sleep(30)


# ==========================================================================
#  MODOS DE TESTE
# ==========================================================================

def checar():
    """Leitura unica de cada timeframe + alerta de teste no Telegram/PC."""
    print("== CHECK ==")
    try:
        aval_ctx, _ = carregar_avaliacao(SYMBOL, INTERVALO_CONTEXTO)
        print(f"contexto {INTERVALO_CONTEXTO}: {aval_ctx['direcao']} estagio {aval_ctx['estagio']}")
    except Exception as e:
        print(f"ERRO ao buscar contexto: {e}")
        aval_ctx = {"direcao": None, "estagio": 0}

    for tf in INTERVALOS_OPERACIONAIS:
        try:
            aval, ts = carregar_avaliacao(SYMBOL, tf)
            aval = filtro_contexto(aval, aval_ctx)
            print(f"\n[{tf}] candle {ts}")
            print(f"  direcao={aval['direcao']} estagio={aval['estagio']} preco={aval['preco']}")
            print(f"  componentes={aval['componentes']}")
        except Exception as e:
            print(f"[{tf}] ERRO: {e}")

    print("\nEnviando alerta de teste...")
    alerta_telegram(f"✅ Teste do Robo Didi Forex em {SYMBOL}. Conexao OK.")
    alerta_pc("Teste Robo Didi", f"Conexao OK em {SYMBOL}")
    print("Pronto. Se nao chegou no Telegram, confira TOKEN e CHAT_ID.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        checar()
    else:
        rodar()
