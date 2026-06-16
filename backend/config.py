import os

TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY", "b8a2f2f6f54a47cdab5b8b314da1f9cd")

# Credenciais do Telegram vem de variaveis de ambiente (nao versionar segredos
# no repositorio publico). Quando ambas estao setadas, o alerta liga sozinho.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
ALERTA_TELEGRAM    = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
ALERTA_PC          = True

# instrumentos monitorados simultaneamente (2 moedas + 2 ativos).
# Foco no 30min (operado) + 1h (contexto) para caber no limite da API gratis.
SYMBOLS = ["EUR/USD", "GBP/USD", "XAU/USD", "BTC/USD"]
SYMBOL                   = SYMBOLS[0]   # primario (compat / grafico principal)

# spread (custo) por instrumento, em preco absoluto
SPREADS = {
    "EUR/USD": 0.00012,
    "GBP/USD": 0.00015,
    "XAU/USD": 0.30,
    "BTC/USD": 15.0,
    "EUR/BRL": 0.0005,
}

INTERVALOS_OPERACIONAIS  = ["30min"]
INTERVALO_CONTEXTO       = "1h"
ESTAGIO_MINIMO_ALERTA    = 2

DIDI  = dict(curta=3, media=8, longa=20)
BB    = dict(periodo=20, desvios=2.0)
TRIX  = dict(periodo=9, sinal=9)
ESTOC = dict(k=14, suav_k=3, d=3)

BW_LOOKBACK  = 50
BW_PERCENTIL = 30
OUTPUTSIZE   = 200
POLL_SEGUNDOS = 30
