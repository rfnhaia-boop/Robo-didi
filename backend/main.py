import asyncio
import datetime as dt
import json
import time
from collections import deque
from typing import List

import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.config import (
    SYMBOL, INTERVALOS_OPERACIONAIS, INTERVALO_CONTEXTO,
    ESTAGIO_MINIMO_ALERTA, POLL_SEGUNDOS,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ALERTA_TELEGRAM,
)
from typing import Optional
from pydantic import BaseModel

from backend.engine import carregar_avaliacao, filtro_contexto, df_para_candles, montar_travas
from backend.backtest import rodar_backtest
from backend import forward
from backend import paper

app = FastAPI(title="Robo Didi Forex")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================================================
#  ESTADO GLOBAL
# ==========================================================================

estado = {
    "symbol": SYMBOL,
    "monitorando": True,
    "ultimo_update": None,
    "timeframes": {},   # tf -> {aval, candles}
    "contexto": {"direcao": None, "estagio": 0},
}

historico_alertas: deque = deque(maxlen=100)
clientes_ws: List[WebSocket] = []

NOMES_ESTAGIO = {1: "VIGIAR", 2: "PREPARAR", 3: "ENTRAR"}
SEGUNDOS_POR_INTERVALO = {"1min": 60, "5min": 300, "15min": 900,
                          "30min": 1800, "45min": 2700, "1h": 3600}


# ==========================================================================
#  TELEGRAM
# ==========================================================================

def enviar_telegram(msg: str):
    if not ALERTA_TELEGRAM or not TELEGRAM_BOT_TOKEN:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg,
                                 "parse_mode": "HTML"}, timeout=10)
    except Exception:
        pass


# ==========================================================================
#  BROADCAST WEBSOCKET
# ==========================================================================

async def broadcast(payload: dict):
    msg = json.dumps(payload)
    mortos = []
    for ws in clientes_ws:
        try:
            await ws.send_text(msg)
        except Exception:
            mortos.append(ws)
    for ws in mortos:
        clientes_ws.remove(ws)


# ==========================================================================
#  LOOP DE MONITORAMENTO
# ==========================================================================

ultimo_fetch = {}
ultimo_candle_alertado = {}


