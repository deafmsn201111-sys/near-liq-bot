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
            html = "<html><body><h2>Тест лимитов и ликвидаций</h2><pre>" + results + "</pre>"
            html += "<p><a href='/test'>Повторить тест</a> | <a href='/ping'>Ping</a></p></body></html>"
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
    log.append("Запуск симуляции проверки настроек бота")
    log.append(f"   Binance тикеры:    {BINANCE_SYMBOLS} (Порог: {fmt_usd(MIN_LIQ_BINANCE)})")
    log.append(f"   Bybit тикеры:      {BYBIT_SYMBOLS} (Порог: {fmt_usd(MIN_LIQ_BYBIT)})")
    log.append(f"   Hyperliquid:       {HYPERLIQUID_COINS} (Порог: {fmt_usd(MIN_LIQ_HYPERLIQUID)})")
    log.append(f"   TG CHAT ID:         {TELEGRAM_CHAT_ID}")
    log.append("═══════════════════════════════════\n")

    # Симуляция Bybit
    sim_symbol = BYBIT_SYMBOLS[0] if BYBIT_SYMBOLS else "NEARUSDT"
    log.append(f"[ТЕСТ Bybit] Отправка {sim_symbol} Лонг $161.3k")
    msg = json.dumps({
        "topic": f"allLiquidation.{sim_symbol}",
        "type": "snapshot",
        "ts": int(time.time() * 1000),
        "data": [{
            "T": int(time.time() * 1000),
            "s": sim_symbol,
            "S": "Sell",
            "v": "75000",
            "p": "2.15"
        }]
    })
    try:
        BybitMonitor().on_message(None, msg)
        log.append("  -> Обработано")
    except Exception as e:
        log.append(f"  -> ОШИБКА: {e}")
    time.sleep(1)

    # Симуляция Binance
    sim_binance = BINANCE_SYMBOLS[0] if BINANCE_SYMBOLS else "NEARUSDT"
    log.append(f"\n[ТЕСТ Binance] Отправка {sim_binance} Лонг $107.3k")
    with binance_prices_lock:
        binance_mark_prices[sim_binance] = 2.145

    msg = json.dumps({
        "e": "forceOrder",
        "o": {
            "s": sim_binance, "S": "SELL", "o": "LIMIT", "f": "IOC",
            "q": "50000", "p": "2.10", "ap": "2.08",
            "X": "FILLED", "l": "50000", "z": "104000",
            "T": int(time.time() * 1000)
        }
    })
    try:
        BinanceMonitor().on_message(None, msg)
        log.append("  -> Обработано")
    except Exception as e:
        log.append(f"  -> ОШИБКА: {e}")

    # Симуляция Hyperliquid (Приведена к реальному формату API биржи)
    sim_hl = HYPERLIQUID_COINS[0] if HYPERLIQUID_COINS else "NEAR"
    log.append(f"\n[ТЕСТ Hyperliquid] Отправка {sim_hl} Лонг $172.0k")
    msg = json.dumps({
        "channel": "liquidations",
        "data": [
            {
                "coin": sim_hl,
                "side": "S",
                "px": "2.15",
                "sz": "80000"
            }
        ]
    })
    try:
        HyperliquidMonitor().on_message(None, msg)
        log.append("  -> Обработано")
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
    
    if s in ("SELL", "S", "A", "LONG"):
        emoji, pos = "🔴", "Long"
    elif s in ("BUY", "B", "SHORT"):
        emoji, pos = "🟢", "Short"
    else:
        emoji, pos = "⚪", s

    value_str = fmt_usd(value_usd)
    price_str = f"${price:,.4f}" if price < 1.0 else (f"${price:,.2f}" if price < 10_000 else f"${price:,.0f}")

    msg = f"{emoji} <b>{coin}</b> Liquidated {pos}: {value_str} @ {price_str} | {exchange}"
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
        if not BINANCE_SYMBOLS:
            # Страховка на случай пустого списка
            return "wss://fstream.binance.com/market/stream?streams=nearusdt@forceOrder"
            
        streams = []
        for s in BINANCE_SYMBOLS:
            streams.append(f"{s.lower()}@forceOrder")
            streams.append(f"{s.lower()}@markPrice@1s")
        
        streams_str = "/".join(streams)
        return f"wss://fstream.binance.com/market/stream?streams={streams_str}"

    def on_open(self, ws):
        logger.info(f"[{self.name}] ✅ Подключено к стримам для: {', '.join(BINANCE_SYMBOLS)}")
        self._delay = 1

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            if "data" in data and "stream" in data:
                inner = data["data"]
            else:
                inner = data

            event_type = inner.get("e")

            if event_type == "markPriceUpdate":
                symbol = inner.get("s")
                mark_price = float(inner.get("p", 0))
                if symbol and mark_price > 0:
                    with binance_prices_lock:
                        binance_mark_prices[symbol] = mark_price
                return

            if event_type != "forceOrder":
                return

            o = inner.get("o", {})
            if not o:
                return

            symbol = o.get("s", "")
            if symbol not in BINANCE_SYMBOLS:
                return

            side = o.get("S", "")
            qty = float(o.get("q", 0))
            
            with binance_prices_lock:
                price = binance_mark_prices.get(symbol, 0.0)
            
            if price == 0.0:
                price = float(o.get("ap", 0) or o.get("p", 0))

            value = qty * price

            logger.info(f"[{self.name}] Ликвидация {symbol} {side} -> {fmt_usd(value)} (Mark: ${price:,.2f})")

            if value >= MIN_LIQ_BINANCE:
                send_telegram(format_liq_msg("Binance", symbol, side, price, qty, value))
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка обработки: {e}")


