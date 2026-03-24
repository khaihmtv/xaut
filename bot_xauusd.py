"""
╔══════════════════════════════════════════════════════════════╗
║         MULTI-COIN BOT – XAU + SOL trên OKX                ║
║                                                              ║
║  XAU: EMA 15/26/80 | ATR 14 | SL 2.5x | TP 4.0x           ║
║  SOL: EMA 21/50/80 | ATR 10 | SL 1.5x | TP 4.0x           ║
║                                                              ║
║  Chạy:  python bot_multi.py                                 ║
║  Dừng:  Ctrl+C                                              ║
╚══════════════════════════════════════════════════════════════╝
"""

import time
import logging
import json
import sys
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import hmac
import hashlib
import base64
import pandas as pd
import numpy as np

# ══════════════════════════════════════════════════════════════
# 1. CẤU HÌNH
# ══════════════════════════════════════════════════════════════

try:
    from config import BASE_URL, API_KEY, API_SECRET, API_PASSPHRASE, SIMULATED
except ImportError:
    print("❌ Không tìm thấy config.py.")
    sys.exit(1)

# ── Cấu hình từng coin ────────────────────────────────────────
COINS = {
    "XAU-USDT-SWAP": {
        "timeframe":   "1H",
        "ema_fast":    15,
        "ema_slow":    26,
        "ema_trend":   80,
        "atr_period":  14,
        "atr_sl_mult": 2.5,
        "atr_tp_mult": 4.0,
        "ct_val":      95,    # 1 contract = 1 oz XAU
    },
    "SOL-USDT-SWAP": {
        "timeframe":   "1H",
        "ema_fast":    21,
        "ema_slow":    50,
        "ema_trend":   80,
        "atr_period":  10,
        "atr_sl_mult": 1.5,
        "atr_tp_mult": 4.0,
        "ct_val":      0.5,    # 1 contract = 1 SOL trên OKX (verify lại nếu khác)
    },
}

# ── Cấu hình chung ────────────────────────────────────────────
LEVERAGE          = 3
RISK_PER_TRADE    = 0.01    # 1% vốn mỗi lệnh mỗi coin
MAX_DRAWDOWN_STOP = 0.20    # Dừng toàn bộ bot nếu DD > 20%
MAX_DAILY_LOSS    = 0.05    # Dừng ngày nếu lỗ > 5%
TRADE_HOURS_UTC   = list(range(6, 18))
CHECK_INTERVAL    = 60      # giây

# ══════════════════════════════════════════════════════════════
# 2. LOGGING
# ══════════════════════════════════════════════════════════════

VN_TZ = timezone(timedelta(hours=7))

log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)
log_file = log_dir / f"bot_multi_{datetime.now(VN_TZ).strftime('%Y%m%d_%H%M%S')}.log"

class _VNFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=VN_TZ)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

_fmt = _VNFormatter("%(asctime)s [%(levelname)s] %(message)s")
_fh  = logging.FileHandler(log_file, encoding="utf-8")
_sh  = logging.StreamHandler(sys.stdout)
_fh.setFormatter(_fmt)
_sh.setFormatter(_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_fh, _sh])
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# 3. OKX CLIENT
# ══════════════════════════════════════════════════════════════

class OKXError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(f"OKX [{code}]: {message}")


