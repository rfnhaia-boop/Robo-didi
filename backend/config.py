import os

TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY", "b8a2f2f6f54a47cdab5b8b314da1f9cd")

# Credenciais do Telegram vem de variaveis de ambiente (nao versionar segredos
# no repositorio publico). Quando ambas estao setadas, o alerta liga sozinho.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
ALERTA_TELEGRAM    = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
ALERTA_PC          = True

SYMBOL                   = "EUR/BRL"
INTERVALOS_OPERACIONAIS  = ["5min", "15min", "30min"]
INTERVALO_CONTEXTO       = "1h"
ESTAGIO_MINIMO_ALERTA    = 1

DIDI  = dict(curta=3, media=8, longa=20)
BB    = dict(periodo=20, desvios=2.0)
TRIX  = dict(periodo=9, sinal=9)
ESTOC = dict(k=14, suav_k=3, d=3)

BW_LOOKBACK  = 50
BW_PERCENTIL = 30
OUTPUTSIZE   = 200
POLL_SEGUNDOS = 30
