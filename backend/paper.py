"""Paper trading multi-estrategia — duas estrategias rodando em paralelo,
cada uma com banca propria, posicoes proprias e config propria. Permite
comparar ao vivo qual performa melhor (A/B). Nenhuma ordem real e enviada.

Estrategia A = Didi (Agulhada) — momentum/tendencia (a campea).
Estrategia B = Smart Money (Liquidity Sweep) — reversao na liquidez (como bancos).
"""

import os
import json
import sqlite3
import datetime as dt

import pandas as pd

from backend.config import SPREADS
from backend.engine import sinal_smc

PASTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dados")
DB = os.path.join(PASTA, "paper.db")

BANCA_INICIAL = 10000.0

# base comum de config
_BASE = {
    "ativo": True,
    "tf": "30min",
    "rr": 3.0,
    "risco_pct": 1.0,
    "hora_ini": 8,
    "hora_fim": 13,
    "respeitar_horario": True,
    "breakeven": True,
    "cb_ativo": True,
    "max_trades_dia": 3,
    "perda_max_dia_pct": 3.0,
    "max_posicoes": 3,
}

# As duas estrategias (A e B)
ESTRATEGIAS = {
    "A": {**_BASE, "nome": "Didi (Agulhada)", "tipo": "didi", "estagio_min": 3},
    "B": {**_BASE, "nome": "Smart Money (Sweep)", "tipo": "smc", "lookback": 10},
}

CHAVES_VALIDAS = set(_BASE) | {"nome", "tipo", "estagio_min", "lookback"}

_bloqueio_avisado = {}   # estrategia -> data


def _spread(symbol):
    return SPREADS.get(symbol, 0.00012)