class OKXClient:
    TIMEOUT     = 20
    MAX_RETRIES = 3

    def __init__(self):
        self.base_url  = BASE_URL
        self.simulated = SIMULATED
        self._lock     = threading.Lock()   # thread-safe API calls

    def _ts(self):
        return (datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"))

    def _sign(self, ts, method, path, body=""):
        msg = ts + method + path + body
        mac = hmac.new(bytes(API_SECRET, "utf-8"), bytes(msg, "utf-8"), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode()

    def _headers(self, method, path, body=""):
        ts = self._ts()
        return {
            "OK-ACCESS-KEY":        API_KEY,
            "OK-ACCESS-SIGN":       self._sign(ts, method, path, body),
            "OK-ACCESS-TIMESTAMP":  ts,
            "OK-ACCESS-PASSPHRASE": API_PASSPHRASE,
            "Content-Type":         "application/json",
            "x-simulated-trading":  self.simulated,
        }

    def _request(self, method, path, body=""):
        url     = self.base_url + path
        headers = self._headers(method, path, body)

        with self._lock:   # chỉ 1 request tại 1 thời điểm để tránh rate limit
            for attempt in range(1, self.MAX_RETRIES + 1):
                try:
                    if method == "GET":
                        r = requests.get(url, headers=headers, timeout=self.TIMEOUT)
                    else:
                        r = requests.post(url, headers=headers, data=body, timeout=self.TIMEOUT)
                    r.raise_for_status()
                    data = r.json()
                    if data.get("code") != "0":
                        raise OKXError(data.get("code", "?"), data.get("msg", "Unknown"))
                    return data

                except requests.Timeout:
                    logger.warning("⏱ Timeout %d/%d: %s %s", attempt, self.MAX_RETRIES, method, path)
                    if attempt == self.MAX_RETRIES:
                        raise
                    time.sleep(5 * attempt)

                except requests.RequestException as e:
                    logger.warning("🌐 Lỗi mạng %d/%d: %s", attempt, self.MAX_RETRIES, e)
                    if attempt == self.MAX_RETRIES:
                        raise
                    time.sleep(5 * attempt)

    def get_candles(self, symbol: str, timeframe: str, limit=200):
        path = f"/api/v5/market/candles?instId={symbol}&bar={timeframe}&limit={limit}"
        r = requests.get(self.base_url + path, timeout=self.TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if "data" not in data or not data["data"]:
            raise ValueError(f"Không lấy được nến {symbol}: {data}")
        return list(reversed(data["data"]))

    def get_equity(self):
        data  = self._request("GET", "/api/v5/account/balance")
        total = 0.0
        for acc in data.get("data", []):
            for d in acc.get("details", []):
                if d.get("ccy") == "USDT":
                    total += float(d.get("eq", 0))
        return total

    def get_positions(self, symbol: str):
        path = f"/api/v5/account/positions?instId={symbol}"
        data = self._request("GET", path)
        return [p for p in data.get("data", []) if float(p.get("pos", 0)) != 0]

    def get_open_algo_orders(self, symbol: str):
        path = f"/api/v5/trade/orders-algo-pending?instId={symbol}&ordType=conditional"
        data = self._request("GET", path)
        return data.get("data", [])

    def place_order(self, symbol: str, side: str, size: int,
                    sl_price=None, tp_price=None):
        pos_side = "long" if side == "buy" else "short"

        # Bước 1: Market order
        body = json.dumps({
            "instId":  symbol,
            "tdMode":  "cross",
            "side":    side,
            "posSide": pos_side,
            "ordType": "market",
            "sz":      str(size),
        })
        response = self._request("POST", "/api/v5/trade/order", body)

        # Bước 2: SL/TP riêng
        if sl_price or tp_price:
            time.sleep(1)
            self._place_algo_sltp(symbol, pos_side, size, sl_price, tp_price)

        return response

    def _place_algo_sltp(self, symbol, pos_side, size, sl_price, tp_price):
        try:
            positions = self.get_positions(symbol)
            if positions:
                entry  = float(positions[0]["avgPx"])
                atr_sl = abs(sl_price - entry) if sl_price else 0
                atr_tp = abs(tp_price - entry) if tp_price else 0
                if pos_side == "long":
                    sl_price = round(entry - atr_sl, 4) if sl_price else None
                    tp_price = round(entry + atr_tp, 4) if tp_price else None
                else:
                    sl_price = round(entry + atr_sl, 4) if sl_price else None
                    tp_price = round(entry - atr_tp, 4) if tp_price else None
                logger.info("[%s] 📌 Entry thật: %.4f | SL: %s | TP: %s",
                            symbol, entry, sl_price, tp_price)
        except Exception as e:
            logger.warning("[%s] Không lấy được entry thật: %s", symbol, e)

        self._place_single_algo(symbol, pos_side, size, sl_price, is_sl=True)
        self._place_single_algo(symbol, pos_side, size, tp_price, is_sl=False)

    def _place_single_algo(self, symbol, pos_side, size, trigger_price, is_sl: bool):
        if not trigger_price:
            return
        close_side  = "sell" if pos_side == "long" else "buy"
        trigger_key = "slTriggerPx" if is_sl else "tpTriggerPx"
        ord_px_key  = "slOrdPx"     if is_sl else "tpOrdPx"
        type_key    = "slTriggerPxType" if is_sl else "tpTriggerPxType"

        body = json.dumps({
            "instId":    symbol,
            "tdMode":    "cross",
            "side":      close_side,
            "posSide":   pos_side,
            "ordType":   "conditional",
            "sz":        str(size),
            trigger_key: str(round(trigger_price, 4)),
            ord_px_key:  "-1",
            type_key:    "last",
        })
        try:
            self._request("POST", "/api/v5/trade/order-algo", body)
            label = "SL" if is_sl else "TP"
            logger.info("[%s] 🛡 %s @ %.4f ✅", symbol, label, trigger_price)
        except OKXError as e:
            logger.error("[%s] ❌ Đặt %s thất bại: %s", symbol, "SL" if is_sl else "TP", e)

    def cancel_algo_orders(self, symbol: str):
        orders = self.get_open_algo_orders(symbol)
        if not orders:
            return
        cancel = [{"instId": symbol, "algoId": o["algoId"]} for o in orders]
        self._request("POST", "/api/v5/trade/cancel-algos", json.dumps(cancel))
        logger.info("[%s] Đã huỷ %d algo orders.", symbol, len(cancel))

    def set_leverage(self, symbol: str, leverage: int):
        for pos_side in ("long", "short"):
            body = json.dumps({
                "instId":  symbol,
                "lever":   str(leverage),
                "mgnMode": "cross",
                "posSide": pos_side,
            })
            try:
                self._request("POST", "/api/v5/account/set-leverage", body)
                logger.info("[%s] ⚙️  Leverage %s x%d ✅", symbol, pos_side, leverage)
            except OKXError as e:
                logger.warning("[%s] Không set leverage %s: %s", symbol, pos_side, e)


# ══════════════════════════════════════════════════════════════
# 4. CHỈ BÁO KỸ THUẬT
# ══════════════════════════════════════════════════════════════

def compute_indicators(candles: list, cfg: dict) -> pd.DataFrame:
    df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close",
                                         "vol", "volCcy", "volCcyQuote", "confirm"])
    for col in ["open", "high", "low", "close", "vol"]:
        df[col] = df[col].astype(float)
    df["ts"] = pd.to_datetime(df["ts"].astype(float), unit="ms", utc=True)
    df.set_index("ts", inplace=True)
    df = df[df["confirm"] == "1"].copy()

    df["ema_fast"]  = df["close"].ewm(span=cfg["ema_fast"],  adjust=False).mean()
    df["ema_slow"]  = df["close"].ewm(span=cfg["ema_slow"],  adjust=False).mean()
    df["ema_trend"] = df["close"].ewm(span=cfg["ema_trend"], adjust=False).mean()

    hl  = df["high"] - df["low"]
    hpc = (df["high"] - df["close"].shift(1)).abs()
    lpc = (df["low"]  - df["close"].shift(1)).abs()
    df["atr"] = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)\
                  .ewm(span=cfg["atr_period"], adjust=False).mean()

    df["cross_up"]   = (df["ema_fast"] > df["ema_slow"]) & \
                       (df["ema_fast"].shift(1) <= df["ema_slow"].shift(1))
    df["cross_down"] = (df["ema_fast"] < df["ema_slow"]) & \
                       (df["ema_fast"].shift(1) >= df["ema_slow"].shift(1))
    df["uptrend"]    = df["close"] > df["ema_trend"]
    df["downtrend"]  = df["close"] < df["ema_trend"]

    return df.dropna()