async def loop_monitoramento():
    global ultimo_fetch, ultimo_candle_alertado

    for tf in INTERVALOS_OPERACIONAIS + [INTERVALO_CONTEXTO]:
        ultimo_fetch[tf] = 0.0
    for tf in INTERVALOS_OPERACIONAIS:
        ultimo_candle_alertado[tf] = None

    while True:
        if not estado["monitorando"]:
            await asyncio.sleep(2)
            continue

        agora = time.time()
        symbol = estado["symbol"]

        try:
            # contexto
            if agora - ultimo_fetch.get(INTERVALO_CONTEXTO, 0) >= SEGUNDOS_POR_INTERVALO[INTERVALO_CONTEXTO]:
                aval_ctx, _, _, _ = carregar_avaliacao(symbol, INTERVALO_CONTEXTO)
                estado["contexto"] = aval_ctx
                ultimo_fetch[INTERVALO_CONTEXTO] = agora

            # timeframes operacionais
            for tf in INTERVALOS_OPERACIONAIS:
                if agora - ultimo_fetch.get(tf, 0) < SEGUNDOS_POR_INTERVALO[tf]:
                    continue

                aval, ts, df, df_ind = carregar_avaliacao(symbol, tf)
                aval = filtro_contexto(aval, estado["contexto"])
                aval["guarda"] = montar_travas(aval, dt.datetime.now().hour)
                ultimo_fetch[tf] = agora

                candles = df_para_candles(df, df_ind)
                estado["timeframes"][tf] = {"aval": aval, "ts": str(ts), "candles": candles}
                estado["ultimo_update"] = dt.datetime.now().isoformat()

                # forward test: resolve sinais pendentes com os candles novos
                try:
                    forward.verificar(symbol, tf, df)
                except Exception:
                    pass

                # paper trading: abre/fecha posicoes virtuais ao vivo
                try:
                    eventos = paper.processar(symbol, tf, aval, df, dt.datetime.now().hour)
                    for evt in (eventos or []):
                        await broadcast({"tipo": "paper", "operacao": evt, "estado": paper.estado()})
                        if evt["evento"] == "abriu":
                            emoji = "🟢" if evt["direcao"] == "compra" else "🔴"
                            enviar_telegram(
                                f"{emoji} <b>PAPER — ABRIR {evt['direcao'].upper()}</b> | {symbol} {tf}\n"
                                f"Entrada: {evt['entrada']}\nStop: {evt['sl']}\nAlvo: {evt['tp']}\n"
                                f"Risco: ${evt['risco']}\n(operacao simulada — copie manualmente)")
                        elif evt["evento"] == "fechou":
                            ico = "✅" if evt["status"] == "alvo" else ("➖" if evt["status"] == "breakeven" else "❌")
                            sinal = "+" if evt["resultado"] >= 0 else ""
                            enviar_telegram(
                                f"{ico} <b>PAPER — FECHOU ({evt['status'].upper()})</b> | {symbol} {tf}\n"
                                f"Resultado: {sinal}${evt['resultado']} ({sinal}{evt['resultado_r']}R)\n"
                                f"Banca: ${evt['banca']}")
                except Exception:
                    pass

                await broadcast({"tipo": "update", "tf": tf, "aval": aval,
                                 "ts": str(ts), "candles": candles,
                                 "contexto": estado["contexto"]})

                # alerta
                novo = (ultimo_candle_alertado.get(tf) != ts)
                if aval["estagio"] >= ESTAGIO_MINIMO_ALERTA and novo and aval["direcao"]:
                    alerta = {
                        "hora": dt.datetime.now().strftime("%d/%m %H:%M"),
                        "timestamp": int(ts.timestamp()),
                        "tf": tf, "symbol": symbol,
                        "direcao": aval["direcao"],
                        "estagio": aval["estagio"],
                        "nome": NOMES_ESTAGIO.get(aval["estagio"], ""),
                        "preco": aval["preco"],
                        "componentes": aval["componentes"],
                        "contra_tendencia": aval.get("contra_tendencia", False),
                        "guarda": aval.get("guarda"),
                    }
                    historico_alertas.appendleft(alerta)
                    await broadcast({"tipo": "alerta", "alerta": alerta})

                    # forward test: registra sinais de estagio 2+ para autoavaliacao
                    try:
                        forward.registrar(alerta, df)
                    except Exception:
                        pass

                    emoji = "🟢" if aval["direcao"] == "compra" else "🔴"
                    msg_tg = (
                        f"{emoji} <b>{alerta['nome']}</b> | {aval['direcao'].upper()} | {symbol} {tf}\n"
                        f"Preco: {aval['preco']}\n"
                        f"Agulhada: {aval['componentes']['agulhada']}\n"
                        f"Bollinger: {aval['componentes']['bollinger']}\n"
                        f"TRIX: {aval['componentes']['trix']}\n"
                        f"Estocastico: {aval['componentes']['estocastico']}\n"
                        + ("⚠️ contra tendencia M30\n" if alerta["contra_tendencia"] else "")
                        + "".join(f"⛔ {t}\n" for t in (aval.get("guarda", {}).get("travas") or []))
                        + alerta["hora"]
                    )
                    enviar_telegram(msg_tg)
                    ultimo_candle_alertado[tf] = ts

        except Exception as e:
            await broadcast({"tipo": "erro", "msg": str(e)})

        await asyncio.sleep(POLL_SEGUNDOS)


@app.on_event("startup")
async def startup():
    asyncio.create_task(loop_monitoramento())


# ==========================================================================
#  ROTAS REST
# ==========================================================================

