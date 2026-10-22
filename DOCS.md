# Robo Didi Forex — Documentação Técnica

Sistema de monitoramento e **paper trading** (operação simulada) de Forex/ativos,
rodando 24/7 numa VPS, com painel web, alertas no Telegram e comparação de
estratégias ao vivo. **Nenhuma ordem real é enviada** — é tudo simulação para
validar antes de arriscar dinheiro.

- **Painel:** https://robodidi.newflowsys.cloud
- **Repositório:** https://github.com/rfnhaia-boop/Robo-didi
- **Stack:** Python · FastAPI · WebSocket · SQLite · HTML/JS (TradingView Lightweight Charts)

---

## 1. Arquitetura

```
                 Twelve Data API (preços/candles)
                          │
                          ▼
   ┌──────────────────────────────────────────┐
   │  BACKEND (FastAPI)  — backend/main.py      │
   │  • loop_monitoramento: busca candles dos   │
   │    4 instrumentos a cada ciclo             │
   │  • engine: calcula indicadores e sinais    │
   │  • paper: 2 estrategias simuladas (A e B)  │
   │  • resumo: consolida o dia (18:30)         │
   │  • broadcast via WebSocket + REST          │
   └──────────────────────────────────────────┘
        │ WebSocket /ws + REST /api/*    │ Telegram Bot API
        ▼                                ▼
   FRONTEND (frontend/index.html)    Celular (alertas)
```

Tudo roda num **systemd service** (`robo-didi`) na VPS, atrás de **Nginx + HTTPS**.

---

## 2. Estrutura de arquivos

```
robo-didi-forex/
├── backend/
│   ├── config.py     # instrumentos, spreads, chaves de API, parametros
│   ├── engine.py     # indicadores (Didi, Bollinger, TRIX, etc.) + sinais
│   ├── paper.py      # paper trading das 2 estrategias (A/B)
│   ├── resumo.py     # resumo do dia (sinais + desempenho)
│   ├── backtest.py   # backtesting historico
│   ├── otimizador.py # varredura de configs (walk-forward)
│   ├── forward.py    # forward test (registra sinais e autoavalia)
│   └── main.py       # servidor FastAPI: loop, rotas, WebSocket
├── frontend/
│   └── index.html    # painel single-page (chart, sinais, operacao, config)
├── dados/            # SQLite (paper.db, forward.db) — NAO versionar
├── setup_vps.sh      # instala dependencias + systemd + firewall
├── setup_nginx.sh    # Nginx proxy reverso + HTTPS (Let's Encrypt)
└── requirements.txt
```

---

## 3. Os indicadores (engine.py)

O setup base é a **Agulhada do Didi**, com confirmações:

| Indicador | O que mede | Função |
|---|---|---|
| **Didi Index** | 3 médias (3, 8, 20) | A "agulhada" é quando a curta e a longa cruzam a média ao mesmo tempo |
| **Bollinger** | volatilidade (squeeze) | Detecta compressão antes da explosão |
| **TRIX** | momentum | Confirma a direção da força |
| **Estocástico** | timing | Cruzamento K/D fora das zonas extremas |
| **ADX** | força da tendência | >20-25 = tendência real (filtro) |
| **RSI** | sobrecompra/venda | Evita entrar esticado |
| **ATR** | volatilidade absoluta | Dimensionamento de stop |

**Estágios do sinal:** 1 = VIGIAR · 2 = PREPARAR · 3 = ENTRAR (agulhada confirmada
com 3+ confluências).

---

## 4. Estratégias (paper.py / strategy.py)

> **Atualização out/2026 — o que a pesquisa mostrou.** Em 18 anos de dados (treino 2008-2019, teste cego
> 2020-2026, custos incluídos — scripts em `pesquisa/`), o **Didi sozinho não tem vantagem estatística**
> (Sharpe < 0 no treino, ~0 no teste cego) e **nenhum sistema técnico testado paga o custo em pares de FX**.
> A única família com vantagem nos dois períodos foi **tendência diária em ouro + BTC + ETH**. Por isso a
> **Estratégia T é a única ligada**; A/B/C ficam no código (backtest/testes), suspensas.

### Estratégia T — Tendência Diária (ligada)
- Ativos: **XAU/USD, BTC/USD, ETH/USD**, barras diárias (consulta a API ~1x por dia por ativo).
- **Nota de tendência** em [-1, +1] = média de 5 sinais sem estado: preço vs SMA100, vs SMA200, retorno de 120d,
  retorno de 250d e preço vs meio do canal de 100 dias (`strategy.tendencia_score`).
- **Entrada:** nota ≥ +0,6 (compra) ou ≤ −0,6 (venda) — ou seja, ≥ 4 dos 5 sinais concordam. Entra no fechamento da barra.
- **Stop:** 3×ATR(14), **móvel** (acompanha a máxima/mínima, só no sentido favorável, vale a partir da barra seguinte).
  Sem alvo fixo (alvo simbólico a 10R). **Saída por sinal:** quando a maioria vira (nota ≤ −0,2 na compra).
- **Risco:** 1% da banca por posição, até 3 posições, exposição agregada máx. 4%, circuit breaker de perda diária 4%,
  trava de campanha em −15% de drawdown.
- Backtest com o código de produção (1% de risco por posição, custo 1,5× spread): XAU +2,1%/ano (DD 7,6%), BTC +5,0%/ano
  (DD 5,2%), ETH +4,6%/ano (DD 6,8%), cada um sobre a banca inteira. Custo 4× maior mal muda. **Não inclui swap/funding.**