def get_signal(df: pd.DataFrame, cfg: dict):
    last = df.iloc[-1]
    signal = None
    if last["cross_up"]   and last["uptrend"]:   signal = "long"
    elif last["cross_down"] and last["downtrend"]: signal = "short"

    if signal is None:
        return None, None, None, None, None

    entry = last["close"]
    atr   = last["atr"]
    sl = entry - cfg["atr_sl_mult"] * atr if signal == "long" \
         else entry + cfg["atr_sl_mult"] * atr
    tp = entry + cfg["atr_tp_mult"] * atr if signal == "long" \
         else entry - cfg["atr_tp_mult"] * atr

    return signal, entry, sl, tp, atr


# ══════════════════════════════════════════════════════════════
# 5. TRẠNG THÁI BOT (dùng chung, thread-safe)
# ══════════════════════════════════════════════════════════════

class BotState:
    def __init__(self, initial_equity: float):
        self.initial_equity = initial_equity
        self.peak_equity    = initial_equity
        self.daily_start_eq = initial_equity
        self.daily_date     = datetime.now(timezone.utc).date()
        self.total_trades   = 0
        self.wins           = 0
        self.losses         = 0
        self.running        = True
        self._lock          = threading.Lock()

        # Per-coin state
        self.last_signal_bar = {sym: None for sym in COINS}

    def update_equity(self, equity: float):
        with self._lock:
            self.peak_equity = max(self.peak_equity, equity)
            today = datetime.now(timezone.utc).date()
            if today != self.daily_date:
                self.daily_date     = today
                self.daily_start_eq = equity
                logger.info("📅 Ngày mới – reset daily stats. Equity: $%.2f", equity)

    def drawdown(self, equity):
        return (self.peak_equity - equity) / self.peak_equity

    def daily_loss(self, equity):
        return (self.daily_start_eq - equity) / self.daily_start_eq

    def check_stop_conditions(self, equity: float) -> bool:
        dd = self.drawdown(equity)
        dl = self.daily_loss(equity)
        if dd >= MAX_DRAWDOWN_STOP:
            logger.critical("🛑 MAX DRAWDOWN %.1f%% đạt ngưỡng! Dừng bot.", dd * 100)
            return True
        if dl >= MAX_DAILY_LOSS:
            logger.warning("⛔ Daily loss %.1f%% đạt ngưỡng! Dừng hôm nay.", dl * 100)
            return True
        return False

    def log_stats(self, equity: float):
        with self._lock:
            total = self.wins + self.losses
            wr    = self.wins / total * 100 if total > 0 else 0
            pnl_pct = (equity - self.initial_equity) / self.initial_equity * 100
            logger.info(
                "📊 Stats | Equity: $%.2f | PnL: %+.2f%% | "
                "Trades: %d | WR: %.1f%% | DD: %.1f%%",
                equity, pnl_pct, total, wr, self.drawdown(equity) * 100
            )


