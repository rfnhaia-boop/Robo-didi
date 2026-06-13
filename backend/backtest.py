"""Backtest do setup Didi sobre candles historicos.

Simula: a cada gatilho (estagio >= minimo) entra na abertura do candle
seguinte, com stop na minima/maxima recente e alvo em RR x risco.
Sai por stop, alvo ou agulhada contraria. Risco fixo de X% da banca.
"""

import numpy as np
import pandas as pd

from backend.engine import buscar_candles, montar_indicadores
from backend.config import BW_LOOKBACK, BW_PERCENTIL


def _sinal_no_candle(d, i):
    """Replica a logica do avaliar() para o candle de indice i."""
    dc, dl = d["didi_curta"], d["didi_longa"]
    if np.isnan(dc[i]) or np.isnan(dl[i]) or np.isnan(d["bb_bw"][i]) or i < BW_LOOKBACK + 2:
        return None, 0, False

    agulhada_compra = dc[i - 1] <= dl[i - 1] and dc[i] > dl[i]
    agulhada_venda  = dc[i - 1] >= dl[i - 1] and dc[i] < dl[i]

    dist_atual = abs(dc[i] - dl[i])
    dist_ant   = abs(dc[i - 1] - dl[i - 1])
    convergindo = dist_atual < dist_ant
    std = d["didi_std"][i]
    perto = dist_atual < (std * 0.6 if std and not np.isnan(std) else 1e9)

    limiar = d["bw_limiar"][i]
    em_squeeze = (not np.isnan(limiar)) and d["bb_bw"][i] <= limiar
    expandindo = d["bb_bw"][i] > d["bb_bw"][i - 1]

    trix_compra = d["trix"][i] > d["trix_sinal"][i] and d["trix"][i] > d["trix"][i - 1]
    trix_venda  = d["trix"][i] < d["trix_sinal"][i] and d["trix"][i] < d["trix"][i - 1]
    estoc_compra = d["estoc_k"][i] > d["estoc_d"][i] and d["estoc_k"][i] < 80
    estoc_venda  = d["estoc_k"][i] < d["estoc_d"][i] and d["estoc_k"][i] > 20
    adx_forte = (not np.isnan(d["adx"][i])) and d["adx"][i] > 25 and d["adx"][i] > d["adx"][i - 1]

    direcao, estagio = None, 0
    conf_c = sum([trix_compra, estoc_compra, expandindo, adx_forte])
    conf_v = sum([trix_venda, estoc_venda, expandindo, adx_forte])

    if agulhada_compra:
        direcao, estagio = "compra", (3 if conf_c >= 3 else 2)
    elif agulhada_venda:
        direcao, estagio = "venda", (3 if conf_v >= 3 else 2)
    elif convergindo and perto and em_squeeze:
        direcao = "compra" if dc[i] < dl[i] else "venda"
        estagio = 1

    agulhada = agulhada_compra or agulhada_venda
    return direcao, estagio, agulhada