- `GET /api/tendencia` mostra a nota atual por ativo e as posições abertas.

### Estratégias A (Didi), B (Smart Money) e C (Rompimento 30min) — suspensas
Mantidas para comparação/backtest. A: agulhada confirmada (+1 candle), filtro DI+/DI−, ADX ≥ 20, piso de stop 1×ATR e
scanner multi-par (`candidato_torneio`/`comprometer_torneio`) que só abre o par de maior nota. B: *liquidity sweep*.
C: rompimento de 20 barras no 30min com stop 2×ATR. Para religar qualquer uma: `POST /api/paper/config?estrategia=A`
com `{"ativo": true}` (exige `X-Admin-Token`).

### Regras comuns (config de cada estratégia)
| Parâmetro | Padrão | O que faz |
|---|---|---|
| `tf` | 30min | Timeframe operado |
| `rr` | 3.0 | Relação risco/retorno (alvo = 3× o risco) |
| `risco_pct` | 1.0 | % da banca arriscada por trade |
| `hora_ini`/`hora_fim` | 8–13h | Janela operacional (BRT) |
| `respeitar_horario` | true | Liga/desliga a janela |
| `breakeven` | true | Move o stop pro zero a zero em +1R |
| `cb_ativo` | true | Liga o circuit breaker |
| `max_trades_dia` | 3 | Máx. trades/dia por instrumento |
| `perda_max_dia_pct` | 3.0 | Para o dia se perder X% da banca |
| `max_posicoes` | 3 | Máx. posições abertas ao mesmo tempo |

**Circuit breaker:** proteção que para de abrir trades quando o limite diário
(de trades ou de perda) é atingido — avisa no Telegram e no painel.

---

## 5. Instrumentos e dados

Monitora **4 instrumentos** em paralelo (config `SYMBOLS`), cada um no **30min +
1h (contexto)** para caber no limite da API grátis (~800 chamadas/dia da Twelve Data):

- 💱 **EUR/USD**, **GBP/USD** (moedas)
- 🪙 **XAU/USD** (ouro), **BTC/USD** (bitcoin — opera 24/7)

Cada instrumento tem seu **spread** (custo) em `config.SPREADS`, descontado de
cada operação para os números ficarem honestos.

---

## 6. API REST (principais rotas)

| Rota | Método | O que retorna/faz |
|---|---|---|
| `/api/status` | GET | Estado geral + dados dos instrumentos |
| `/api/tendencia` | GET | Nota de tendência por ativo + posições abertas da estratégia T |
| `/api/candles/{tf}?symbol=` | GET | Candles + indicadores de um instrumento |
| `/api/paper` | GET | Estado das 2 estratégias (banca, trades, posições) |
| `/api/paper/config?estrategia=A\|B` | POST | Altera config de uma estratégia |
| `/api/paper/reset?estrategia=` | POST | Zera a banca de uma estratégia (ou todas) |
| `/api/resumo` | GET | Resumo do dia (sinais + desempenho) |
| `/api/alertas` | GET | Histórico de alertas |
| `/api/forward` | GET | Estatísticas do forward test |
| `/api/backtest` | POST | Roda um backtest com parâmetros |
| `/api/pausar` · `/api/retomar` | POST | Pausa/retoma o monitoramento |
| `/ws` | WebSocket | Stream ao vivo (update, alerta, paper, resumo) |

---

## 7. Deploy e operação na VPS

**Acessar o terminal:** painel hPanel Hostinger → VPS → botão **Terminal**
(o SSH externo é bloqueado pelo firewall da Hostinger).

**Atualizar o código (após um push no GitHub):**
```bash
cd /root/Robo-didi && git pull && systemctl restart robo-didi
```

**Ver se está rodando / logs:**
```bash
systemctl status robo-didi --no-pager
journalctl -u robo-didi -n 50 --no-pager
```

**Recriar o paper do zero** (ex: após mudar o schema do banco):
```bash
cd /root/Robo-didi && rm -f dados/paper.db && systemctl restart robo-didi
```

**Fuso horário da VPS:** deve ser `America/Sao_Paulo` (a janela 8–13h e o resumo
18:30 dependem disso):
```bash
timedatectl set-timezone America/Sao_Paulo
```

---

## 8. Segredos (não vão pro GitHub)

Credenciais ficam em **variáveis de ambiente** do serviço, em
`/etc/systemd/system/robo-didi.service.d/telegram.conf`:
```ini
[Service]
Environment="TELEGRAM_BOT_TOKEN=<token>"
Environment="TELEGRAM_CHAT_ID=<chat_id>"
Environment="TWELVE_DATA_API_KEY=<nova_chave>"
Environment="ADMIN_TOKEN=<senha_longa_e_aleatoria>"
```
O `config.py` lê de `os.environ`. Após editar, recarregue:
```bash
systemctl daemon-reload && systemctl restart robo-didi
```

---

## 9. Filosofia de validação

Os backtests provaram que **afrouxar o Didi (estágio 2, sem janela) só faz perder** —
a config restrita é a única lucrativa. Por isso a regra de ouro: **não afrouxar a
estratégia que funciona; criar uma estratégia nova (B) com lógica diferente para
competir.** O mercado, no paper, decide qual presta — sem achismo, com dinheiro de
mentira, antes de arriscar de verdade.