# ══════════════════════════════════════════════════════════════
# 6. LOGIC MỖI COIN (chạy trong thread riêng)
# ══════════════════════════════════════════════════════════════

def calculate_size(equity: float, entry: float, sl: float, cfg: dict) -> int:
    """
    Tính số contract dựa trên risk % vốn.
    ct_val = giá trị 1 contract tính bằng đơn vị tài sản.
      XAU-USDT-SWAP: ct_val=1  → 1 contract = 1 oz
      SOL-USDT-SWAP: ct_val=1  → 1 contract = 1 SOL
    Nếu OKX dùng ct_val khác (ví dụ 10 SOL/contract) thì chỉnh ct_val=10.
    """
    ct_val      = cfg.get("ct_val", 1)
    risk_amount = equity * RISK_PER_TRADE
    sl_distance = abs(entry - sl)
    if sl_distance <= 0 or ct_val <= 0:
        return 0
    # PnL mỗi contract khi chạm SL = sl_distance * ct_val
    size = (risk_amount * LEVERAGE) / (sl_distance * ct_val)
    return max(1, round(size))


def is_trade_hour() -> bool:
    return datetime.now(timezone.utc).hour in TRADE_HOURS_UTC


def coin_loop(symbol: str, cfg: dict, client: OKXClient, state: BotState):
    """Vòng lặp riêng cho mỗi coin — chạy trong thread."""
    logger.info("[%s] 🚀 Thread khởi động | EMA %d/%d/%d | SL %.1fx | TP %.1fx",
                symbol, cfg["ema_fast"], cfg["ema_slow"], cfg["ema_trend"],
                cfg["atr_sl_mult"], cfg["atr_tp_mult"])

    loop_count = 0
    while state.running:
        loop_count += 1
        try:
            _coin_tick(symbol, cfg, client, state, loop_count)
        except OKXError as e:
            logger.error("[%s] OKX Error: %s", symbol, e)
            time.sleep(30)
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.warning("[%s] 🌐 Mạng lỗi, thử lại sau 30s: %s", symbol, type(e).__name__)
            time.sleep(30)
        except Exception as e:
            logger.exception("[%s] Lỗi không mong đợi: %s", symbol, e)
            time.sleep(30)

        if state.running:
            time.sleep(CHECK_INTERVAL)

    # Dừng an toàn coin này
    try:
        client.cancel_algo_orders(symbol)
        logger.info("[%s] ✅ Đã huỷ algo orders khi dừng.", symbol)
    except Exception as e:
        logger.error("[%s] Lỗi khi dừng: %s", symbol, e)