@app.get("/api/status")
def status():
    return {
        "symbol": estado["symbol"],
        "monitorando": estado["monitorando"],
        "ultimo_update": estado["ultimo_update"],
        "contexto": estado["contexto"],
        "timeframes": {
            tf: {
                "aval": v["aval"],
                "ts": v["ts"],
            }
            for tf, v in estado["timeframes"].items()
        },
        "total_alertas": len(historico_alertas),
    }


@app.get("/api/candles/{tf}")
def candles(tf: str):
    if tf not in estado["timeframes"]:
        return {"candles": []}
    return {"candles": estado["timeframes"][tf]["candles"]}


@app.get("/api/alertas")
def alertas():
    return {"alertas": list(historico_alertas)}


@app.get("/api/forward")
def forward_stats():
    try:
        return forward.estatisticas()
    except Exception as e:
        return {"erro": str(e)}


@app.get("/api/paper")
def paper_estado():
    try:
        return paper.estado()
    except Exception as e:
        return {"erro": str(e)}


class PaperConfig(BaseModel):
    ativo: Optional[bool] = None
    tf: Optional[str] = None
    estagio_min: Optional[int] = None
    rr: Optional[float] = None
    risco_pct: Optional[float] = None
    hora_ini: Optional[int] = None
    hora_fim: Optional[int] = None
    respeitar_horario: Optional[bool] = None
    breakeven: Optional[bool] = None


@app.post("/api/paper/config")
def paper_set_config(cfg: PaperConfig):
    try:
        novo = {k: v for k, v in cfg.dict().items() if v is not None}
        return paper.set_config(novo)
    except Exception as e:
        return {"erro": str(e)}


@app.post("/api/paper/reset")
def paper_reset():
    try:
        paper.resetar()
        return paper.estado()
    except Exception as e:
        return {"erro": str(e)}


@app.post("/api/pausar")
def pausar():
    estado["monitorando"] = False
    return {"ok": True, "monitorando": False}


@app.post("/api/retomar")
def retomar():
    estado["monitorando"] = True
    return {"ok": True, "monitorando": True}


@app.post("/api/par/{symbol}")
def trocar_par(symbol: str):
    estado["symbol"] = symbol.upper().replace("-", "/")
    for tf in INTERVALOS_OPERACIONAIS + [INTERVALO_CONTEXTO]:
        ultimo_fetch[tf] = 0.0
    for tf in INTERVALOS_OPERACIONAIS:
        ultimo_candle_alertado[tf] = None
    estado["timeframes"] = {}
    return {"ok": True, "symbol": estado["symbol"]}


# ==========================================================================
#  BACKTEST
# ==========================================================================

class BacktestParams(BaseModel):
    symbol: Optional[str] = None
    interval: str = "5min"
    estagio_min: int = 2
    rr: float = 1.5
    risco_pct: float = 1.0
    hora_ini: Optional[int] = None
    hora_fim: Optional[int] = None


@app.post("/api/backtest")
def backtest(params: BacktestParams):
    symbol = params.symbol or estado["symbol"]
    try:
        return rodar_backtest(
            symbol=symbol,
            interval=params.interval,
            estagio_min=params.estagio_min,
            rr=params.rr,
            risco_pct=params.risco_pct,
            hora_ini=params.hora_ini,
            hora_fim=params.hora_fim,
        )
    except Exception as e:
        return {"erro": str(e)}


# ==========================================================================
#  WEBSOCKET
# ==========================================================================

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clientes_ws.append(ws)

    # manda estado atual imediatamente ao conectar
    await ws.send_text(json.dumps({
        "tipo": "init",
        "symbol": estado["symbol"],
        "monitorando": estado["monitorando"],
        "contexto": estado["contexto"],
        "timeframes": {
            tf: {"aval": v["aval"], "ts": v["ts"], "candles": v["candles"]}
            for tf, v in estado["timeframes"].items()
        },
        "alertas": list(historico_alertas),
    }))

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        if ws in clientes_ws:
            clientes_ws.remove(ws)


# ==========================================================================
#  FRONTEND ESTÁTICO
# ==========================================================================

app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
def root():
    return FileResponse("frontend/index.html")
