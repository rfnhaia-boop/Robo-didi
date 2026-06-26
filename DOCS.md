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

## 4. As duas estratégias (paper.py)

Rodam **em paralelo**, cada uma com banca de $10.000 própria, posições e config
independentes. Servem para comparar ao vivo qual performa melhor (A/B).

### Estratégia A — Didi (Agulhada)
Momentum / continuação de tendência (a "campeã" validada no walk-forward).
- Entra no **estágio 3** (agulhada + confluências), a favor da tendência do 1h
- **Filtro ADX ≥ 20** (só tendência real)
- Stop na mínima/máxima dos últimos 5 candles · Alvo por RR

### Estratégia B — Smart Money (Liquidity Sweep)
Reversão na liquidez — como os bancos "caçam stops" do varejo (`engine.sinal_smc`).
- **Compra:** o candle varre a mínima recente (lookback 10) e **fecha de volta acima** dela
- **Venda:** varre a máxima recente e fecha de volta abaixo
- Stop no extremo varrido · Alvo por RR

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
