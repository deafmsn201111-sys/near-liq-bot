#!/usr/bin/env python3
import os, sys, json, time, threading, logging, signal
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import websocket
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, MIN_LIQ_USD,
    BINANCE_WS_URL,
    BYBIT_WS_URL, BYBIT_TOPICS,
    HYPERLIQUID_WS_URL, HYPERLIQUID_COINS,
    MIN_MSG_INTERVAL, HTTP_PORT,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-16s | %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("bot.log", encoding="utf-8")],
)
logger = logging.getLogger("CORE")

# ─── HTTP Keep-Alive ────────────────────────────────────────
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/ping", "/health"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK - NEAR Liq Bot is running")
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, fmt, *args):
        pass

def start_http_server():
    server = HTTPServer(("0.0.0.0", HTTP_PORT), PingHandler)
    logger.info(f"HTTP keep-alive на порту {HTTP_PORT}")
    server.serve_forever()

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
        logger.warning("TG_BOT_TOKEN не задан — пропуск")
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

def format_liq_msg(exchange, coin, side, price, qty, value_usd, extra=""):
    s = side.upper().strip()
    if s in ("SELL", "S"):
        emoji, pos = "🔴", "Лонг"
    elif s in ("BUY", "B"):
        emoji, pos = "🟢", "Шорт"
    else:
        emoji, pos = "⚪", s
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    msg = (f"{emoji} <b>Крупная ликвидация {coin}</b>\n\n"
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
        logger.info(f"[{self.name}] Реконнект через {d}с…")
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

# ─── Binance (combined stream) ──────────────────────────────
class BinanceMonitor(BaseMonitor):
    name = "Binance"
    @property
    def url(self):
        return BINANCE_WS_URL
    def on_open(self, ws):
        logger.info(f"[{self.name}] ✅ Подключено (combined stream)")
        self._delay = 1
    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            # Combined stream: {"stream":"btcusdt@forceOrder","data":{...}}
            payload = data.get("data", data)
            o = payload.get("o", {})
            if not o: return
            symbol = o.get("s", "")         # BTCUSDT
            coin   = symbol.replace("USDT", "").replace("usdt", "")
            side   = o.get("S", "")
            qty    = float(o.get("q", 0))
            price  = float(o.get("ap", o.get("p", 0)))
            value  = float(o.get("z", 0))
            if value == 0: value = qty * price
            logger.info(f"[{self.name}] {symbol} {side} {qty:.4f} @ {price:.4f} → ${value:,.2f}")
            if value >= MIN_LIQ_USD:
                send_telegram(format_liq_msg("Binance", coin, side, price, qty, value))
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка: {e} | {message[:120]}")

# ─── Bybit ──────────────────────────────────────────────────
class BybitMonitor(BaseMonitor):
    name = "Bybit"
    @property
    def url(self):
        return BYBIT_WS_URL
    def on_open(self, ws):
        logger.info(f"[{self.name}] ✅ Подключено, подписка на {len(BYBIT_TOPICS)} тем")
        ws.send(json.dumps({"op": "subscribe", "args": BYBIT_TOPICS}))
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
                coin   = symbol.replace("USDT", "")
                side   = item.get("side", "")
                size   = float(item.get("size", 0))
                price  = float(item.get("price", 0))
                value  = size * price
                logger.info(f"[{self.name}] {symbol} {side} {size:.4f} @ {price:.4f} → ${value:,.2f}")
                if value >= MIN_LIQ_USD:
                    send_telegram(format_liq_msg("Bybit", coin, side, price, size, value))
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка: {e} | {message[:120]}")

# ─── Hyperliquid ────────────────────────────────────────────
class HyperliquidMonitor(BaseMonitor):
    name = "Hyperliquid"
    @property
    def url(self):
        return HYPERLIQUID_WS_URL
    def on_open(self, ws):
        logger.info(f"[{self.name}] ✅ Подключено, подписка на {len(HYPERLIQUID_COINS)} монет")
        for coin in HYPERLIQUID_COINS:
            ws.send(json.dumps({
                "method": "subscribe",
                "subscription": {"type": "trades", "coin": coin}
            }))
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
                trades = [trades_data]
            for t in trades:
                coin  = t.get("coin", "")
                side  = t.get("side", "")
                px    = float(t.get("px", 0))
                sz    = float(t.get("sz", 0))
                value = px * sz
                liq   = t.get("liquidation")
                if not liq: continue
                liq_user = liq.get("liquidatedUser", "?")[:10] + "…"
                logger.info(f"[{self.name}] LIQ {coin} {side} {sz:.4f} @ {px:.4f} → ${value:,.2f} (user={liq_user})")
                if value >= MIN_LIQ_USD:
                    extra = f"🏷 <b>Адрес:</b> <code>{liq_user}</code>"
                    send_telegram(format_liq_msg("Hyperliquid", coin, side, px, sz, value, extra))
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка: {e} | {message[:120]}")

# ─── MAIN ───────────────────────────────────────────────────
monitors = []

def main():
    logger.info("=" * 60)
    logger.info("🚀 NEAR Liquidation Bot — запуск")
    logger.info(f"   Монеты: {', '.join(HYPERLIQUID_COINS)}")
    logger.info(f"   Порог:  ${MIN_LIQ_USD:,.0f}")
    logger.info(f"   HTTP:   порт {HTTP_PORT}")
    logger.info(f"   TG:     {TELEGRAM_CHAT_ID}")
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