def rodar_backtest(symbol, interval, estagio_min=2, rr=1.5, risco_pct=1.0,
                   hora_ini=None, hora_fim=None, usar_adx=True, outputsize=5000,
                   breakeven=False, df_pronto=None, spread_preco=None):
    # custo por operacao (ida e volta) em unidades de preco
    if spread_preco is None:
        spread_preco = 0.0005 if "BRL" in symbol.upper() else 0.00012
    if df_pronto is not None:
        df = df_pronto.copy()
    else:
        df = buscar_candles(symbol, interval, outputsize=outputsize)
        df = montar_indicadores(df)

    # series auxiliares pre-computadas
    df["didi_std"] = df["didi_curta"].rolling(20).std()
    df["bw_limiar"] = df["bb_bw"].rolling(BW_LOOKBACK).quantile(BW_PERCENTIL / 100.0)

    d = {col: df[col].values for col in
         ["open", "high", "low", "close", "didi_curta", "didi_longa", "bb_bw",
          "trix", "trix_sinal", "estoc_k", "estoc_d", "adx", "didi_std", "bw_limiar"]}
    datas = df["datetime"].values
    n = len(df)

    banca_inicial = 10000.0
    banca = banca_inicial
    equity = [{"time": int(pd.Timestamp(datas[0]).timestamp()), "value": banca}]
    trades = []
    posicao = None  # dict: dir, entrada, sl, tp, idx_entrada

    def hora_brt(idx):
        return (pd.Timestamp(datas[idx]).hour - 3) % 24

    def dentro_horario(idx):
        if hora_ini is None or hora_fim is None:
            return True
        h = hora_brt(idx)
        if hora_ini <= hora_fim:
            return hora_ini <= h < hora_fim
        return h >= hora_ini or h < hora_fim  # janela cruzando meia-noite

    def fechar(idx, preco_saida, motivo):
        nonlocal banca, posicao
        p = posicao
        risco_preco = abs(p["entrada"] - p.get("sl_original", p["sl"]))
        if risco_preco <= 0:
            posicao = None
            return
        if p["dir"] == "compra":
            resultado_preco = preco_saida - p["entrada"]
        else:
            resultado_preco = p["entrada"] - preco_saida
        resultado_preco -= spread_preco                      # custo da operacao
        resultado_r = resultado_preco / risco_preco          # em multiplos de risco
        resultado_dinheiro = banca * (risco_pct / 100.0) * resultado_r
        banca += resultado_dinheiro
        trades.append({
            "data": str(pd.Timestamp(datas[idx]))[:16],
            "dir": p["dir"],
            "entrada": round(p["entrada"], 5),
            "saida": round(preco_saida, 5),
            "resultado_r": round(resultado_r, 2),
            "resultado": round(resultado_dinheiro, 2),
            "motivo": motivo,
            "estagio": p["estagio"],
        })
        equity.append({"time": int(pd.Timestamp(datas[idx]).timestamp()), "value": round(banca, 2)})
        posicao = None

    for i in range(BW_LOOKBACK + 3, n - 1):
        # gestao da posicao aberta
        if posicao is not None:
            p = posicao
            hi, lo = d["high"][i], d["low"][i]
            if p["dir"] == "compra":
                if lo <= p["sl"]:
                    fechar(i, p["sl"], "breakeven" if p.get("be") else "stop")
                elif hi >= p["tp"]:
                    fechar(i, p["tp"], "alvo")
                elif breakeven and not p.get("be"):
                    risco = p["entrada"] - p["sl_original"]
                    if hi >= p["entrada"] + risco:   # andou +1R -> stop no zero a zero
                        p["sl"] = p["entrada"]
                        p["be"] = True
            else:
                if hi >= p["sl"]:
                    fechar(i, p["sl"], "breakeven" if p.get("be") else "stop")
                elif lo <= p["tp"]:
                    fechar(i, p["tp"], "alvo")
                elif breakeven and not p.get("be"):
                    risco = p["sl_original"] - p["entrada"]
                    if lo <= p["entrada"] - risco:
                        p["sl"] = p["entrada"]
                        p["be"] = True

        direcao, estagio, agulhada = _sinal_no_candle(d, i)

        # agulhada contraria fecha posicao
        if posicao is not None and agulhada and direcao != posicao["dir"]:
            fechar(i, d["close"][i], "agulhada contraria")

        # entrada
        if posicao is None and direcao and estagio >= estagio_min and dentro_horario(i):
            entrada = d["open"][i + 1]
            janela_lo = d["low"][max(0, i - 4):i + 1].min()
            janela_hi = d["high"][max(0, i - 4):i + 1].max()
            if direcao == "compra":
                sl = janela_lo
                if entrada - sl <= 0:
                    continue
                tp = entrada + rr * (entrada - sl)
            else:
                sl = janela_hi
                if sl - entrada <= 0:
                    continue
                tp = entrada - rr * (sl - entrada)
            posicao = {"dir": direcao, "entrada": entrada, "sl": sl, "tp": tp,
                       "sl_original": sl, "idx": i + 1, "estagio": estagio}

    # fecha posicao pendurada no fim
    if posicao is not None:
        fechar(n - 1, d["close"][n - 1], "fim do periodo")

    # estatisticas
    ganhos = [t["resultado"] for t in trades if t["resultado"] > 0]
    perdas = [t["resultado"] for t in trades if t["resultado"] <= 0]
    total = len(trades)
    fator = (sum(ganhos) / abs(sum(perdas))) if perdas and sum(perdas) != 0 else (float('inf') if ganhos else 0)

    # drawdown maximo sobre a curva de equity
    pico, dd_max = banca_inicial, 0.0
    for ponto in equity:
        pico = max(pico, ponto["value"])
        dd = (pico - ponto["value"]) / pico * 100
        dd_max = max(dd_max, dd)

    # comportamento por periodos (mes a mes)
    mensal = {}
    for t in trades:
        mes = t["data"][:7]
        m = mensal.setdefault(mes, {"mes": mes, "trades": 0, "vitorias": 0, "resultado": 0.0})
        m["trades"] += 1
        m["vitorias"] += 1 if t["resultado"] > 0 else 0
        m["resultado"] = round(m["resultado"] + t["resultado"], 2)
    mensal = sorted(mensal.values(), key=lambda x: x["mes"])

    periodo_ini = str(pd.Timestamp(datas[0]))[:16]
    periodo_fim = str(pd.Timestamp(datas[-1]))[:16]

    return {
        "symbol": symbol,
        "interval": interval,
        "periodo": f"{periodo_ini} ate {periodo_fim}",
        "candles": n,
        "parametros": {
            "estagio_min": estagio_min, "rr": rr, "risco_pct": risco_pct,
            "hora_ini": hora_ini, "hora_fim": hora_fim,
        },
        "banca_inicial": banca_inicial,
        "banca_final": round(banca, 2),
        "retorno_pct": round((banca / banca_inicial - 1) * 100, 2),
        "trades": total,
        "vitorias": len(ganhos),
        "derrotas": len(perdas),
        "taxa_acerto": round(len(ganhos) / total * 100, 1) if total else 0,
        "fator_lucro": round(fator, 2) if fator != float('inf') else None,
        "drawdown_max_pct": round(dd_max, 2),
        "spread_usado": spread_preco,
        "mensal": mensal,
        "equity": equity,
        "lista_trades": trades[-100:],
    }
