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


# ─── Helpers ────────────────────────────────────────────────
def fmt_usd(value: float) -> str:
    """Compact USD formatting: 1234 → $1.2k, 1234567 → $1.2m"""
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
    logger.info(f"HTTP сервер на порту {HTTP_PORT}")
    server.serve_forever()


# ─── Test Simulation ────────────────────────────────────────
def run_test_simulation():
    log = []
    log.append("═══════════════════════════════════")
    log.append("Запуск симуляции ликвидаций")
    log.append(f"   Порог: {fmt_usd(MIN_LIQ_USD)}")
    log.append(f"   TG: {TELEGRAM_CHAT_ID}")
    log.append("═══════════════════════════════════\n")

    # ТЕСТ 1: Bybit NEAR
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
        log.append("  -> отправлено")
    except Exception as e:
        log.append(f"  -> ОШИБКА: {e}")
    time.sleep(4)

    # ТЕСТ 2: Bybit BTC
    log.append("\n[ТЕСТ 2] Bybit BTC Шорт $234.5k")
    msg = json.dumps({
        "topic": "allLiquidation.BTCUSDT",
        "type": "snapshot",
        "ts": int(time.time() * 1000),
        "data": [{
            "T": int(time.time() * 1000),
            "s": "BTCUSDT",
            "S": "Buy",
            "v": "3.5",
            "p": "67000"
        }]
    })
    try:
        BybitMonitor().on_message(None, msg)
        log.append("  -> отправлено")
    except Exception as e:
        log.append(f"  -> ОШИБКА: {e}")
    time.sleep(4)

    # ТЕСТ 3: Binance NEAR
    log.append("\n[ТЕСТ 3] Binance NEAR Лонг $107.3k")
    msg = json.dumps({
        "e": "forceOrder",
        "E": int(time.time() * 1000),
        "o": {
            "s": "NEARUSDT", "S": "SELL", "o": "LIMIT", "f": "IOC",
            "q": "50000", "p": "2.15", "ap": "2.145",
            "X": "FILLED", "l": "50000", "z": "107250",
            "T": int(time.time() * 1000)
        }
    })
    try:
        BinanceMonitor().on_message(None, msg)
        log.append("  -> отправлено")
    except Exception as e:
        log.append(f"  -> ОШИБКА: {e}")
    time.sleep(4)

    # ТЕСТ 4: Hyperliquid
    log.append("\n[ТЕСТ 4] Hyperliquid BTC $335k")
    msg = json.dumps({
        "channel": "trades",
        "data": [{
            "coin": "BTC",
            "side": "A",  # A = ask side = liquidated long (sell)
            "px": "67000",
            "sz": "5.0",
            "time": int(time.time() * 1000),
            "hash": "0xtest",
            "tid": 99999,
            "liquidation": {
                "liquidatedUser": "0xtestuser123456789",
                "markPx": "67100",  # mark price at liquidation moment
                "method": "market"
            }
        }]
    })
    try:
        HyperliquidMonitor().on_message(None, msg)
        log.append("  -> отправлено")
    except Exception as e:
        log.append(f"  -> ОШИБКА: {e}")
    time.sleep(4)

    # ТЕСТ 5: Прямая отправка
    log.append("\n[ТЕСТ 5] Прямая отправка в Telegram")
    try:
        msg_text = format_liq_msg("TEST", "BTCUSDT", "SELL", 67000.0, 3.5, 234500.0)
        result = send_telegram(msg_text)
        log.append(f"  -> результат: {result}")
    except Exception as e:
        log.append(f"  -> ОШИБКА: {e}")

    log.append("\n═══════════════════════════════════")
    log.append("Симуляция завершена. Проверь Telegram.")
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
        logger.warning("TG_BOT_TOKEN не задан")
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
        logger.error(f"Telegram API {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"Ошибка Telegram: {e}")
    return False


def format_liq_msg(exchange, symbol, side, price, qty, value_usd, extra=""):
    s = side.upper().strip()
    coin = symbol.replace("USDT", "").replace("usdt", "")
    # Hyperliquid: "A" = ask (liquidated long), "B" = bid (liquidated short)
    if s in ("SELL", "S", "A"):
        emoji, pos = "🟢", "Short"
    elif s in ("BUY", "B"):
        emoji, pos = "🔴", "Long"
    else:
        emoji, pos = "⚪", s

    value_str = fmt_usd(value_usd)
    price_str = f"${price:,.2f}" if price < 10_000 else f"${price:,.0f}"

    msg = f"{emoji} <b>{coin}</b> Liquidated {pos}: {value_str} at {price_str} on {exchange}"
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

    def on_message(self, ws, message):
        pass

    def on_open(self, ws):
        pass

    def on_error(self, ws, error):
        logger.error(f"[{self.name}] WS ошибка: {error}")

    def on_close(self, ws, code, msg):
        logger.warning(f"[{self.name}] WS закрыт ({code})")
        if self._running:
            self._reconnect()

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
        # Disable library-level TCP pings entirely (ping_interval=0).
        # Binance sends its own keepalives; Bybit and Hyperliquid need
        # application-level JSON heartbeats which each monitor handles
        # in a dedicated thread started from on_open.
        self.ws.run_forever(ping_interval=0)

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
            # Combined stream wraps payload: {"stream":"...","data":{...}}
            if "data" in data and "stream" in data:
                inner = data["data"]
            else:
                inner = data

            # forceOrder event
            if inner.get("e") != "forceOrder":
                return

            o = inner.get("o", {})
            if not o:
                return

            symbol = o.get("s", "")
            side   = o.get("S", "")   # "BUY" = liquidated long, "SELL" = liquidated short
            qty    = float(o.get("q", 0))
            # ap = average fill price; p = order price. Use ap as execution price.
            price  = float(o.get("ap", 0) or o.get("p", 0))
            value  = float(o.get("z", 0))
            if value == 0:
                value = qty * price

            logger.info(f"[{self.name}] {symbol} {side} {qty:.4f} @ {price:.4f} → {fmt_usd(value)}")
            if value >= MIN_LIQ_USD:
                send_telegram(format_liq_msg("Binance", symbol, side, price, qty, value))
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка: {e} | {message[:200]}")


# ─── Bybit ──────────────────────────────────────────────────
class BybitMonitor(BaseMonitor):
    name = "Bybit"

    def __init__(self):
        super().__init__()
        # Кеш mark-price по символам, обновляется из топика tickers.*
        self._mark_prices = {}
        self._mp_lock = threading.Lock()

    @property
    def url(self):
        return "wss://stream.bybit.com/v5/public/linear"

    def on_open(self, ws):
        liq_topics = [f"allLiquidation.{s}" for s in BYBIT_SYMBOLS]
        ticker_topics = [f"tickers.{s}" for s in BYBIT_SYMBOLS]
        topics = liq_topics + ticker_topics
        logger.info(
            f"[{self.name}] ✅ Подключено, подписка: "
            f"{len(liq_topics)} liquidation + {len(ticker_topics)} tickers"
        )
        ws.send(json.dumps({"op": "subscribe", "args": topics}))
        self._delay = 1
        # Bybit закрывает соединение после 20с тишины — пинг каждые 18с
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

            # Heartbeat / подтверждение подписки
            is_pong = data.get("op") == "pong" or data.get("ret_msg") == "pong"
            if "success" in data or is_pong:
                if data.get("success") is False:
                    logger.warning(f"[{self.name}] Подписка отклонена: {data}")
                elif is_pong:
                    logger.debug(f"[{self.name}] pong")
                else:
                    # Подписка подтверждена — логируем только при первом подключении
                    if not getattr(self, '_sub_logged', False):
                        logger.info(f"[{self.name}] Подписка подтверждена")
                        self._sub_logged = True
                    else:
                        logger.debug(f"[{self.name}] Подписка подтверждена (повтор)")
                return

            # ── Топик tickers.* : обновляем кеш mark-price ─────────────
            if topic.startswith("tickers."):
                d = data.get("data", {})
                if isinstance(d, list):
                    d = d[0] if d else {}
                symbol = d.get("symbol", "")
                mark_price_str = d.get("markPrice")
                if symbol and mark_price_str:
                    try:
                        mp = float(mark_price_str)
                        if mp > 0:
                            with self._mp_lock:
                                self._mark_prices[symbol] = mp
                            logger.debug(f"[{self.name}] mark {symbol}={mp}")
                    except (ValueError, TypeError):
                        pass
                return

            # ── Топик allLiquidation.* : берём mark price из кеша ───────
            if "liquidation" not in topic.lower():
                return

            d = data.get("data", {})
            items = d if isinstance(d, list) else [d]
            for item in items:
                symbol = item.get("s", item.get("symbol", ""))
                side   = item.get("S", item.get("side", ""))
                size   = float(item.get("v", item.get("size", 0)))
                exec_price = float(item.get("p", item.get("price", 0)))

                # Берём текущий mark price из кеша; если ещё не успели
                # получить — fallback на цену исполнения.
                with self._mp_lock:
                    mark_price = self._mark_prices.get(symbol, 0.0)

                if mark_price > 0:
                    price = mark_price
                    price_src = "mark"
                else:
                    price = exec_price
                    price_src = "liq"

                value = size * price

                logger.info(
                    f"[{self.name}] {symbol} {side} {size:.4f} @ {price:.4f} "
                    f"({price_src}; exec={exec_price:.4f}) → {fmt_usd(value)}"
                )
                if value >= MIN_LIQ_USD:
                    send_telegram(format_liq_msg("Bybit", symbol, side, price, size, value))
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка: {e} | {message[:200]}")


# ─── Hyperliquid ─────────────────────────────────────────────
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
        # Hyperliquid drops idle connections — send ping every 30s
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
                logger.info(f"[{self.name}] Подписка: {data}")
                return
            if channel == "pong":
                return
            if channel != "trades":
                return

            raw = data.get("data", [])
            # data may be a list of trades or a single trade dict
            trades = raw if isinstance(raw, list) else [raw]

            for t in trades:
                liq = t.get("liquidation")
                if not liq:
                    continue

                coin  = t.get("coin", "")
                side  = t.get("side", "")   # "A" = ask (long liq), "B" = bid (short liq)
                sz    = float(t.get("sz", 0))

                # FIX: use markPx from liquidation object as the reference price,
                # not px (fill price). markPx reflects fair market price at liq time.
                mark_px = float(liq.get("markPx", 0) or t.get("px", 0))
                value   = mark_px * sz

                liq_user = liq.get("liquidatedUser", "?")
                liq_user_short = liq_user[:10] + "…" if len(liq_user) > 10 else liq_user

                logger.info(
                    f"[{self.name}] LIQ {coin} {side} {sz:.4f} @ markPx={mark_px:.4f} "
                    f"→ {fmt_usd(value)} (user={liq_user_short})"
                )

                if value >= MIN_LIQ_USD:
                    extra = f"🏷 <b>Адрес:</b> <code>{liq_user_short}</code>"
                    send_telegram(format_liq_msg("Hyperliquid", coin, side, mark_px, sz, value, extra))
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка: {e} | {message[:200]}")


# ─── MAIN ────────────────────────────────────────────────────
monitors = []


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

    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()

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