# ─── Bybit ──────────────────────────────────────────────────
class BybitMonitor(BaseMonitor):
    name = "Bybit"

    @property
    def ws_ping_interval(self):
        return 20

    @property
    def ws_ping_timeout(self):
        return 10

    @property
    def url(self):
        return "wss://stream.bybit.com/v5/public/linear"

    def on_open(self, ws):
        if not BYBIT_SYMBOLS:
            logger.warning(f"[{self.name}] Список подписок Bybit пуст.")
            return
            
        logger.info(f"[{self.name}] ✅ Подключено. Отправка подписок на {len(BYBIT_SYMBOLS)} пар...")
        self._delay = 1
        req = {
            "op": "subscribe",
            "args": [f"allLiquidation.{s}" for s in BYBIT_SYMBOLS]
        }
        ws.send(json.dumps(req))

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            if data.get("op") == "subscribe":
                logger.info(f"[{self.name}] Ответ на подписку: {data}")
                return

            topic = data.get("topic", "")
            if not topic.startswith("allLiquidation."):
                return

            items = data.get("data", [])
            for item in items:
                symbol = item.get("s", "")
                if symbol not in BYBIT_SYMBOLS:
                    continue

                bybit_side = item.get("S", "") # Получаем "Buy" или "Sell" от Bybit
                qty = float(item.get("v", 0))
                price = float(item.get("p", 0))
                value = qty * price

                # ИСПРАВЛЕНИЕ: Конвертируем сторону позиции Bybit в понятный для format_liq_msg формат
                # "Buy" у Bybit = Ликвидация LONG. "Sell" у Bybit = Ликвидация SHORT.
                side = "SHORT" if bybit_side == "Buy" else "SHORT"

                logger.info(f"[{self.name}] Ликвидация {symbol} {side} -> {fmt_usd(value)} (Mark: ${price:,.2f})")

                if value >= MIN_LIQ_BYBIT:
                    send_telegram(format_liq_msg("Bybit", symbol, side, price, qty, value))
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка обработки: {e}")


