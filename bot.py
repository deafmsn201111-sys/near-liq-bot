#!/usr/bin/env python3
"""
Liquidation Notification Bot
Exchanges: Binance Futures, Bybit, Hyperliquid
Sends Telegram alerts for large liquidations.
"""

import os
import sys
import json
import time
import threading
import logging
import signal
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
import websocket

from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, MIN_LIQ_USD,
    BINANCE_SYMBOLS, BYBIT_SYMBOLS, HYPERLIQUID_COINS,
    MIN_MSG_INTERVAL, HTTP_PORT,
)

# ─── Logging ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-16s | %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("CORE")


# ─── Helpers ────────────────────────────────────────────────
def fmt_usd(value: float) -> str:
    """Compact USD formatting: 1234 → $1.2k, 1234567 → $1.23m"""
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


def safe_float(value, fallback: float = 0.0) -> float:
    """Convert value to float, ignoring '0', None, '' as falsy string traps."""
    try:
        result = float(value)
        return result if result != 0.0 else fallback
    except (TypeError, ValueError):
        return fallback


# ─── Telegram ───────────────────────────────────────────────
_last_msg_time = 0.0
_msg_lock = threading.Lock()


def send_telegram(text: str) -> bool:
    global _last_msg_time
    with _msg_lock:
        now = time.time()
        elapsed = now - _last_msg_time
        if elapsed < MIN_MSG_INTERVAL:
            time.sleep(MIN_MSG_INTERVAL - elapsed)
        _last_msg_time = time.time()

    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TG_BOT_TOKEN не задан — сообщение не отправлено")
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
        logger.error(f"Telegram API {r.status_code}: {r.text[:300]}")
    except Exception as e:
        logger.error(f"Ошибка Telegram: {e}")
    return False


def format_liq_msg(exchange: str, symbol: str, side: str,
                   price: float, qty: float, value_usd: float,
                   extra: str = "") -> str:
    """
    Build a Telegram HTML message for a liquidation event.

    Side conventions (AFTER normalisation to 'LONG'/'SHORT'):
      'LONG'  → position that was liquidated = Long → RED emoji 🔴
      'SHORT' → position that was liquidated = Short → GREEN emoji 🟢
    """
    s = side.upper().strip()
    coin = symbol.replace("USDT", "").replace("usdt", "")

    if s == "LONG":
        emoji, pos = "🟢", "Short"
    elif s == "SHORT":
        emoji, pos = "🔴", "Long"
    else:
        emoji, pos = "⚪", s

    value_str = fmt_usd(value_usd)
    price_str = f"${price:,.2f}" if price < 10_000 else f"${price:,.0f}"

    msg = (
        f"{emoji} <b>{coin}</b> Liquidated {pos}: "
        f"{value_str} @ {price_str} | {exchange}"
    )
    if extra:
        msg += f"\n{extra}"
    if value_usd >= 500_000:
        msg += "\n\n🔥🔥🔥 <b>HUGE!</b>"
    elif value_usd >= 200_000:
        msg += "\n\n🔥 <b>BIG!</b>"
    return msg


def normalize_side_binance_bybit(raw_side: str) -> str:
    """
    Binance forceOrder & Bybit allLiquidation both report the EXCHANGE action:
      'SELL' / 'Sell' → exchange sold the position → it was a LONG that got liquidated
      'BUY'  / 'Buy'  → exchange bought back the position → it was a SHORT
    Returns 'LONG' or 'SHORT'.
    """
    s = raw_side.upper().strip()
    if s == "SELL":
        return "LONG"
    if s == "BUY":
        return "SHORT"
    return s


def normalize_side_hyperliquid(szi: float) -> str:
    """
    Hyperliquid liquidations channel: szi is the SIZE of the liquidated position.
      negative szi → position was long (forced to sell) → LONG liquidated
      positive szi → position was short (forced to buy) → SHORT liquidated
    """
    return "LONG" if szi < 0 else "SHORT"


