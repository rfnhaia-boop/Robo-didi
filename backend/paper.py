"""Paper trading — operacao simulada ao vivo.

O robo abre e fecha posicoes VIRTUAIS em tempo real seguindo a config
campea (estagio 3, janela de horario, RR alvo, risco fixo). Nenhuma ordem
real e enviada. Serve para acompanhar como seria a operacao de verdade e
copiar manualmente. Persistido em SQLite (sobrevive a reinicios).
"""

import os
import json
import sqlite3
import datetime as dt

import pandas as pd

from backend.config import SPREADS

PASTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dados")
DB = os.path.join(PASTA, "paper.db")

BANCA_INICIAL = 10000.0

# config campea (validada no walk-forward) — pode ser ajustada via set_config
CONFIG_PADRAO = {
    "ativo": False,          # paper trading ligado/desligado
    "tf": "30min",           # timeframe operado (campeao = 30min)
    "estagio_min": 3,
    "rr": 3.0,
    "risco_pct": 1.0,
    "hora_ini": 8,
    "hora_fim": 13,
    "respeitar_horario": True,
    "breakeven": True,       # move o stop pro zero a zero em +1R
    # circuit breaker — protecao contra dia ruim
    "cb_ativo": True,
    "max_trades_dia": 3,        # nao abre mais que N trades por dia (por instrumento)
    "perda_max_dia_pct": 3.0,   # para o dia se perder X% da banca
    "max_posicoes": 3,          # maximo de posicoes abertas ao mesmo tempo (todos os pares)
}

# avisa o bloqueio do circuit breaker so uma vez por dia
_bloqueio_avisado = None


def _stats_dia(c, symbol=None):
    """Trades abertos hoje e resultado realizado hoje. symbol=None = todos."""
    hoje = dt.date.today().isoformat()
    filtro = "AND symbol=?" if symbol else ""
    args_a = ([symbol, hoje] if symbol else [hoje])
    trades = c.execute(
        f"SELECT COUNT(*) FROM posicoes WHERE substr(aberta_em,1,10)=? {filtro}",
        ([hoje, symbol] if symbol else [hoje])).fetchone()[0]
    res = c.execute(
        f"SELECT COALESCE(SUM(resultado),0) FROM posicoes "
        f"WHERE status!='aberta' AND substr(fechada_em,1,10)=? {filtro}",
        ([hoje, symbol] if symbol else [hoje])).fetchone()[0]
    return trades, round(res or 0, 2)


def _circuit_breaker(c, cfg, symbol=None):
    """Devolve (bloqueado, motivo). Avalia limites do dia."""
    if not cfg.get("cb_ativo"):
        return False, None
    trades, res = _stats_dia(c, symbol)
    banca = _banca(c)
    if trades >= cfg.get("max_trades_dia", 3):
        return True, f"limite de {cfg['max_trades_dia']} trades/dia atingido"
    if res <= -(cfg.get("perda_max_dia_pct", 3.0) / 100.0) * banca:
        return True, f"perda diaria de {cfg['perda_max_dia_pct']}% atingida"
    return False, None


def _spread(symbol):
    return SPREADS.get(symbol, 0.00012)