def _conn():
    os.makedirs(PASTA, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS contas (
        estrategia TEXT PRIMARY KEY, banca REAL, config TEXT, criado_em TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS posicoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, estrategia TEXT,
        aberta_em TEXT, ts_candle TEXT, symbol TEXT, tf TEXT,
        direcao TEXT, entrada REAL, sl REAL, sl_original REAL, tp REAL,
        risco_dinheiro REAL, tamanho REAL, breakeven INTEGER DEFAULT 0,
        status TEXT DEFAULT 'aberta',
        fechada_em TEXT, preco_saida REAL, resultado REAL, resultado_r REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS equity (
        id INTEGER PRIMARY KEY AUTOINCREMENT, estrategia TEXT, ts INTEGER, banca REAL)""")
    # garante uma conta por estrategia
    for estr, cfg in ESTRATEGIAS.items():
        row = c.execute("SELECT 1 FROM contas WHERE estrategia=?", (estr,)).fetchone()
        if not row:
            c.execute("INSERT INTO contas (estrategia, banca, config, criado_em) VALUES (?,?,?,?)",
                      (estr, BANCA_INICIAL, json.dumps(cfg), dt.datetime.now().isoformat()))
    c.commit()
    return c


def get_config(estr):
    c = _conn()
    row = c.execute("SELECT config FROM contas WHERE estrategia=?", (estr,)).fetchone()
    c.close()
    base = dict(ESTRATEGIAS[estr])
    if row:
        base.update(json.loads(row[0]))
    return base


def set_config(estr, novo):
    if estr not in ESTRATEGIAS:
        return {"erro": "estrategia invalida"}
    c = _conn()
    cfg = get_config(estr)
    cfg.update({k: v for k, v in novo.items() if k in CHAVES_VALIDAS})
    c.execute("UPDATE contas SET config=? WHERE estrategia=?", (json.dumps(cfg), estr))
    c.commit()
    c.close()
    return cfg


def resetar(estr=None):
    c = _conn()
    alvos = [estr] if estr else list(ESTRATEGIAS)
    for e in alvos:
        c.execute("DELETE FROM posicoes WHERE estrategia=?", (e,))
        c.execute("DELETE FROM equity WHERE estrategia=?", (e,))
        c.execute("UPDATE contas SET banca=? WHERE estrategia=?", (BANCA_INICIAL, e))
    c.commit()
    c.close()


def _banca(c, estr):
    r = c.execute("SELECT banca FROM contas WHERE estrategia=?", (estr,)).fetchone()
    return r[0] if r else BANCA_INICIAL


def _stats_dia(c, estr, symbol=None):
    hoje = dt.date.today().isoformat()
    if symbol:
        trades = c.execute("SELECT COUNT(*) FROM posicoes WHERE estrategia=? AND symbol=? AND substr(aberta_em,1,10)=?",
                           (estr, symbol, hoje)).fetchone()[0]
        res = c.execute("SELECT COALESCE(SUM(resultado),0) FROM posicoes WHERE estrategia=? AND status!='aberta' AND symbol=? AND substr(fechada_em,1,10)=?",
                        (estr, symbol, hoje)).fetchone()[0]
    else:
        trades = c.execute("SELECT COUNT(*) FROM posicoes WHERE estrategia=? AND substr(aberta_em,1,10)=?",
                           (estr, hoje)).fetchone()[0]
        res = c.execute("SELECT COALESCE(SUM(resultado),0) FROM posicoes WHERE estrategia=? AND status!='aberta' AND substr(fechada_em,1,10)=?",
                        (estr, hoje)).fetchone()[0]
    return trades, round(res or 0, 2)


def _circuit_breaker(c, estr, cfg, symbol=None):
    if not cfg.get("cb_ativo"):
        return False, None
    trades, res = _stats_dia(c, estr, symbol)
    banca = _banca(c, estr)
    if trades >= cfg.get("max_trades_dia", 3):
        return True, f"limite de {cfg['max_trades_dia']} trades/dia atingido"
    if res <= -(cfg.get("perda_max_dia_pct", 3.0) / 100.0) * banca:
        return True, f"perda diaria de {cfg['perda_max_dia_pct']}% atingida"
    return False, None


def _sinal_entrada(estr, cfg, aval, df):
    """Devolve {direcao, entrada, sl} conforme o tipo da estrategia, ou None."""
    tipo = cfg.get("tipo", "didi")
    if tipo == "didi":
        direcao = aval.get("direcao")
        adx_val = aval.get("adx_valor") or 0
        if (not direcao or aval.get("estagio", 0) < cfg.get("estagio_min", 3)
                or aval.get("contra_tendencia") or len(df) < 6
                or adx_val < 20):
            return None
        entrada = float(df["close"].iloc[-1])
        if direcao == "compra":
            sl = float(df["low"].iloc[-5:].min())
        else:
            sl = float(df["high"].iloc[-5:].max())
        return {"direcao": direcao, "entrada": entrada, "sl": sl}
    elif tipo == "smc":
        return sinal_smc(df, cfg.get("lookback", 10))
    return None


def _processar_estrategia(c, estr, symbol, tf, aval, df, hora_brt):
    cfg = get_config(estr)
    eventos = []
    if not cfg.get("ativo"):
        return eventos
    spread = _spread(symbol)

    # 1) resolve posicoes abertas desta estrategia/symbol/tf
    abertas = c.execute(
        "SELECT id, ts_candle, direcao, entrada, sl, sl_original, tp, risco_dinheiro, breakeven "
        "FROM posicoes WHERE status='aberta' AND estrategia=? AND symbol=? AND tf=?",
        (estr, symbol, tf)).fetchall()

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
            ganho -= spread
            resultado_r = round(ganho / risco_preco, 2) if risco_preco else 0
            resultado = round(risco_din * resultado_r, 2)
            nova_banca = round(_banca(c, estr) + resultado, 2)
            c.execute("""UPDATE posicoes SET status=?, fechada_em=?, preco_saida=?,
                         resultado=?, resultado_r=? WHERE id=?""",
                      (status, dt.datetime.now().isoformat(), round(saida, 5),
                       resultado, resultado_r, pid))
            c.execute("UPDATE contas SET banca=? WHERE estrategia=?", (nova_banca, estr))
            c.execute("INSERT INTO equity (estrategia, ts, banca) VALUES (?,?,?)",
                      (estr, int(dt.datetime.now().timestamp()), nova_banca))
            eventos.append({"evento": "fechou", "estrategia": estr, "nome": cfg["nome"],
                            "symbol": symbol, "tf": tf, "direcao": direcao,
                            "status": status, "resultado": resultado, "resultado_r": resultado_r,
                            "saida": round(saida, 5), "banca": nova_banca})
    c.commit()

    # 2) abre nova posicao
    dentro_horario = (not cfg["respeitar_horario"]) or (cfg["hora_ini"] <= hora_brt < cfg["hora_fim"])
    tf_certo = (tf == cfg["tf"])
    ja_aberta = c.execute("SELECT 1 FROM posicoes WHERE status='aberta' AND estrategia=? AND symbol=? AND tf=?",
                          (estr, symbol, tf)).fetchone()

    sinal = _sinal_entrada(estr, cfg, aval, df) if (tf_certo and dentro_horario and not ja_aberta) else None
    quer_abrir = sinal is not None

    if quer_abrir:
        total_abertas = c.execute("SELECT COUNT(*) FROM posicoes WHERE status='aberta' AND estrategia=?", (estr,)).fetchone()[0]
        if total_abertas >= cfg.get("max_posicoes", 3):
            quer_abrir = False

    if quer_abrir:
        bloqueado, motivo = _circuit_breaker(c, estr, cfg, symbol)
        if bloqueado:
            quer_abrir = False
            hoje = dt.date.today().isoformat()
            if _bloqueio_avisado.get(estr) != hoje:
                _bloqueio_avisado[estr] = hoje
                eventos.append({"evento": "bloqueado", "estrategia": estr, "nome": cfg["nome"],
                                "symbol": symbol, "tf": tf, "motivo": motivo, "banca": _banca(c, estr)})

    if quer_abrir:
        direcao = sinal["direcao"]
        entrada = sinal["entrada"]
        sl = sinal["sl"]
        risco_preco = (entrada - sl) if direcao == "compra" else (sl - entrada)
        tp = entrada + cfg["rr"] * risco_preco if direcao == "compra" else entrada - cfg["rr"] * risco_preco
        if risco_preco > 0:
            banca = _banca(c, estr)
            risco_din = round(banca * cfg["risco_pct"] / 100.0, 2)
            tamanho = round(risco_din / risco_preco, 2)
            c.execute("""INSERT INTO posicoes
                (estrategia, aberta_em, ts_candle, symbol, tf, direcao, entrada, sl, sl_original, tp,
                 risco_dinheiro, tamanho) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (estr, dt.datetime.now().isoformat(), str(df["datetime"].iloc[-1]),
                 symbol, tf, direcao, round(entrada, 5), round(sl, 5), round(sl, 5), round(tp, 5),
                 risco_din, tamanho))
            c.commit()
            eventos.append({"evento": "abriu", "estrategia": estr, "nome": cfg["nome"],
                            "symbol": symbol, "tf": tf, "direcao": direcao,
                            "entrada": round(entrada, 5), "sl": round(sl, 5), "tp": round(tp, 5),
                            "risco": risco_din})
    return eventos


def processar(symbol, tf, aval, df, hora_brt):
    """Roda as duas estrategias em paralelo neste candle. Devolve lista de eventos."""
    if df is None or not len(df):
        return []
    c = _conn()
    eventos = []
    for estr in ESTRATEGIAS:
        try:
            eventos += _processar_estrategia(c, estr, symbol, tf, aval, df, hora_brt)
        except Exception:
            pass
    c.close()
    return eventos


def _estado_estrategia(c, estr):
    cfg = get_config(estr)
    banca = _banca(c, estr)
    abertas = c.execute(
        "SELECT symbol, tf, direcao, entrada, sl, tp, risco_dinheiro, ts_candle, breakeven "
        "FROM posicoes WHERE status='aberta' AND estrategia=? ORDER BY id DESC", (estr,)).fetchall()
    fechadas = c.execute(
        "SELECT symbol, tf, direcao, entrada, preco_saida, resultado, resultado_r, status, fechada_em, ts_candle "
        "FROM posicoes WHERE status!='aberta' AND estrategia=? ORDER BY id DESC LIMIT 100", (estr,)).fetchall()
    eq = c.execute("SELECT ts, banca FROM equity WHERE estrategia=? ORDER BY id ASC", (estr,)).fetchall()
    trades_hoje, res_hoje = _stats_dia(c, estr)
    cb_bloqueado, cb_motivo = _circuit_breaker(c, estr, cfg)

    abertas = [dict(zip(["symbol", "tf", "direcao", "entrada", "sl", "tp", "risco", "ts_candle", "breakeven"], a)) for a in abertas]
    fechadas = [dict(zip(["symbol", "tf", "direcao", "entrada", "saida", "resultado", "resultado_r", "status", "fechada_em", "ts_candle"], f)) for f in fechadas]
    vit = [f for f in fechadas if f["resultado"] > 0]
    total = len(fechadas)
    equity = [{"time": ts, "value": round(b, 2)} for ts, b in eq]
    if not equity:
        equity = [{"time": int(dt.datetime.now().timestamp()), "value": BANCA_INICIAL}]
    return {
        "estrategia": estr,
        "nome": cfg["nome"],
        "tipo": cfg.get("tipo"),
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


def estado():
    """Estado das duas estrategias. Mantem campos de topo apontando pra A (compat)."""
    c = _conn()
    estr_estados = {e: _estado_estrategia(c, e) for e in ESTRATEGIAS}
    c.close()
    a = estr_estados["A"]
    out = dict(a)              # compat: campos de topo = estrategia A
    out["estrategias"] = estr_estados
    return out