# ─── HTTP Server ─────────────────────────────────────────────
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
            html = (
                "<html><body><h2>Тест ликвидаций</h2><pre>"
                + results
                + "</pre><p><a href='/test'>Повторить</a> | "
                "<a href='/ping'>Ping</a></p></body></html>"
            )
            self.wfile.write(html.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass  # Suppress HTTP access logs


def start_http_server():
    server = HTTPServer(("0.0.0.0", HTTP_PORT), PingHandler)
    logger.info(f"HTTP сервер на порту {HTTP_PORT}")
    server.serve_forever()


# ─── Test Simulation ────────────────────────────────────────
def run_test_simulation() -> str:
    log = []
    log.append("═══════════════════════════════════")
    log.append("Запуск симуляции ликвидаций")
    log.append(f"   Порог: {fmt_usd(MIN_LIQ_USD)}")
    log.append(f"   TG: {TELEGRAM_CHAT_ID}")
    log.append("═══════════════════════════════════\n")

    # Тест 1: Bybit NEAR Long
    log.append("[ТЕСТ 1] Bybit NEAR Лонг $161.3k")
    msg = json.dumps({
        "topic": "allLiquidation.NEARUSDT",
        "type": "snapshot",
        "ts": int(time.time() * 1000),
        "data": [{"T": int(time.time() * 1000), "s": "NEARUSDT", "S": "Sell", "v": "75000", "p": "2.15"}],
    })
    try:
        BybitMonitor().on_message(None, msg)
        log.append("  -> отправлено")
    except Exception as e:
        log.append(f"  -> ОШИБКА: {e}")
    time.sleep(4)

    # Тест 2: Bybit BTC Short
    log.append("\n[ТЕСТ 2] Bybit BTC Шорт $234.5k")
    msg = json.dumps({
        "topic": "allLiquidation.BTCUSDT",
        "type": "snapshot",
        "ts": int(time.time() * 1000),
        "data": [{"T": int(time.time() * 1000), "s": "BTCUSDT", "S": "Buy", "v": "3.5", "p": "67000"}],
    })
    try:
        BybitMonitor().on_message(None, msg)
        log.append("  -> отправлено")
    except Exception as e:
        log.append(f"  -> ОШИБКА: {e}")
    time.sleep(4)

    # Тест 3: Binance NEAR
    log.append("\n[ТЕСТ 3] Binance NEAR Лонг $107.3k")
    msg = json.dumps({
        "stream": "nearusdt@forceOrder",
        "data": {
            "e": "forceOrder",
            "E": int(time.time() * 1000),
            "o": {
                "s": "NEARUSDT", "S": "SELL", "o": "LIMIT", "f": "IOC",
                "q": "50000", "p": "2.15", "ap": "2.145",
                "X": "FILLED", "l": "50000", "z": "107250",
                "T": int(time.time() * 1000),
            },
        },
    })
    try:
        BinanceMonitor().on_message(None, msg)
        log.append("  -> отправлено")
    except Exception as e:
        log.append(f"  -> ОШИБКА: {e}")
    time.sleep(4)

    # Тест 4: Hyperliquid BTC
    log.append("\n[ТЕСТ 4] Hyperliquid BTC Long $335k")
    msg = json.dumps({
        "channel": "liquidations",
        "data": {
            "coin": "BTC",
            "liqPrice": "67000",
            "szi": "-5.0",       # negative = long position
            "user": "0xtestuser123456789",
        },
    })
    try:
        HyperliquidMonitor().on_message(None, msg)
        log.append("  -> отправлено")
    except Exception as e:
        log.append(f"  -> ОШИБКА: {e}")
    time.sleep(4)

    # Тест 5: Прямая отправка
    log.append("\n[ТЕСТ 5] Прямая отправка в Telegram")
    try:
        msg_text = format_liq_msg("TEST", "BTCUSDT", "LONG", 67000.0, 3.5, 234500.0)
        result = send_telegram(msg_text)
        log.append(f"  -> результат: {result}")
    except Exception as e:
        log.append(f"  -> ОШИБКА: {e}")

    log.append("\n═══════════════════════════════════")
    log.append("Симуляция завершена. Проверь Telegram.")
    log.append("═══════════════════════════════════")
    return "\n".join(log)


# ─── Base Monitor ────────────────────────────────────────────
class BaseMonitor:
    name = "BASE"

    def __init__(self):
        self.ws = None
        self._delay = 1
        self._running = True

    @property
    def ws_ping_interval(self) -> int:
        """WebSocket binary ping interval (sec). 0 = disabled."""
        return 0

    @property
    def ws_ping_timeout(self):
        """Pong timeout (sec). None = no timeout."""
        return None

    @property
    def url(self) -> str:
        raise NotImplementedError

    def on_message(self, ws, message):
        pass

    def on_open(self, ws):
        pass

    def on_error(self, ws, error):
        logger.error(f"[{self.name}] WS ошибка: {error}")

    def on_close(self, ws, code, msg):
        if self._running:
            logger.warning(f"[{self.name}] WS закрыт (code={code}), переподключение...")
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


# ─── Binance Monitor ────────────────────────────────────────
class BinanceMonitor(BaseMonitor):
    """
    Connects to Binance Futures combined stream for forceOrder events.

    FIX 1: ap (averagePrice) fallback bug — float('0' or '2.15') = 0.0 because
            '0' is a truthy non-empty string. Use safe_float() instead.
    FIX 2: Side mapping — S='SELL' means the LONG position was force-sold → display as Long liq.
    FIX 3: Use binary WS ping_interval to keep connection alive through proxies.
    """
    name = "Binance"

    @property
    def ws_ping_interval(self) -> int:
        # Binary ping every 20s keeps the connection alive through NAT/proxies.
        # Binance Futures requires keepalive < 60s.
        return 20

    @property
    def ws_ping_timeout(self) -> int:
        return 10

    @property
    def url(self) -> str:
        streams = "/".join(f"{s.lower()}@forceOrder" for s in BINANCE_SYMBOLS)
        return f"wss://fstream.binance.com/stream?streams={streams}"

    def on_open(self, ws):
        logger.info(f"[{self.name}] ✅ Подключено ({', '.join(BINANCE_SYMBOLS)})")
        self._delay = 1

    def on_message(self, ws, message):
        try:
            data = json.loads(message)

            # Combined stream wraps payload: {"stream": "...", "data": {...}}
            inner = data.get("data", data) if "stream" in data else data

            if inner.get("e") != "forceOrder":
                return

            o = inner.get("o", {})
            if not o:
                return

            symbol    = o.get("s", "")
            raw_side  = o.get("S", "")   # 'SELL' or 'BUY' (exchange action)
            qty       = float(o.get("q", 0) or 0)

            # FIX 1: safe_float avoids the '0'-string-truthy trap.
            # ap = average fill price; p = order price.
            ap    = safe_float(o.get("ap", 0))
            p     = safe_float(o.get("p", 0))
            price = ap if ap > 0 else p

            # z = cumulative quote qty (already in USD for *USDT pairs)
            value = safe_float(o.get("z", 0))
            if value == 0:
                value = qty * price

            # FIX 2: normalize side to what was LIQUIDATED, not exchange action
            side = normalize_side_binance_bybit(raw_side)

            logger.info(
                f"[{self.name}] {symbol} {side} {qty:.4f} @ {price:.4f} → {fmt_usd(value)}"
            )
            if value >= MIN_LIQ_USD:
                send_telegram(format_liq_msg("Binance", symbol, side, price, qty, value))

        except Exception as e:
            logger.error(f"[{self.name}] Ошибка: {e} | {message[:200]}")


# ─── Bybit Monitor ──────────────────────────────────────────
class BybitMonitor(BaseMonitor):
    """
    Connects to Bybit V5 public linear stream.
    Subscribes to allLiquidation.* and tickers.* (for mark price cache).

    FIX: Side mapping — S='Sell' means long was liquidated → display as Long liq.
    """
    name = "Bybit"

    def __init__(self):
        super().__init__()
        self._mark_prices: dict[str, float] = {}
        self._mp_lock = threading.Lock()
        self._sub_logged = False

    @property
    def url(self) -> str:
        return "wss://stream.bybit.com/v5/public/linear"

    def on_open(self, ws):
        liq_topics    = [f"allLiquidation.{s}" for s in BYBIT_SYMBOLS]
        ticker_topics = [f"tickers.{s}" for s in BYBIT_SYMBOLS]
        ws.send(json.dumps({"op": "subscribe", "args": liq_topics + ticker_topics}))
        logger.info(
            f"[{self.name}] ✅ Подключено, подписка: "
            f"{len(liq_topics)} liquidation + {len(ticker_topics)} tickers"
        )
        self._delay = 1
        self._sub_logged = False

        # Bybit closes idle connections after ~20s — heartbeat every 18s
        def _heartbeat():
            while self._running and self.ws is ws:
                time.sleep(18)
                if self._running and self.ws is ws:
                    try:
                        ws.send(json.dumps({"op": "ping"}))
                    except Exception:
                        break

        threading.Thread(target=_heartbeat, daemon=True, name="Bybit-HB").start()

    def on_message(self, ws, message):
        try:
            data = json.loads(message)

            # Pong / subscription confirmation
            is_pong = data.get("op") == "pong" or data.get("ret_msg") == "pong"
            if "success" in data or is_pong:
                if data.get("success") is False:
                    logger.warning(f"[{self.name}] Подписка отклонена: {data}")
                elif not is_pong and not self._sub_logged:
                    logger.info(f"[{self.name}] Подписка подтверждена")
                    self._sub_logged = True
                return

            topic = data.get("topic", "")

            # tickers.* — cache mark price
            if topic.startswith("tickers."):
                d = data.get("data", {})
                if isinstance(d, list):
                    d = d[0] if d else {}
                symbol = d.get("symbol", "")
                mp_str = d.get("markPrice")
                if symbol and mp_str:
                    mp = safe_float(mp_str)
                    if mp > 0:
                        with self._mp_lock:
                            self._mark_prices[symbol] = mp
                        logger.debug(f"[{self.name}] mark {symbol}={mp}")
                return

            if "liquidation" not in topic.lower():
                return

            # allLiquidation.*
            items = data.get("data", {})
            if isinstance(items, dict):
                items = [items]

            for item in items:
                symbol     = item.get("s", item.get("symbol", ""))
                raw_side   = item.get("S", item.get("side", ""))
                size       = float(item.get("v", item.get("size", 0)) or 0)
                exec_price = safe_float(item.get("p", item.get("price", 0)))

                with self._mp_lock:
                    mark_price = self._mark_prices.get(symbol, 0.0)

                price      = mark_price if mark_price > 0 else exec_price
                price_src  = "mark" if mark_price > 0 else "liq"
                value      = size * price

                # FIX: normalize side
                side = normalize_side_binance_bybit(raw_side)

                logger.info(
                    f"[{self.name}] {symbol} {side} {size:.4f} @ {price:.4f} "
                    f"({price_src}; exec={exec_price:.4f}) → {fmt_usd(value)}"
                )
                if value >= MIN_LIQ_USD:
                    send_telegram(format_liq_msg("Bybit", symbol, side, price, size, value))

        except Exception as e:
            logger.error(f"[{self.name}] Ошибка: {e} | {message[:200]}")


# ─── Hyperliquid Monitor ─────────────────────────────────────
class HyperliquidMonitor(BaseMonitor):
    """
    Connects to Hyperliquid WebSocket API.

    FIX 1: Subscribe to 'liquidations' channel per coin, NOT 'trades'.
            The 'trades' channel rarely contains liquidation data and uses
            a different schema. The 'liquidations' channel is dedicated and reliable.

    FIX 2: Correct message schema for 'liquidations' channel:
            data.coin, data.liqPrice, data.szi (size; negative = long liq), data.user

    FIX 3: Side derived from szi sign (negative = long position = long liquidated).

    FIX 4: Do NOT use websocket-client binary ping_interval with Hyperliquid —
            HL expects text-level {"method": "ping"} frames, not binary WS pings.
            Binary pings from websocket-client can cause HL to silently drop the conn.
    """
    name = "Hyperliquid"

    # Intentionally leave ws_ping_interval=0: we do application-level text pings
    @property
    def ws_ping_interval(self) -> int:
        return 0

    @property
    def url(self) -> str:
        return "wss://api.hyperliquid.xyz/ws"

    def on_open(self, ws):
        logger.info(
            f"[{self.name}] ✅ Подключено, подписка на {len(HYPERLIQUID_COINS)} монет "
            f"(liquidations channel)"
        )
        # FIX 1: subscribe to 'liquidations', not 'trades'
        for coin in HYPERLIQUID_COINS:
            ws.send(json.dumps({
                "method": "subscribe",
                "subscription": {"type": "liquidations", "coin": coin},
            }))
            time.sleep(0.15)
        self._delay = 1

        # FIX 4: application-level text ping every 30s (HL requires ping < 60s)
        def _heartbeat():
            while self._running and self.ws is ws:
                time.sleep(30)
                if self._running and self.ws is ws:
                    try:
                        ws.send(json.dumps({"method": "ping"}))
                    except Exception:
                        break

        threading.Thread(target=_heartbeat, daemon=True, name="HL-HB").start()

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            channel = data.get("channel", "")

            if channel == "subscriptionResponse":
                logger.info(f"[{self.name}] Подписка подтверждена: {data.get('data', '')}")
                return
            if channel == "pong":
                logger.debug(f"[{self.name}] pong")
                return

            # FIX 2: handle 'liquidations' channel schema
            if channel != "liquidations":
                return

            liq_data = data.get("data", {})
            # HL may send a list or a single dict
            items = liq_data if isinstance(liq_data, list) else [liq_data]

            for item in items:
                coin      = item.get("coin", "")
                liq_price = safe_float(item.get("liqPrice", 0))
                szi       = float(item.get("szi", 0) or 0)   # signed size
                user      = item.get("user", "?")

                if liq_price == 0 or szi == 0:
                    continue

                qty   = abs(szi)
                value = liq_price * qty

                # FIX 3: derive side from szi sign
                side = normalize_side_hyperliquid(szi)

                user_short = user[:10] + "…" if len(user) > 10 else user

                logger.info(
                    f"[{self.name}] LIQ {coin} {side} {qty:.4f} @ {liq_price:.4f} "
                    f"→ {fmt_usd(value)} (user={user_short})"
                )

                if value >= MIN_LIQ_USD:
                    extra = f"🏷 <b>Адрес:</b> <code>{user_short}</code>"
                    send_telegram(
                        format_liq_msg("Hyperliquid", coin + "USDT", side, liq_price, qty, value, extra)
                    )

        except Exception as e:
            logger.error(f"[{self.name}] Ошибка: {e} | {message[:200]}")


# ─── Main ────────────────────────────────────────────────────
monitors: list[BaseMonitor] = []


def main():
    logger.info("=" * 60)
    logger.info("🚀 Liquidation Bot — запуск")
    logger.info(f"   Binance: {BINANCE_SYMBOLS}")
    logger.info(f"   Bybit:   {BYBIT_SYMBOLS}")
    logger.info(f"   HL:      {HYPERLIQUID_COINS}")
    logger.info(f"   Порог:   {fmt_usd(MIN_LIQ_USD)}")
    logger.info(f"   Порт:    {HTTP_PORT}")
    logger.info(f"   TG:      {TELEGRAM_CHAT_ID}")
    logger.info("=" * 60)

    threading.Thread(target=start_http_server, daemon=True, name="HTTP").start()

    monitors.extend([BinanceMonitor(), BybitMonitor(), HyperliquidMonitor()])
    for m in monitors:
        m.start_thread()
        time.sleep(1.5)

    logger.info("✅ Все мониторы запущены")

    def shutdown(signum, frame):
        logger.info(f"Сигнал {signum} — остановка...")
        for m in monitors:
            m.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