def _conn():
    os.makedirs(PASTA, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS conta (
        id INTEGER PRIMARY KEY CHECK (id=1),
        banca REAL, config TEXT, criado_em TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS posicoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        aberta_em TEXT, ts_candle TEXT, symbol TEXT, tf TEXT,
        direcao TEXT, entrada REAL, sl REAL, sl_original REAL, tp REAL,
        risco_dinheiro REAL, tamanho REAL, breakeven INTEGER DEFAULT 0,
        status TEXT DEFAULT 'aberta',     -- aberta | alvo | stop | breakeven
        fechada_em TEXT, preco_saida REAL, resultado REAL, resultado_r REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS equity (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, banca REAL)""")
    # migracao leve: garante colunas novas em bancos antigos
    cols = [r[1] for r in c.execute("PRAGMA table_info(posicoes)").fetchall()]
    if "sl_original" not in cols:
        c.execute("ALTER TABLE posicoes ADD COLUMN sl_original REAL")
    if "breakeven" not in cols:
        c.execute("ALTER TABLE posicoes ADD COLUMN breakeven INTEGER DEFAULT 0")
    row = c.execute("SELECT banca, config FROM conta WHERE id=1").fetchone()
    if not row:
        c.execute("INSERT INTO conta (id, banca, config, criado_em) VALUES (1,?,?,?)",
                  (BANCA_INICIAL, json.dumps(CONFIG_PADRAO), dt.datetime.now().isoformat()))
        c.commit()
    return c


def get_config():
    c = _conn()
    cfg = json.loads(c.execute("SELECT config FROM conta WHERE id=1").fetchone()[0])
    c.close()
    # garante chaves novas
    base = dict(CONFIG_PADRAO)
    base.update(cfg)
    return base


def set_config(novo):
    c = _conn()
    cfg = get_config()
    cfg.update({k: v for k, v in novo.items() if k in CONFIG_PADRAO})
    c.execute("UPDATE conta SET config=? WHERE id=1", (json.dumps(cfg),))
    c.commit()
    c.close()
    return cfg


def resetar():
    c = _conn()
    c.execute("DELETE FROM posicoes")
    c.execute("DELETE FROM equity")
    c.execute("UPDATE conta SET banca=? WHERE id=1", (BANCA_INICIAL,))
    c.commit()
    c.close()


def _banca(c):
    return c.execute("SELECT banca FROM conta WHERE id=1").fetchone()[0]


def processar(symbol, tf, aval, df, hora_brt):
    """Chamado a cada candle novo. Resolve posicoes abertas e, se houver
    sinal valido dentro das regras, abre uma posicao virtual.
    Devolve lista de eventos (abriu/fechou) para alertar."""
    cfg = get_config()
    if not cfg["ativo"] or df is None or not len(df):
        return []

    spread = _spread(symbol)
    eventos = []
    c = _conn()

    # 1) resolve posicoes abertas deste symbol/tf
    abertas = c.execute(
        "SELECT id, ts_candle, direcao, entrada, sl, sl_original, tp, risco_dinheiro, breakeven "
        "FROM posicoes WHERE status='aberta' AND symbol=? AND tf=?", (symbol, tf)).fetchall()

    for pid, ts_candle, direcao, entrada, sl, sl_orig, tp, risco_din, be in abertas:
        sl_orig = sl_orig if sl_orig is not None else sl
        depois = df[df["datetime"] > pd.Timestamp(ts_candle)]
        if not len(depois):
            continue
        risco_preco = abs(entrada - sl_orig)
        status, saida = None, None
        for _, row in depois.iterrows():
            if direcao == "compra":
                if row["low"] <= sl:   status, saida = ("breakeven" if be else "stop"), sl; break
                if row["high"] >= tp:  status, saida = "alvo", tp; break
                if cfg["breakeven"] and not be and risco_preco > 0 and row["high"] >= entrada + risco_preco:
                    sl, be = entrada, 1
                    c.execute("UPDATE posicoes SET sl=?, breakeven=1 WHERE id=?", (round(sl, 5), pid))
            else:
                if row["high"] >= sl:  status, saida = ("breakeven" if be else "stop"), sl; break
                if row["low"] <= tp:   status, saida = "alvo", tp; break
                if cfg["breakeven"] and not be and risco_preco > 0 and row["low"] <= entrada - risco_preco:
                    sl, be = entrada, 1
                    c.execute("UPDATE posicoes SET sl=?, breakeven=1 WHERE id=?", (round(sl, 5), pid))
        if status:
            ganho = (saida - entrada) if direcao == "compra" else (entrada - saida)
            ganho -= spread                                   # custo da operacao
            resultado_r = round(ganho / risco_preco, 2) if risco_preco else 0
            resultado = round(risco_din * resultado_r, 2)
            nova_banca = round(_banca(c) + resultado, 2)
            c.execute("""UPDATE posicoes SET status=?, fechada_em=?, preco_saida=?,
                         resultado=?, resultado_r=? WHERE id=?""",
                      (status, dt.datetime.now().isoformat(), round(saida, 5),
                       resultado, resultado_r, pid))
            c.execute("UPDATE conta SET banca=? WHERE id=1", (nova_banca,))
            c.execute("INSERT INTO equity (ts, banca) VALUES (?,?)",
                      (int(dt.datetime.now().timestamp()), nova_banca))
            eventos.append({"evento": "fechou", "symbol": symbol, "tf": tf, "direcao": direcao,
                            "status": status, "resultado": resultado, "resultado_r": resultado_r,
                            "saida": round(saida, 5), "banca": nova_banca})

    c.commit()

    # 2) abre nova posicao — somente no timeframe configurado
    estagio = aval.get("estagio", 0)
    direcao = aval.get("direcao")
    dentro_horario = (not cfg["respeitar_horario"]) or (cfg["hora_ini"] <= hora_brt < cfg["hora_fim"])
    tf_certo = (tf == cfg["tf"])

    ja_aberta = c.execute(
        "SELECT 1 FROM posicoes WHERE status='aberta' AND symbol=? AND tf=?",
        (symbol, tf)).fetchone()

    quer_abrir = (tf_certo and direcao and estagio >= cfg["estagio_min"] and dentro_horario
                  and not ja_aberta and not aval.get("contra_tendencia") and len(df) >= 6)

    # limite global: nao passar de N posicoes abertas no total (todos os instrumentos)
    if quer_abrir:
        total_abertas = c.execute("SELECT COUNT(*) FROM posicoes WHERE status='aberta'").fetchone()[0]
        if total_abertas >= cfg.get("max_posicoes", 3):
            quer_abrir = False

    # circuit breaker: tinha sinal valido, mas o limite do dia esta estourado
    if quer_abrir:
        bloqueado, motivo = _circuit_breaker(c, cfg, symbol)
        if bloqueado:
            quer_abrir = False
            global _bloqueio_avisado
            hoje = dt.date.today().isoformat()
            if _bloqueio_avisado != hoje:
                _bloqueio_avisado = hoje
                eventos.append({"evento": "bloqueado", "symbol": symbol, "tf": tf,
                                "motivo": motivo, "banca": _banca(c)})

    if quer_abrir:
        entrada = float(df["close"].iloc[-1])
        if direcao == "compra":
            sl = float(df["low"].iloc[-5:].min())
            risco_preco = entrada - sl
            tp = entrada + cfg["rr"] * risco_preco
        else:
            sl = float(df["high"].iloc[-5:].max())
            risco_preco = sl - entrada
            tp = entrada - cfg["rr"] * risco_preco
        if risco_preco > 0:
            banca = _banca(c)
            risco_din = round(banca * cfg["risco_pct"] / 100.0, 2)
            tamanho = round(risco_din / risco_preco, 2)
            c.execute("""INSERT INTO posicoes
                (aberta_em, ts_candle, symbol, tf, direcao, entrada, sl, sl_original, tp,
                 risco_dinheiro, tamanho) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (dt.datetime.now().isoformat(), str(df["datetime"].iloc[-1]),
                 symbol, tf, direcao, round(entrada, 5), round(sl, 5), round(sl, 5), round(tp, 5),
                 risco_din, tamanho))
            c.commit()
            eventos.append({
                "evento": "abriu", "symbol": symbol, "tf": tf, "direcao": direcao,
                "entrada": round(entrada, 5), "sl": round(sl, 5), "tp": round(tp, 5),
                "risco": risco_din,
            })
    c.close()
    return eventos


def estado():
    c = _conn()
    banca = _banca(c)
    cfg = get_config()
    abertas = c.execute(
        "SELECT symbol, tf, direcao, entrada, sl, tp, risco_dinheiro, ts_candle, breakeven "
        "FROM posicoes WHERE status='aberta' ORDER BY id DESC").fetchall()
    fechadas = c.execute(
        "SELECT symbol, tf, direcao, entrada, preco_saida, resultado, resultado_r, status, fechada_em "
        "FROM posicoes WHERE status!='aberta' ORDER BY id DESC LIMIT 100").fetchall()
    eq = c.execute("SELECT ts, banca FROM equity ORDER BY id ASC").fetchall()
    trades_hoje, res_hoje = _stats_dia(c)
    cb_bloqueado, cb_motivo = _circuit_breaker(c, cfg)
    c.close()

    abertas = [dict(zip(["symbol", "tf", "direcao", "entrada", "sl", "tp", "risco", "ts_candle", "breakeven"], a)) for a in abertas]
    fechadas = [dict(zip(["symbol", "tf", "direcao", "entrada", "saida", "resultado", "resultado_r", "status", "fechada_em"], f)) for f in fechadas]

    vit = [f for f in fechadas if f["resultado"] > 0]
    total = len(fechadas)
    equity = [{"time": ts, "value": round(b, 2)} for ts, b in eq]
    if not equity:
        equity = [{"time": int(dt.datetime.now().timestamp()), "value": BANCA_INICIAL}]
    return {
        "config": cfg,
        "banca_inicial": BANCA_INICIAL,
        "banca": round(banca, 2),
        "retorno_pct": round((banca / BANCA_INICIAL - 1) * 100, 2),
        "abertas": abertas,
        "n_abertas": len(abertas),
        "fechadas": fechadas,
        "total_operacoes": total,
        "vitorias": len(vit),
        "taxa_acerto": round(len(vit) / total * 100, 1) if total else 0,
        "equity": equity,
        "circuit_breaker": {
            "ativo": cfg.get("cb_ativo", False),
            "trades_hoje": trades_hoje,
            "max_trades_dia": cfg.get("max_trades_dia"),
            "resultado_hoje": res_hoje,
            "perda_max_dia_pct": cfg.get("perda_max_dia_pct"),
            "bloqueado": cb_bloqueado,
            "motivo": cb_motivo,
        },
    }
