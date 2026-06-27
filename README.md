# Liquidation Notification Bot

Telegram-бот для уведомлений о крупных ликвидациях на Binance Futures, Bybit и Hyperliquid.

## Быстрый старт

### 1. Установка зависимостей

```bash
pip install -r requirements.txt
```

### 2. Переменные окружения

```bash
export TG_BOT_TOKEN="ваш_токен_бота"
export TG_CHAT_ID="ваш_chat_id"
export MIN_LIQ_USD="100000"   # порог в USD (по умолчанию 100 000)
export PORT="10000"            # HTTP-порт (по умолчанию 10000)
```

### 3. Запуск

```bash
python bot.py
```

## Деплой на Render

### 1. Создай сервис

В [dashboard.render.com](https://dashboard.render.com) → **New → Web Service** → подключи репозиторий.

> Важно выбрать именно **Web Service**, а не Background Worker — тогда Render будет использовать HTTP health check и не будет перезапускать сервис без причины.

### 2. Настройки сборки

| Поле | Значение |
|------|----------|
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python bot.py` |
| **Health Check Path** | `/ping` |

### 3. Переменные окружения

Перейди в **Dashboard → твой сервис → Environment → Add Environment Variable** и добавь:

| Переменная | Описание | Обязательно |
|------------|----------|-------------|
| `TG_BOT_TOKEN` | Токен бота от @BotFather | ✅ |
| `TG_CHAT_ID` | ID чата или канала | ✅ |
| `MIN_LIQ_USD` | Порог ликвидации в USD (по умолчанию `100000`) | ❌ |

> `PORT` прописывать не нужно — Render подставляет его автоматически.

После добавления переменных Render автоматически перезапустит сервис.

---

## Конфигурация монет

В `config.py` — списки торговых пар для каждой биржи:

```python
BINANCE_SYMBOLS   = ["NEARUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
BYBIT_SYMBOLS     = ["NEARUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
HYPERLIQUID_COINS = ["NEAR", "BTC", "ETH", "SOL"]
```

## HTTP эндпоинты

| Путь      | Описание                        |
|-----------|---------------------------------|
| `/ping`   | Health check → `OK`             |
| `/health` | Health check → `OK`             |
| `/test`   | Запустить симуляцию ликвидаций  |

## Исправленные баги

### Binance
- **FIX 1:** Неправильный fallback цены — `float(ap or p)` где `ap='0'` (непустая строка) всегда возвращает `0.0`. Исправлено через `safe_float()`.
- **FIX 2:** Инверсия сторон — `S='SELL'` означает что биржа _продала_ лонг-позицию = ликвидирован **Long**, а не Short.

### Hyperliquid
- **FIX 3:** Неправильный канал — подписка шла на `trades` вместо `liquidations`. Канал `liquidations` специализированный и надёжный.
- **FIX 4:** Разные форматы сообщений — `liquidations` использует поля `liqPrice`, `szi`, `user`, а не `px`/`sz`/`liquidation{}`.
- **FIX 5:** Бинарные WS-пинги (`ping_interval`) ломают соединение с HL — теперь используются text-пинги `{"method": "ping"}`.
- **FIX 6:** Направление ликвидации — определяется знаком `szi`: отрицательный = Long позиция.

### Общее
- **FIX 7:** Инверсия эмодзи — `🔴` для Long ликвидации, `🟢` для Short ликвидации.
