#!/usr/bin/env python3
import os, sys, json, time, threading, logging, signal
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import websocket
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, MIN_LIQ_USD,
    MIN_LIQ_BINANCE, MIN_LIQ_BYBIT, MIN_LIQ_HYPERLIQUID,
    BINANCE_SYMBOLS, BYBIT_SYMBOLS, HYPERLIQUID_COINS,
    MIN_MSG_INTERVAL, HTTP_PORT,
)

# Настройка ротации файлов
file_handler = RotatingFileHandler(
    "bot.log", 
    maxBytes=2 * 1024 * 1024, 
    backupCount=1, 
    encoding="utf-8"
)

logging.getLogger("websocket").setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-12s | %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout), file_handler],
)
logger = logging.getLogger("CORE")

binance_mark_prices = {}
binance_prices_lock = threading.Lock()

bybit_mark_prices = {}
bybit_prices_lock = threading.Lock()

def safe_float(val, default=0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def fmt_usd(value: float) -> str:
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

class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/ping", "/health"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, fmt, *args):
        pass

def start_http_server():
    server = HTTPServer(("0.0.0.0", HTTP_PORT), PingHandler)
    server.serve_forever()

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
        return False
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        r = requests.post(url, data=payload, timeout=10)
        return r.status_code == 200
    except Exception:
        pass
    return False

def format_liq_msg(exchange, symbol, side, price, qty, value_usd, extra=""):
    s = side.upper().strip()
    coin = symbol.replace("USDT", "").replace("usdt", "")
    
    if s in ("SELL", "S", "LONG"):
        emoji, pos = "🔴", "LONG"
    elif s in ("BUY", "B", "SHORT"):
        emoji, pos = "🟢", "SHORT"
    else:
        emoji, pos = "⚪", s

    if value_usd >= 1_000_000:
        emoji_prefix = emoji * 3
    elif value_usd >= 500_000:
        emoji_prefix = emoji * 2
    else:
        emoji_prefix = emoji

    value_str = fmt_usd(value_usd)
    price_str = f"${price/1000:.3f}" if price >= 1000 else f"${price:,.2f}"
    
    msg = f"{emoji_prefix} <b>#{coin}</b> Liquidated {pos}: {value_str} @ {price_str} | {exchange}"
    if extra:
        msg += f"\n{extra}"
    return msg
    
class BaseMonitor:
    name = "BASE"
    def __init__(self):
        self.ws = None
        self._delay = 1
        self._running = True

    def on_error(self, ws, error):
        logger.error(f"[{self.name}] WS ошибка: {error}")

    def on_close(self, ws, code, msg):
        if self._running:
            threading.Thread(target=self._reconnect, daemon=True).start()

    def _reconnect(self):
        d = min(self._delay, 30)
        time.sleep(d)
        self._delay = min(self._delay * 2, 30)
        if self._running:
            self.run()

    def run(self):
        self.ws = websocket.WebSocketApp(
            self.url,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open,
        )
        self.ws.run_forever(ping_interval=20, ping_timeout=10)

    def start_thread(self):
        threading.Thread(target=self.run, daemon=True, name=self.name).start()

    def stop(self):
        self._running = False
        if self.ws:
            self.ws.close()

class BinanceMonitor(BaseMonitor):
    name = "Binance"
    @property
    def url(self):
        streams = [f"{s.lower()}@forceOrder" for s in BINANCE_SYMBOLS] + [f"{s.lower()}@markPrice@1s" for s in BINANCE_SYMBOLS]
        return f"wss://fstream.binance.com/market/stream?streams={'/'.join(streams)}"

    def on_open(self, ws):
        logger.info(f"[{self.name}] ✅ Подключено.")
        self._delay = 1

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            inner = data.get("data", data)
            event_type = inner.get("e")

            if event_type == "markPriceUpdate":
                symbol = inner.get("s")
                mark_price = safe_float(inner.get("p", 0))
                if symbol and mark_price > 0:
                    with binance_prices_lock:
                        binance_mark_prices[symbol] = mark_price
                return

            if event_type == "forceOrder":
                o = inner.get("o", {})
                symbol = o.get("s", "")
                if symbol not in BINANCE_SYMBOLS: return
                side = "LONG" if o.get("S", "").upper() == "SELL" else "SHORT"
                qty = safe_float(o.get("q", 0))
                with binance_prices_lock:
                    price = binance_mark_prices.get(symbol, 0.0)
                if price == 0.0: price = safe_float(o.get("ap") or o.get("p", 0))
                value = qty * price

                if value >= MIN_LIQ_BINANCE:
                    logger.info(f"🔥 [BINANCE] Крупная ликва: {symbol} {side} {fmt_usd(value)}")
                    send_telegram(format_liq_msg("Binance", symbol, side, price, qty, value))
        except Exception:
            pass

class BybitMonitor(BaseMonitor):
    name = "Bybit"
    @property
    def url(self): return "wss://stream.bybit.com/v5/public/linear"
    def on_open(self, ws):
        self._delay = 1
        args = [f"allLiquidation.{s}" for s in BYBIT_SYMBOLS] + [f"tickers.{s}" for s in BYBIT_SYMBOLS]
        ws.send(json.dumps({"op": "subscribe", "args": args}))

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            topic = data.get("topic", "")
            if topic.startswith("tickers."):
                inner = data.get("data", {})
                symbol = inner.get("symbol")
                if symbol and "markPrice" in inner:
                    mark_price = safe_float(inner.get("markPrice", 0))
                    if mark_price > 0:
                        with bybit_prices_lock: bybit_mark_prices[symbol] = mark_price
                return
            if topic.startswith("allLiquidation."):
                for item in data.get("data", []):
                    symbol = item.get("s", "")
                    if symbol not in BYBIT_SYMBOLS: continue
                    side = "SHORT" if item.get("S", "").lower() == "sell" else "LONG"
                    qty = safe_float(item.get("v", 0))
                    with bybit_prices_lock: price = bybit_mark_prices.get(symbol, 0.0)
                    if price == 0.0: price = safe_float(item.get("p", 0))
                    value = qty * price

                    if value >= MIN_LIQ_BYBIT:
                        logger.info(f"🔥 [BYBIT] Крупная ликва: {symbol} {side} {fmt_usd(value)}")
                        send_telegram(format_liq_msg("Bybit", symbol, side, price, qty, value))
        except Exception:
            pass

class HyperliquidMonitor(BaseMonitor):
    name = "Hyperliquid-Explorer"
    
    @property
    def url(self): 
        return "wss://api.hyperliquid.xyz/ws"

    def on_open(self, ws):
        logger.info(f"[{self.name}] ✅ Подключено к EXPLORER. Ищем ликвидации...")
        # Подписываемся на блокчейн-события
        ws.send(json.dumps({
            "method": "subscribe", 
            "subscription": {"type": "explorer"}
        }))

    def on_message(self, ws, message):
        try:
            msg = json.loads(message)
            # В explorer приходят данные блока
            if msg.get("channel") != "explorer":
                return

            # Внутри explorer всегда есть список транзакций (txs)
            data = msg.get("data", {})
            txs = data.get("txs", [])

            for tx in txs:
                # В Hyperliquid транзакция имеет поле 'action'
                action = tx.get("action", {})
                
                # Ищем именно ликвидацию
                # В структуре HL это выглядит примерно так (названия полей могут быть чуть иными, 
                # уточнишь по первому логу):
                if action.get("type") == "liquidation":
                    process_liquidation(action)

        except Exception as e:
            logger.error(f"[HL-EXPLORER-ERROR] Ошибка разбора: {e}")

def process_liquidation(liq):
    """
    Тут мы обрабатываем найденную ликвидацию
    """
    user = liq.get("liquidatedUser")
    price = liq.get("markPx")
    size = liq.get("sz")
    coin = liq.get("coin")
    
    # Тот самый фильтр на 500к
    value = float(price) * float(size)
    if value < 500000:
        return

    logger.info(f"[LIQUIDATION-DETECTED] User: {user} | Coin: {coin} | Val: {value} | Price: {price}")

monitors = []

def main():
    logger.info("🚀 Сборщик ликвидаций запущен.")
    threading.Thread(target=start_http_server, daemon=True).start()

    monitors.extend([BinanceMonitor(), BybitMonitor(), HyperliquidMonitor()])
    for m in monitors:
        m.start_thread()
        time.sleep(1)

    stop_event = threading.Event()
    def handle_exit(signum, frame):
        for m in monitors: m.stop()
        stop_event.set()

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
    stop_event.wait()

if __name__ == "__main__":
    main()