# ─── Hyperliquid ─────────────────────────────────────────────
class HyperliquidMonitor(BaseMonitor):
    name = "Hyperliquid"

    @property
    def url(self):
        return "wss://api.hyperliquid.xyz/ws"

    def on_open(self, ws):
        logger.info(f"[{self.name}] ✅ Подключено. Подписка на специализированный канал liquidations...")
        self._delay = 1
        req = {
            "method": "subscribe",
            "subscription": {"type": "liquidations"}
        }
        ws.send(json.dumps(req))

        def _hl_heartbeat():
            while self._running and self.ws is ws:
                time.sleep(15)
                if self._running and self.ws is ws:
                    try:
                        ws.send(json.dumps({"method": "ping"}))
                    except Exception:
                        break
        threading.Thread(target=_hl_heartbeat, daemon=True, name="HL-Heartbeat").start()

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            
            if data.get("channel") == "subscriptionResponse":
                logger.info(f"[{self.name}] Подписка успешно подтверждена биржей.")
                return

            if data.get("channel") != "liquidations":
                return

            # ИСПРАВЛЕНИЕ: Hyperliquid присылает список напрямую в data["data"]
            liquidations = data.get("data", [])
            if not isinstance(liquidations, list):
                return

            for liq in liquidations:
                coin = liq.get("coin", "")
                
                if coin not in HYPERLIQUID_COINS:
                    continue

                # ИСПРАВЛЕНИЕ: Чтение правильных полей публичного API Hyperliquid
                price_str = liq.get("px")
                sz_str = liq.get("sz")
                side_str = liq.get("side") # "S" (Sell) или "B" (Buy)
                
                if not price_str or not sz_str or not side_str:
                    continue

                price = float(price_str)
                sz = float(sz_str)
                value = price * sz

                # ИСПРАВЛЕНИЕ: "S" (принудительная продажа) означает ликвидацию LONG. 
                # "B" (принудительный выкуп) означает ликвидацию SHORT.
                side = "LONG" if side_str == "S" else "SHORT"

                logger.info(
                    f"[{self.name}] ЛИКВИДАЦИЯ {coin} {side} -> {fmt_usd(value)} (MarkPx: ${price:,.4f})"
                )

                if value >= MIN_LIQ_HYPERLIQUID:
                    # Поле user отсутствует в публичном фиде, отправляем без extra-параметров
                    send_telegram(format_liq_msg("Hyperliquid", coin, side, price, sz, value))

        except Exception as e:
            logger.error(f"[{self.name}] Ошибка парсинга Hyperliquid: {e}")


# ─── MAIN ────────────────────────────────────────────────────
monitors = []

def main():
    logger.info("=" * 60)
    logger.info("🚀 Liquidation Bot — запуск")
    logger.info(f"   Binance: {BINANCE_SYMBOLS} (Порог: {fmt_usd(MIN_LIQ_BINANCE)})")
    logger.info(f"   Bybit:   {BYBIT_SYMBOLS} (Порог: {fmt_usd(MIN_LIQ_BYBIT)})")
    logger.info(f"   HL:      {HYPERLIQUID_COINS} (Порог: {fmt_usd(MIN_LIQ_HYPERLIQUID)})")
    logger.info(f"   Общий базовый порог:   {fmt_usd(MIN_LIQ_USD)}")
    logger.info(f"   Порт:    {HTTP_PORT}")
    logger.info(f"   TG:      {TELEGRAM_CHAT_ID}")
    logger.info("=" * 60)

    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()

    monitors.extend([BinanceMonitor(), BybitMonitor(), HyperliquidMonitor()])
    for m in monitors:
        m.start_thread()
        time.sleep(1.5)

    logger.info("Бот запущен в фоновом режиме контейнера. Ожидание событий...")

    stop_event = threading.Event()

    def handle_exit(signum, frame):
        logger.info("Получен сигнал остановки от Render. Завершаем работу потоков...")
        for m in monitors:
            m.stop()
        stop_event.set()

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    stop_event.wait()
    logger.info("Бот полностью остановлен.")

if __name__ == "__main__":
    main()