def _coin_tick(symbol: str, cfg: dict, client: OKXClient,
               state: BotState, loop_count: int):

    # Kiểm tra giờ trade
    if not is_trade_hour():
        if loop_count % 10 == 0:
            logger.info("[%s] 🕐 Ngoài giờ trade (UTC %dh). Đang chờ...",
                        symbol, datetime.now(timezone.utc).hour)
        return

    # Kiểm tra vị thế hiện tại
    positions = client.get_positions(symbol)
    if positions:
        pos = positions[0]
        logger.debug("[%s] 📌 Đang giữ: %s | PnL: %s USDT",
                     symbol, pos.get("posSide"), pos.get("upl"))
        return

    # Lấy nến + tính toán
    candles = client.get_candles(symbol, cfg["timeframe"], limit=250)
    df      = compute_indicators(candles, cfg)

    if len(df) < cfg["ema_trend"] + 10:
        logger.warning("[%s] Không đủ dữ liệu nến (%d).", symbol, len(df))
        return

    signal, entry, sl, tp, atr = get_signal(df, cfg)

    last_bar_ts = df.index[-1]
    if signal and last_bar_ts == state.last_signal_bar[symbol]:
        return

    if signal is None:
        logger.debug("[%s] Không có tín hiệu.", symbol)
        return

    # Lấy equity để tính size
    equity = client.get_equity()
    state.update_equity(equity)

    if state.check_stop_conditions(equity):
        state.running = False
        return

    size = calculate_size(equity, entry, sl, cfg)
    if size <= 0:
        logger.warning("[%s] Size = 0. Bỏ qua.", symbol)
        return

    rr = cfg["atr_tp_mult"] / cfg["atr_sl_mult"]
    logger.info("[%s] 🎯 %s | Entry: %.4f | SL: %.4f | TP: %.4f | ATR: %.4f | R:R 1:%.1f | Size: %d",
                symbol, signal.upper(), entry, sl, tp, atr, rr, size)

    side = "buy" if signal == "long" else "sell"
    try:
        client.place_order(
            symbol    = symbol,
            side      = side,
            size      = size,
            sl_price  = round(sl, 4),
            tp_price  = round(tp, 4),
        )
        state.last_signal_bar[symbol] = last_bar_ts
        with state._lock:
            state.total_trades += 1
        logger.info("[%s] ✅ Lệnh đặt thành công!", symbol)

    except OKXError as e:
        logger.error("[%s] ❌ Đặt lệnh thất bại: %s", symbol, e)
        state.last_signal_bar[symbol] = last_bar_ts


# ══════════════════════════════════════════════════════════════
# 7. MAIN
# ══════════════════════════════════════════════════════════════

def run_bot():
    logger.info("=" * 60)
    logger.info("🤖 MULTI-COIN BOT KHỞI ĐỘNG")
    logger.info("   Coins:     %s", ", ".join(COINS.keys()))
    logger.info("   Leverage:  x%d", LEVERAGE)
    logger.info("   Risk/coin: %.0f%% | Max DD: %.0f%% | Daily loss: %.0f%%",
                RISK_PER_TRADE * 100, MAX_DRAWDOWN_STOP * 100, MAX_DAILY_LOSS * 100)
    logger.info("   Mode:      %s", "🔵 SIMULATED" if SIMULATED == "1" else "🔴 LIVE")
    logger.info("=" * 60)

    client = OKXClient()

    # Cài leverage cho tất cả coins
    for symbol in COINS:
        client.set_leverage(symbol, LEVERAGE)

    # Lấy equity ban đầu
    try:
        equity = client.get_equity()
        logger.info("💰 Equity khởi động: $%.2f USDT", equity)
    except Exception as e:
        logger.critical("Không lấy được equity: %s", e)
        return

    state = BotState(equity)

    # Log stats định kỳ trong main thread
    def stats_loop():
        while state.running:
            time.sleep(CHECK_INTERVAL * 30)   # mỗi 30 phút
            if state.running:
                try:
                    eq = client.get_equity()
                    state.log_stats(eq)
                except Exception:
                    pass

    # Khởi động threads
    threads = []

    stats_thread = threading.Thread(target=stats_loop, daemon=True, name="stats")
    stats_thread.start()

    for symbol, cfg in COINS.items():
        t = threading.Thread(
            target=coin_loop,
            args=(symbol, cfg, client, state),
            daemon=True,
            name=symbol,
        )
        t.start()
        threads.append(t)
        time.sleep(2)   # stagger start để tránh rate limit

    logger.info("✅ %d coin threads đang chạy. Ctrl+C để dừng.", len(threads))

    try:
        while state.running:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\n⌨️  Ctrl+C – Đang dừng an toàn...")
        state.running = False

    # Chờ threads dừng
    for t in threads:
        t.join(timeout=10)

    try:
        equity = client.get_equity()
        state.log_stats(equity)
        logger.info("🏁 Bot đã dừng. Equity cuối: $%.2f", equity)
    except Exception:
        logger.info("🏁 Bot đã dừng.")


if __name__ == "__main__":
    run_bot()