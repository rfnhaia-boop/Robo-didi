"""Forward test: registra cada sinal ao vivo e, candles depois, verifica
sozinho se teria batido o alvo ou o stop. Estatistica real, sem viés
de otimizacao. Persistido em SQLite (sobrevive a reinicios).
"""

import os
import sqlite3
import datetime as dt

import pandas as pd

PASTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dados")
DB = os.path.join(PASTA, "forward.db")

RR_PADRAO = 2.5          # alvo = 2.5x o risco (config vencedora do walk-forward)
JANELA_STOP = 5          # stop na min/max dos ultimos N candles
EXPIRA_CANDLES = 100     # sem bater alvo nem stop em N candles -> expira


def _conn():
    os.makedirs(PASTA, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS sinais (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        criado_em TEXT,
        ts_candle TEXT,
        symbol TEXT,
        tf TEXT,
        direcao TEXT,
        estagio INTEGER,
        entrada REAL,
        sl REAL,
        tp REAL,
        status TEXT DEFAULT 'pendente',   -- pendente | alvo | stop | expirou
        resolvido_em TEXT,
        preco_saida REAL,
        resultado_r REAL
    )""")
    return c


def registrar(alerta, df):
    """Registra um sinal de estagio >= 2 com stop e alvo calculados."""
    if alerta["estagio"] < 2 or df is None or len(df) < JANELA_STOP + 1:
        return
    entrada = float(df["close"].iloc[-1])
    if alerta["direcao"] == "compra":
        sl = float(df["low"].iloc[-JANELA_STOP:].min())
        risco = entrada - sl
        if risco <= 0:
            return
        tp = entrada + RR_PADRAO * risco
    else:
        sl = float(df["high"].iloc[-JANELA_STOP:].max())
        risco = sl - entrada
        if risco <= 0:
            return
        tp = entrada - RR_PADRAO * risco

    c = _conn()
    # evita duplicar o mesmo candle/tf/symbol
    ja = c.execute("SELECT 1 FROM sinais WHERE ts_candle=? AND tf=? AND symbol=?",
                   (str(df["datetime"].iloc[-1]), alerta["tf"], alerta["symbol"])).fetchone()
    if not ja:
        c.execute("""INSERT INTO sinais
            (criado_em, ts_candle, symbol, tf, direcao, estagio, entrada, sl, tp)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (dt.datetime.now().isoformat(), str(df["datetime"].iloc[-1]),
             alerta["symbol"], alerta["tf"], alerta["direcao"], alerta["estagio"],
             round(entrada, 5), round(sl, 5), round(tp, 5)))
        c.commit()
    c.close()


def verificar(symbol, tf, df):
    """Percorre sinais pendentes deste par/tf e resolve com os candles novos."""
    if df is None or not len(df):
        return
    c = _conn()
    pendentes = c.execute(
        "SELECT id, ts_candle, direcao, entrada, sl, tp FROM sinais "
        "WHERE status='pendente' AND symbol=? AND tf=?", (symbol, tf)).fetchall()

    for sid, ts_candle, direcao, entrada, sl, tp in pendentes:
        ts0 = pd.Timestamp(ts_candle)
        depois = df[df["datetime"] > ts0]
        if not len(depois):
            continue

        status, saida = None, None
        for _, row in depois.iterrows():
            if direcao == "compra":
                if row["low"] <= sl:
                    status, saida = "stop", sl
                    break
                if row["high"] >= tp:
                    status, saida = "alvo", tp
                    break
            else:
                if row["high"] >= sl:
                    status, saida = "stop", sl
                    break
                if row["low"] <= tp:
                    status, saida = "alvo", tp
                    break

        if status is None and len(depois) >= EXPIRA_CANDLES:
            status, saida = "expirou", float(depois["close"].iloc[-1])

        if status:
            risco = abs(entrada - sl)
            ganho = (saida - entrada) if direcao == "compra" else (entrada - saida)
            resultado_r = round(ganho / risco, 2) if risco > 0 else 0
            c.execute("""UPDATE sinais SET status=?, resolvido_em=?,
                         preco_saida=?, resultado_r=? WHERE id=?""",
                      (status, dt.datetime.now().isoformat(),
                       round(saida, 5), resultado_r, sid))
    c.commit()
    c.close()


def estatisticas():
    c = _conn()
    linhas = c.execute(
        "SELECT criado_em, ts_candle, symbol, tf, direcao, estagio, entrada, sl, tp, "
        "status, preco_saida, resultado_r FROM sinais ORDER BY id DESC LIMIT 200").fetchall()
    c.close()

    sinais = [dict(zip(
        ["criado_em", "ts_candle", "symbol", "tf", "direcao", "estagio",
         "entrada", "sl", "tp", "status", "preco_saida", "resultado_r"], l)) for l in linhas]

    resolvidos = [s for s in sinais if s["status"] in ("alvo", "stop", "expirou")]
    alvos = sum(1 for s in resolvidos if s["status"] == "alvo")
    stops = sum(1 for s in resolvidos if s["status"] == "stop")
    total_r = round(sum(s["resultado_r"] or 0 for s in resolvidos), 2)

    return {
        "total_sinais": len(sinais),
        "pendentes": sum(1 for s in sinais if s["status"] == "pendente"),
        "resolvidos": len(resolvidos),
        "alvos": alvos,
        "stops": stops,
        "expirados": len(resolvidos) - alvos - stops,
        "taxa_acerto": round(alvos / len(resolvidos) * 100, 1) if resolvidos else 0,
        "resultado_r_total": total_r,
        "rr_usado": RR_PADRAO,
        "sinais": sinais[:50],
    }
