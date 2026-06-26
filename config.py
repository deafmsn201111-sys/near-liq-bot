import os

TELEGRAM_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.environ.get("TG_CHAT_ID", "")

# ─── Монеты для мониторинга ─────────────────────────────────
# Добавляйте любые тикеры. Для каждой биржи символы строятся автоматически.
WATCH_COINS = ["NEAR", "BTC", "ETH"]

# ─── Фильтр ─────────────────────────────────────────────────
MIN_LIQ_USD = float(os.environ.get("MIN_LIQ_USD", "50000"))

# ─── Binance (combined stream) ──────────────────────────────
_binance_streams = "/".join(f"{c.lower()}usdt@forceOrder" for c in WATCH_COINS)
BINANCE_WS_URL = f"wss://fstream.binance.com/stream?streams={_binance_streams}"

# ─── Bybit ──────────────────────────────────────────────────
BYBIT_WS_URL  = "wss://stream.bybit.com/v5/public/linear"
BYBIT_TOPICS  = [f"allLiquidation.{c}USDT" for c in WATCH_COINS]

# ─── Hyperliquid ────────────────────────────────────────────
HYPERLIQUID_WS_URL = "wss://api.hyperliquid.xyz/ws"
HYPERLIQUID_COINS  = WATCH_COINS

# ─── Прочее ─────────────────────────────────────────────────
MIN_MSG_INTERVAL = 3
HTTP_PORT = int(os.environ.get("PORT", 10000))
