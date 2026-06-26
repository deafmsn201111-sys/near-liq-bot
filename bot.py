#!/usr/bin/env python3
import os, sys, json, time, threading, logging, signal
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import websocket
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, MIN_LIQ_USD,
    BINANCE_SYMBOLS, BYBIT_SYMBOLS, HYPERLIQUID_COINS,
    MIN_MSG_INTERVAL, HTTP_PORT,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-16s | %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("bot.log", encoding="utf-8")],
)
logger = logging.getLogger("CORE")

# ─── HTTP Server + Test Endpoint ────────────────────────────
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/ping", "/health"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK - NEAR Liq Bot is running")
        elif self.path == "/test":
            # ── СИМУЛЯЦИЯ ЛИКВИДАЦИЙ ──
            results = run_test_simulation()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = "<html><body><h2>🧪 Тест ликвидаций</h2><pre>" + results + "</pre>"
            html += "<p><a href='/test'>Запустить снова</a> | <a href='/ping'>Ping</a></p></body></html>"
            self.wfile.write(html.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, fmt, *args):
        pass

def start_http_server():
    server = HTTPServer(("0.0.0.0", HTTP_PORT), PingHandler)
    logger.info(f"HTTP сервер на порту {HTTP_PORT} (роуты: /ping, /test)")
    server.serve_forever()

# ─── Test Simulation ────────────────────────────────────────
def run_test_simulation():
    """Симулирует ликвидации от всех бирж и отправляет в Telegram."""
    log = []
    log.append("═══════════════════════════════════")
    log.append("🧪 Запуск симуляции ликвидаций")
    log.append(f"   Порог MIN_LIQ_USD: ${MIN_LIQ_USD:,.0f}")
    log.append(f"   TG_CHAT_ID: {TELEGRAM_CHAT_ID}")
    log.append("═══════════════════════════════════\n")

    # ── ТЕСТ 1: Bybit NEAR — Лонг ──
    log.append("【ТЕСТ 1】Bybit NEAR Лонг-ликвидация $161,250")
    msg_bybit_near = json.dumps({
        "topic": "allLiquidation.NEARUSDT",
        "type": "snapshot",
        "data": [{
            "symbol": "NEARUSDT",
            "side": "Sell",
            "size": "75000",
            "price": "2.15",
            "updatedTime": int(time.time()*1000)
        }]
    })
    try:
        m = BybitMonitor()
        m.on_message(None, msg_bybit_near)
        log.append("  → отправлено в обработчик BybitMonitor")
    except Exception as e:
        log.append(f"  → ОШИБКА: {e}")
    time.sleep(4)

    # ── ТЕСТ 2: Bybit BTC — Шорт ──
    log.append("\n【ТЕСТ 2】Bybit BTC Шорт-ликвидация $234,500")
    msg_bybit_btc = json.dumps({
        "topic": "allLiquidation.BTCUSDT",
        "type": "snapshot",
        "data": [{
            "symbol": "BTCUSDT",
            "side": "Buy",
            "size": "3.5",
            "price": "67000",
            "updatedTime": int(time.time()*1000)
        }]
    })
    try:
        m = BybitMonitor()
        m.on_message(None, msg_bybit_btc)
        log.append("  → отправлено в обработчик BybitMonitor")
    except Exception as e:
        log.append(f"  → ОШИБКА: {e}")
    time.sleep(4)

    # ── ТЕСТ 3: Binance NEAR — Лонг ──
    log.append("\n【ТЕСТ 3】Binance NEAR Лонг-ликвидация $107,250")
    msg_binance = json.dumps({
        "e": "forceOrder",
        "E": int(time.time()*1000),
        "o": {
            "s": "NEARUSDT",
            "S": "SELL",
            "o": "LIMIT",
            "f": "IOC",
            "q": "50000",
            "p": "2.15",
            "ap": "2.145",
            "X": "FILLED",
            "l": "50000",
            "z": "107250",
            "T": int(time.time()*1000)
        }
    })
    try:
        m = BinanceMonitor()
        m.on_message(None, msg_binance)
        log.append("  → отправлено в обработчик BinanceMonitor")
    except Exception as e:
        log.append(f"  → ОШИБКА: {e}")
    time.sleep(4)

    # ── ТЕСТ 4: Hyperliquid BTC — Лонг ──
    log.append("\n【ТЕСТ 4】Hyperliquid BTC Ликвидация $335,000")
    msg_hl = json.dumps({
        "channel": "trades",
        "data": {
            "coin": "BTC",
            "trades": [{
                "coin": "BTC",
                "side": "S",
                "px": "67000",
                "sz": "5.0",
                "time": int(time.time()*1000),
                "hash": "0xtest123",
                "tid": 99999,
                "liquidation": {
                    "liquidatedUser": "0xtestuser123456789",
                    "markPx": "67000"
                }
            }]
        }
    })
    try:
        m = HyperliquidMonitor()
        m.on_message(None, msg_hl)
        log.append("  → отправлено в обработчик HyperliquidMonitor")
    except Exception as e:
        log.append(f"  → ОШИБКА: {e}")
    time.sleep(4)

    # ── ТЕСТ 5: Прямая отправка ──
    log.append("\n【ТЕСТ 5】Прямая отправка в Telegram")
    try:
        msg = format_liq_msg("TEST", "BTCUSDT", "SELL", 67000.0, 3.5, 234500.0)
        result = send_telegram(msg)
        log.append(f"  → send_telegram вернул: {result}")
    except Exception as e:
        log.append(f"  → ОШИБКА: {e}")

    log.append("\n═══════════════════════════════════")
    log.append("✅ Симуляция завершена.")
    log.append(f"   Проверь Telegram-канал: {TELEGRAM_CHAT_ID}")
    log.append(f"   Должно прийти 5 сообщений")
    log.append("═══════════════════════════════════")

    return "\n".join(log)

