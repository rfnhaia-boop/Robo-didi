"""Otimizador: roda o backtest em todas as combinacoes de parametros
e ranqueia priorizando consistencia (drawdown baixo) sobre retorno bruto.

Uso:  python -m backend.otimizador EUR/BRL
"""

import sys
import itertools

from backend.engine import buscar_candles, montar_indicadores
from backend.backtest import rodar_backtest

INTERVALOS = ["5min", "15min", "30min", "1h"]
ESTAGIOS   = [2, 3]
RRS        = [1.5, 2.0, 2.5, 3.0]
HORARIOS   = [(None, None), (9, 12), (8, 13), (9, 17)]
BREAKEVENS = [False, True]

MIN_TRADES = 12  # menos que isso e estatistica de loteria


def otimizar(symbol):
    # baixa cada timeframe UMA vez (4 chamadas de API no total)
    dados = {}
    for tf in INTERVALOS:
        try:
            df = buscar_candles(symbol, tf, outputsize=5000)
            dados[tf] = montar_indicadores(df)
            print(f"[dados] {symbol} {tf}: {len(df)} candles")
        except Exception as e:
            print(f"[dados] {symbol} {tf}: ERRO {e}")

    resultados = []
    combos = list(itertools.product(INTERVALOS, ESTAGIOS, RRS, HORARIOS, BREAKEVENS))
    print(f"\nRodando {len(combos)} combinacoes...\n")

    for tf, est, rr, (h1, h2), be in combos:
        if tf not in dados:
            continue
        try:
            r = rodar_backtest(symbol, tf, estagio_min=est, rr=rr, risco_pct=1.0,
                               hora_ini=h1, hora_fim=h2, breakeven=be,
                               df_pronto=dados[tf])
        except Exception:
            continue
        if r["trades"] < MIN_TRADES:
            continue
        dd = max(r["drawdown_max_pct"], 0.5)  # evita divisao por ~zero
        r["score"] = round(r["retorno_pct"] / dd, 2)  # retorno por unidade de risco
        r["config"] = {"tf": tf, "estagio": est, "rr": rr,
                       "horario": f"{h1}-{h2}h" if h1 is not None else "livre",
                       "breakeven": be}
        resultados.append(r)

    resultados.sort(key=lambda x: x["score"], reverse=True)
    return resultados


def imprimir(resultados, top=15):
    print(f"{'#':>3} {'TF':>6} {'Est':>3} {'RR':>4} {'Horario':>8} {'BE':>3} | "
          f"{'Trades':>6} {'Acerto':>7} {'Retorno':>8} {'DD':>6} {'Fator':>6} {'Score':>6}")
    print("-" * 90)
    for i, r in enumerate(resultados[:top], 1):
        c = r["config"]
        print(f"{i:>3} {c['tf']:>6} {c['estagio']:>3} {c['rr']:>4} {c['horario']:>8} "
              f"{'sim' if c['breakeven'] else 'nao':>3} | "
              f"{r['trades']:>6} {r['taxa_acerto']:>6}% {r['retorno_pct']:>7}% "
              f"{r['drawdown_max_pct']:>5}% {str(r['fator_lucro'] or '-'):>6} {r['score']:>6}")


def walk_forward(symbol, frac_treino=0.7, top_n=10):
    """Otimiza nos primeiros 70% dos dados e valida os campeoes
    nos 30% finais que eles nunca viram."""
    dados = {}
    for tf in INTERVALOS:
        try:
            df = buscar_candles(symbol, tf, outputsize=5000)
            dados[tf] = montar_indicadores(df)
            print(f"[dados] {symbol} {tf}: {len(df)} candles")
        except Exception as e:
            print(f"[dados] {symbol} {tf}: ERRO {e}")

    # 1) otimiza no periodo de TREINO
    resultados_treino = []
    for tf, est, rr, (h1, h2), be in itertools.product(INTERVALOS, ESTAGIOS, RRS, HORARIOS, BREAKEVENS):
        if tf not in dados:
            continue
        n = int(len(dados[tf]) * frac_treino)
        treino = dados[tf].iloc[:n].reset_index(drop=True)
        try:
            r = rodar_backtest(symbol, tf, estagio_min=est, rr=rr, risco_pct=1.0,
                               hora_ini=h1, hora_fim=h2, breakeven=be, df_pronto=treino)
        except Exception:
            continue
        if r["trades"] < MIN_TRADES:
            continue
        dd = max(r["drawdown_max_pct"], 0.5)
        r["score"] = round(r["retorno_pct"] / dd, 2)
        r["config"] = {"tf": tf, "estagio": est, "rr": rr, "h1": h1, "h2": h2, "be": be,
                       "horario": f"{h1}-{h2}h" if h1 is not None else "livre", "breakeven": be}
        resultados_treino.append(r)
    resultados_treino.sort(key=lambda x: x["score"], reverse=True)

    # 2) valida os TOP N no periodo de TESTE (nunca visto)
    print(f"\n=== WALK-FORWARD — treino 70% | validacao 30% ===\n")
    print(f"{'#':>3} {'TF':>6} {'Est':>3} {'RR':>4} {'Horario':>8} {'BE':>3} | "
          f"{'TREINO ret/DD':>14} | {'VALIDACAO ret/DD':>17} {'trades':>7} {'veredito':>10}")
    print("-" * 95)
    for i, r in enumerate(resultados_treino[:top_n], 1):
        c = r["config"]
        n = int(len(dados[c["tf"]]) * frac_treino)
        teste = dados[c["tf"]].iloc[n:].reset_index(drop=True)
        try:
            v = rodar_backtest(symbol, c["tf"], estagio_min=c["estagio"], rr=c["rr"],
                               risco_pct=1.0, hora_ini=c["h1"], hora_fim=c["h2"],
                               breakeven=c["be"], df_pronto=teste)
        except Exception:
            continue
        veredito = "SOBREVIVEU" if v["retorno_pct"] > 0 else "morreu"
        print(f"{i:>3} {c['tf']:>6} {c['estagio']:>3} {c['rr']:>4} {c['horario']:>8} "
              f"{'sim' if c['be'] else 'nao':>3} | "
              f"{r['retorno_pct']:>6}%/{r['drawdown_max_pct']:>5}% | "
              f"{v['retorno_pct']:>8}%/{v['drawdown_max_pct']:>5}% {v['trades']:>7} {veredito:>10}")


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "EUR/BRL"
    modo_wf = "--wf" in sys.argv
    if modo_wf:
        print(f"=== WALK-FORWARD — {symbol} ===\n")
        walk_forward(symbol)
    else:
        print(f"=== OTIMIZADOR — {symbol} ===\n")
        res = otimizar(symbol)
        print(f"\n{len(res)} combinacoes validas (>= {MIN_TRADES} trades)\n")
        print("=== TOP 15 por consistencia (retorno / drawdown) ===\n")
        imprimir(res)
