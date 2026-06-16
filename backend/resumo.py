"""Resumo do dia — consolida os sinais disparados e o desempenho do paper
trading do dia corrente. Usado tanto no painel (/api/resumo) quanto no
envio automatico do Telegram as 18:30 (BRT)."""

import datetime as dt


def montar_resumo(historico_alertas, paper_estado, symbol, contexto=None):
    hoje = dt.date.today()

    # ---- sinais do dia ----
    sinais_hoje = []
    for a in historico_alertas:
        ts = a.get("timestamp")
        if not ts:
            continue
        try:
            if dt.datetime.fromtimestamp(ts).date() == hoje:
                sinais_hoje.append(a)
        except Exception:
            pass

    fortes = [a for a in sinais_hoje if a.get("estagio", 0) >= 3]
    compras = [a for a in sinais_hoje if a.get("direcao") == "compra"]
    vendas = [a for a in sinais_hoje if a.get("direcao") == "venda"]

    # ---- desempenho do paper no dia ----
    fechadas = (paper_estado or {}).get("fechadas", [])
    paper_hoje = []
    for f in fechadas:
        fe = f.get("fechada_em")
        if not fe:
            continue
        try:
            if dt.datetime.fromisoformat(fe).date() == hoje:
                paper_hoje.append(f)
        except Exception:
            pass

    vit = [f for f in paper_hoje if f.get("resultado", 0) > 0]
    der = [f for f in paper_hoje if f.get("resultado", 0) <= 0]
    resultado_dia = round(sum(f.get("resultado", 0) for f in paper_hoje), 2)

    cfg = (paper_estado or {}).get("config", {})

    return {
        "data": hoje.strftime("%d/%m/%Y"),
        "symbol": symbol,
        "contexto": (contexto or {}).get("direcao"),
        "sinais": {
            "total": len(sinais_hoje),
            "fortes": len(fortes),
            "compras": len(compras),
            "vendas": len(vendas),
            "lista": [
                {"hora": a.get("hora"), "tf": a.get("tf"),
                 "direcao": a.get("direcao"), "estagio": a.get("estagio"),
                 "nome": a.get("nome"), "preco": a.get("preco")}
                for a in sinais_hoje[:20]
            ],
        },
        "paper": {
            "ativo": cfg.get("ativo", False),
            "operacoes": len(paper_hoje),
            "vitorias": len(vit),
            "derrotas": len(der),
            "taxa_acerto": round(len(vit) / len(paper_hoje) * 100, 1) if paper_hoje else 0,
            "resultado_dia": resultado_dia,
            "banca": (paper_estado or {}).get("banca", 0),
            "retorno_total": (paper_estado or {}).get("retorno_pct", 0),
        },
    }


def formatar_telegram(r):
    """Monta a mensagem de texto do resumo para enviar no Telegram."""
    s = r["sinais"]
    p = r["paper"]
    linhas = [
        f"\U0001F4CA <b>RESUMO DO DIA</b> — {r['symbol']} | {r['data']}",
        "",
        f"\U0001F4E1 <b>Sinais:</b> {s['total']} no total "
        f"({s['fortes']} fortes • {s['compras']}\U0001F7E2 / {s['vendas']}\U0001F534)",
    ]

    if p["ativo"]:
        if p["operacoes"]:
            ico = "✅" if p["resultado_dia"] >= 0 else "❌"
            sinal = "+" if p["resultado_dia"] >= 0 else ""
            linhas += [
                "",
                f"\U0001F4BC <b>Paper trading:</b> {p['operacoes']} operacoes "
                f"({p['vitorias']}V / {p['derrotas']}D • {p['taxa_acerto']}% acerto)",
                f"{ico} Resultado do dia: {sinal}${p['resultado_dia']}",
                f"\U0001F4B0 Banca: ${p['banca']} ({'+' if p['retorno_total']>=0 else ''}{p['retorno_total']}% total)",
            ]
        else:
            linhas += ["", "\U0001F4BC <b>Paper trading:</b> nenhuma operacao hoje "
                       "(setup nao apareceu dentro das regras)"]
    else:
        linhas += ["", "\U0001F4BC Paper trading desligado"]

    return "\n".join(linhas)
