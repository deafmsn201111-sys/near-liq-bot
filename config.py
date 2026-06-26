import os

TELEGRAM_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.environ.get("TG_CHAT_ID", "")
MIN_LIQ_USD = float(os.environ.get("MIN_LIQ_USD", "1000"))

BINANCE_SYMBOLS = ["NEARUSDT", "BTCUSDT"]
BYBIT_SYMBOLS   = ["NEARUSDT", "BTCUSDT"]
HYPERLIQUID_COINS = ["NEAR", "BTC"]

MIN_MSG_INTERVAL = 3
HTTP_PORT = int(os.environ.get("PORT", 10000))
