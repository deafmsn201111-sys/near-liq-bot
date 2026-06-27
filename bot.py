#!/usr/bin/env python3
import os, sys, json, time, threading, logging, signal
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import websocket
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, MIN_LIQ_USD,
    BINANCE_SYMBOLS, BYBIT_SYMBOLS, HYPERLIQUID_COINS,
    MIN_MSG_INTERVAL, HTTP_PORT,
)

# Автоматическая ротация логов (макс. 5 МБ на файл, хранить до 3 архивов)
file_handler = RotatingFileHandler(
    "bot.log", 
    maxBytes=5 * 1024 * 1024, 
    backupCount=3, 
    encoding="utf-8"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-16s | %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout), file_handler],
)
logger = logging.getLogger("CORE")


# Глобальный кэш для сохранения цен маркировки Binance
binance_mark_prices = {}
binance_prices_lock = threading.Lock()


# ─── Helpers ────────────────────────────────────────────────
def fmt_usd(value: float) -> str:
    """Компактное форматирование цен: 1234 -> $1.2k, 1234567 -> $1.2m"""
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}b"
    elif value >= 1_000_000:
        n = value / 1_000_000
        return f"${n:.2f}m" if n < 10 else f"${n:.1f}m"
    elif value >= 1_000:
        n = value / 1_000
        return f"${n:.2f}k" if n < 10 else f"${n:.1f}k"
    else:
        return f"${value:.0f}"


# ─── HTTP Server + Test Endpoint ────────────────────────────
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/ping", "/health"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK - Liq Bot is running")
        elif self.path == "/test":
            results = run_test_simulation()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = "<html><body><h2>Тест ликвидаций</h2><pre>" + results + "</pre>"
            html += "<p><a href='/test'>Повторить</a> | <a href='/ping'>Ping</a></p></body></html>"
            self.wfile.write(html.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass


def start_http_server():
    server = HTTPServer(("0.0.0.0", HTTP_PORT), PingHandler)
    logger.info(f"HTTP сервер запущен на порту {HTTP_PORT}")
    server.serve_forever()


# ─── Test Simulation ────────────────────────────────────────
def run_test_simulation():
    log = []
    log.append("═══════════════════════════════════")
    log.append("Запуск симуляции ликвидаций")
    log.append(f"   Порог: {fmt_usd(MIN_LIQ_USD)}")
    log.append(f"   TG CHAT ID: {TELEGRAM_CHAT_ID}")
    log.append("═══════════════════════════════════\n")

    # ТЕСТ 1: Bybit
    log.append("[ТЕСТ 1] Bybit NEAR Лонг $161.3k")
    msg = json.dumps({
        "topic": "allLiquidation.NEARUSDT",
        "type": "snapshot",
        "ts": int(time.time() * 1000),
        "data": [{
            "T": int(time.time() * 1000),
            "s": "NEARUSDT",
            "S": "Sell",
            "v": "75000",
            "p": "2.15"
        }]
    })
    try:
        BybitMonitor().on_message(None, msg)
        log.append("  -> обработано")
    except Exception as e:
        log.append(f"  -> ОШИБКА: {e}")
    time.sleep(1)

    # ТЕСТ 2: Binance
    log.append("\n[ТЕСТ 2] Binance NEAR Лонг $107.3k")
    with binance_prices_lock:
        binance_mark_prices["NEARUSDT"] = 2.145

    msg = json.dumps({
        "e": "forceOrder",
        "E": int(time.time() * 1000),
        "o": {
            "s": "NEARUSDT", "S": "SELL", "o": "LIMIT", "f": "IOC",
            "q": "50000", "p": "2.10", "ap": "2.08",
            "X": "FILLED", "l": "50000", "z": "104000",
            "T": int(time.time() * 1000)
        }
    })
    try:
        BinanceMonitor().on_message(None, msg)
        log.append("  -> обработано")
    except Exception as e:
        log.append(f"  -> ОШИБКА: {e}")

    log.append("\n═══════════════════════════════════")
    log.append("Симуляция завершена.")
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
        logger.warning("TG_BOT_TOKEN не настроен в окружении")
        return False
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code == 200:
            logger.info("✅ Отправлено в Telegram")
            return True
        logger.error(f"Telegram API вернул {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"Ошибка при отправке в Telegram: {e}")
    return False


def format_liq_msg(exchange, symbol, side, price, qty, value_usd, extra=""):
    s = side.upper().strip()
    coin = symbol.replace("USDT", "").replace("usdt", "")
    
    if s in ("SELL", "S", "A"):
        emoji, pos = "🟢", "Short"
    elif s in ("BUY", "B"):
        emoji, pos = "🔴", "Long"
    else:
        emoji, pos = "⚪", s

    value_str = fmt_usd(value_usd)
    price_str = f"${price:,.4f}" if price < 1.0 else (f"${price:,.2f}" if price < 10_000 else f"${price:,.0f}")

    msg = f"{emoji} <b>{coin}</b> Liquidated {pos}: {value_str} @ Mark Price {price_str} | {exchange}"
    if extra:
        msg += f"\n{extra}"
    if value_usd >= 500_000:
        msg += "\n\n🔥🔥🔥 <b>HUGE!</b>"
    elif value_usd >= 200_000:
        msg += "\n\n🔥 <b>BIG!</b>"
    return msg


# ─── Base Monitor ────────────────────────────────────────────
class BaseMonitor:
    name = "BASE"

    def __init__(self):
        self.ws = None
        self._delay = 1
        self._running = True

    @property
    def ws_ping_interval(self):
        return 0

    @property
    def ws_ping_timeout(self):
        return None

    def on_message(self, ws, message):
        pass

    def on_open(self, ws):
        pass

    def on_error(self, ws, error):
        logger.error(f"[{self.name}] WS ошибка: {error}")

    def on_close(self, ws, code, msg):
        if self._running:
            logger.warning(f"[{self.name}] WS закрыт (code={code}, msg={msg}), переподключение...")
            self._reconnect()
        else:
            logger.info(f"[{self.name}] WS закрыт штатно")

    def _reconnect(self):
        d = min(self._delay, 60)
        logger.info(f"[{self.name}] Переподключение через {d}с...")
        time.sleep(d)
        self._delay = min(self._delay * 2, 60)
        if self._running:
            self.run()

    @property
    def url(self):
        raise NotImplementedError

    def run(self):
        self.ws = websocket.WebSocketApp(
            self.url,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open,
        )
        self.ws.run_forever(
            ping_interval=self.ws_ping_interval,
            ping_timeout=self.ws_ping_timeout,
        )

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
    def ws_ping_interval(self):
        return 20

    @property
    def ws_ping_timeout(self):
        return 10

    @property
    def url(self):
        streams = []
        for s in BINANCE_SYMBOLS:
            streams.append(f"{s.lower()}@forceOrder")
            streams.append(f"{s.lower()}@markPrice@1s")
        
        streams_str = "/".join(streams)
        return f"wss://fstream.binance.com/market/stream?streams={streams_str}"