# ─── Telegram ───────────────────────────────────────────────
_last_msg_time = 0.0
_msg_lock = threading.Lock()

def send_telegram(text):
    global _last_msg_time
    with _msg_lock:
        now = time.time()
        elapsed = now - _last_msg_time
        if elapsed < MIN_MSG_INTERVAL:
            time.sleep(MIN_MSG_INTERVAL - elapsed)
        _last_msg_time = time.time()
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "test":
        logger.warning("TG_BOT_TOKEN не задан — пропуск отправки")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code == 200:
            logger.info("✅ Отправлено в Telegram")
            return True
        logger.error(f"Telegram API {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"Ошибка Telegram: {e}")
    return False

def format_liq_msg(exchange, symbol, side, price, qty, value_usd, extra=""):
    s = side.upper().strip()
    coin = symbol.replace("USDT", "").replace("usdt", "")
    if s in ("SELL", "S"):
        emoji, pos = "🔴", "Лонг"
    elif s in ("BUY", "B"):
        emoji, pos = "🟢", "Шорт"
    else:
        emoji, pos = "⚪", s
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    msg = (f"{emoji} <b>Ликвидация {coin}</b>\n\n"
           f"🏦 <b>Биржа:</b> {exchange}\n"
           f"📊 <b>Позиция:</b> {pos}\n"
           f"💰 <b>Объём:</b> ${value_usd:,.2f}\n"
           f"📈 <b>Кол-во:</b> {qty:,.2f} {coin}\n"
           f"💵 <b>Цена:</b> ${price:,.4f}\n"
           f"⏰ <b>Время:</b> {now}")
    if extra:
        msg += f"\n{extra}"
    if value_usd >= 500000:
        msg += "\n\n🔥🔥🔥 <b>ОЧЕНЬ КРУПНАЯ!</b>"
    elif value_usd >= 200000:
        msg += "\n\n🔥 <b>Крупная ликвидация</b>"
    return msg

# ─── Base Monitor ───────────────────────────────────────────
class BaseMonitor:
    name = "BASE"
    def __init__(self):
        self.ws = None
        self._delay = 1
        self._running = True
    def on_message(self, ws, message): pass
    def on_open(self, ws): pass
    def on_error(self, ws, error):
        logger.error(f"[{self.name}] WS ошибка: {error}")
    def on_close(self, ws, code, msg):
        logger.warning(f"[{self.name}] WS закрыт ({code}) {msg}")
        if self._running:
            self._reconnect()
    def _reconnect(self):
        d = min(self._delay, 30)
        logger.info(f"[{self.name}] Переподключение через {d} сек...")
        time.sleep(d)
        self._delay = min(self._delay * 2, 30)
        if self._running:
            self.run()
    @property
    def url(self):
        raise NotImplementedError
    def run(self):
        self.ws = websocket.WebSocketApp(self.url, on_message=self.on_message, on_error=self.on_error, on_close=self.on_close, on_open=self.on_open)
        self.ws.run_forever(ping_interval=20, ping_timeout=10)
    def start_thread(self):
        t = threading.Thread(target=self.run, daemon=True, name=self.name)
        t.start()
        return t
    def stop(self):
        self._running = False
        if self.ws:
            self.ws.close()

# ─── Binance ────────────────────────────────────────────────
class BinanceMonitor(BaseMonitor):
    name = "Binance"
    @property
    def url(self):
        streams = "/".join(f"{s.lower()}@forceOrder" for s in BINANCE_SYMBOLS)
        return f"wss://fstream.binance.com/stream?streams={streams}"
    def on_open(self, ws):
        logger.info(f"[{self.name}] ✅ Подключено ({', '.join(BINANCE_SYMBOLS)})")
        self._delay = 1
    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            if "data" in data and "stream" in data:
                inner = data["data"]
            else:
                inner = data
            o = inner.get("o", {})
            if not o: return
            symbol = o.get("s", "")
            side = o.get("S", "")
            qty = float(o.get("q", 0))
            price = float(o.get("ap", o.get("p", 0)))
            value = float(o.get("z", 0))
            if value == 0: value = qty * price
            logger.info(f"[{self.name}] {symbol} {side} {qty:.4f} @ {price:.4f} → ${value:,.2f}")
            if value >= MIN_LIQ_USD:
                send_telegram(format_liq_msg("Binance", symbol, side, price, qty, value))
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка: {e} | {message[:200]}")

# ─── Bybit ──────────────────────────────────────────────────
class BybitMonitor(BaseMonitor):
    name = "Bybit"
    @property
    def url(self):
        return "wss://stream.bybit.com/v5/public/linear"
    def on_open(self, ws):
        topics = [f"allLiquidation.{s}" for s in BYBIT_SYMBOLS]
        logger.info(f"[{self.name}] ✅ Подключено, подписка на {len(topics)} топиков")
        ws.send(json.dumps({"op": "subscribe", "args": topics}))
        self._delay = 1
    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            if "success" in data:
                logger.info(f"[{self.name}] Подписка: {data.get('success')}")
                return
            topic = data.get("topic", "")
            if "liquidation" not in topic.lower(): return
            d = data.get("data", {})
            items = d if isinstance(d, list) else [d]
            for item in items:
                symbol = item.get("symbol", "")
                side = item.get("side", "")
                size = float(item.get("size", 0))
                price = float(item.get("price", 0))
                value = size * price
                logger.info(f"[{self.name}] {symbol} {side} {size:.4f} @ {price:.4f} → ${value:,.2f}")
                if value >= MIN_LIQ_USD:
                    send_telegram(format_liq_msg("Bybit", symbol, side, price, size, value))
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка: {e} | {message[:200]}")

# ─── Hyperliquid ────────────────────────────────────────────
class HyperliquidMonitor(BaseMonitor):
    name = "Hyperliquid"
    @property
    def url(self):
        return "wss://api.hyperliquid.xyz/ws"
    def on_open(self, ws):
        logger.info(f"[{self.name}] ✅ Подключено, подписка на {len(HYPERLIQUID_COINS)} монет")
        for coin in HYPERLIQUID_COINS:
            ws.send(json.dumps({
                "method": "subscribe",
                "subscription": {"type": "trades", "coin": coin}
            }))
            time.sleep(0.2)
        self._delay = 1
    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            channel = data.get("channel", "")
            if channel == "subscriptionResponse":
                logger.info(f"[{self.name}] Подписка подтверждена")
                return
            if channel != "trades": return
            trades_data = data.get("data", {})
            trades = trades_data if isinstance(trades_data, list) else trades_data.get("trades", [])
            if isinstance(trades_data, dict) and "coin" in trades_data:
                trades = [trades_data] if "side" in trades_data else trades_data.get("trades", [trades_data])
            for t in trades:
                coin = t.get("coin", "")
                side = t.get("side", "")
                px = float(t.get("px", 0))
                sz = float(t.get("sz", 0))
                value = px * sz
                liq = t.get("liquidation")
                if not liq: continue
                liq_user = liq.get("liquidatedUser", "?")[:10] + "…"
                logger.info(f"[{self.name}] LIQ {coin} {side} {sz:.4f} @ {px:.4f} → ${value:,.2f} (user={liq_user})")
                if value >= MIN_LIQ_USD:
                    extra = f"🏷 <b>Адрес:</b> <code>{liq_user}</code>"
                    send_telegram(format_liq_msg("Hyperliquid", coin, side, px, sz, value, extra))
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка: {e} | {message[:200]}")

# ─── MAIN ───────────────────────────────────────────────────
monitors = []

def main():
    logger.info("=" * 60)
    logger.info("🚀 Liquidation Bot — запуск")
    logger.info(f"   Монет: Binance={BINANCE_SYMBOLS}, Bybit={BYBIT_SYMBOLS}, HL={HYPERLIQUID_COINS}")
    logger.info(f"   Порог: ${MIN_LIQ_USD:,.0f}")
    logger.info(f"   HTTP порт: {HTTP_PORT} (роуты: /ping, /test)")
    logger.info(f"   Telegram: {TELEGRAM_CHAT_ID}")
    logger.info("=" * 60)
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()
    monitors.extend([BinanceMonitor(), BybitMonitor(), HyperliquidMonitor()])
    for m in monitors:
        m.start_thread()
        time.sleep(1.5)
    logger.info("✅ Все мониторы запущены. Ожидание ликвидаций…")
    def shutdown(signum, frame):
        logger.info(f"Сигнал {signum} — остановка…")
        for m in monitors:
            m.stop()
        sys.exit(0)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
