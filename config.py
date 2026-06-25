import os

TELEGRAM_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.environ.get("TG_CHAT_ID", "")
MIN_LIQ_USD = float(os.environ.get("MIN_LIQ_USD", "50000"))

BINANCE_WS_URL = "wss://fstream.binance.com/ws/NEARUSDT@forceOrder"
BINANCE_SYMBOL = "NEARUSDT"

BYBIT_WS_URL   = "wss://stream.bybit.com/v5/public/linear"
BYBIT_TOPIC    = "allLiquidation.NEARUSDT"

HYPERLIQUID_WS_URL = "wss://api.hyperliquid.xyz/ws"
HYPERLIQUID_COIN   = "NEAR"

MIN_MSG_INTERVAL = 3
HTTP_PORT = int(os.environ.get("PORT", 10000))
