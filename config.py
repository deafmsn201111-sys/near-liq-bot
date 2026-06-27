import os

# Telegram конфигурация
TELEGRAM_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.environ.get("TG_CHAT_ID", "")

# Общий порог ликвидации по умолчанию (используется как резервный)
MIN_LIQ_USD = float(os.environ.get("MIN_LIQ_USD", "100000"))

# Кастомные пороги ликвидаций для каждой биржи (если не заданы, берут значение MIN_LIQ_USD)
MIN_LIQ_BINANCE = float(os.environ.get("MIN_LIQ_BINANCE", MIN_LIQ_USD))
MIN_LIQ_BYBIT = float(os.environ.get("MIN_LIQ_BYBIT", MIN_LIQ_USD))
MIN_LIQ_HYPERLIQUID = float(os.environ.get("MIN_LIQ_HYPERLIQUID", MIN_LIQ_USD))

# ─── Динамическое управление тикерами через Environment ───
# Если переменные не заданы на Render, используются списки по умолчанию

# Для Binance (ввод через запятую, например: NEARUSDT,BTCUSDT,ETHUSDT)
binance_raw = os.environ.get("BINANCE_SYMBOLS", "NEARUSDT,BTCUSDT,ETHUSDT,SOLUSDT")
BINANCE_SYMBOLS = [s.strip().upper() for s in binance_raw.split(",") if s.strip()]

# Для Bybit (ввод через запятую, например: NEARUSDT,BTCUSDT,ETHUSDT)
bybit_raw = os.environ.get("BYBIT_SYMBOLS", "NEARUSDT,BTCUSDT,ETHUSDT,SOLUSDT")
BYBIT_SYMBOLS = [s.strip().upper() for s in bybit_raw.split(",") if s.strip()]

# Для Hyperliquid (ввод базовых имен через запятую, например: NEAR,BTC,ETH)
hl_raw = os.environ.get("HYPERLIQUID_COINS", "NEAR,BTC,ETH,SOL")
HYPERLIQUID_COINS = [c.strip().upper() for c in hl_raw.split(",") if c.strip()]


# Ограничение частоты отправки сообщений в Telegram (в секундах)
MIN_MSG_INTERVAL = 3

# Порт для веб-сервера (Render передает его автоматически в переменную PORT)
HTTP_PORT = int(os.environ.get("PORT", 10000))
