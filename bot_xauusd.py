"""
╔══════════════════════════════════════════════════════════════╗
║         LIVE BOT – XAU/USD Futures trên OKX                 ║
║         Chiến lược: EMA 15/26/80 + ATR (SL 2.5x, TP 4x) ok   ║
║                                                              ║
║  Chạy:   python bot_xauusd.py                               ║
║  Dừng:   Ctrl+C                                             ║
╚══════════════════════════════════════════════════════════════╝
"""

import time
import logging
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import hmac
import hashlib
import base64
import pandas as pd
import numpy as np

# ══════════════════════════════════════════════════════════════
# 1. CẤU HÌNH – CHỈNH TẠI ĐÂY
# ══════════════════════════════════════════════════════════════

# OKX credentials — đọc từ config.py
try:
    from config import BASE_URL, API_KEY, API_SECRET, API_PASSPHRASE, SIMULATED
except ImportError:
    print("❌ Không tìm thấy config.py. Tạo file config.py với các biến:")
    print("   BASE_URL, API_KEY, API_SECRET, API_PASSPHRASE, SIMULATED")
    sys.exit(1)

# Symbol — XAU/USD perpetual swap trên OKX
SYMBOL    = "XAU-USDT-SWAP"
TIMEFRAME = "1H"          # OKX dùng "1H" cho khung 1 giờ

# Tham số chiến lược (tốt nhất từ grid search v2)
EMA_FAST     = 15   # 9  → 15
EMA_SLOW     = 26   # 34 → 26
EMA_TREND    = 80   # 100 → 80
ATR_PERIOD   = 14
ATR_SL_MULT  = 2.5  # 2.0 → 2.5
ATR_TP_MULT  = 4.0  # 3.0 → 4.0

# Quản lý vốn
RISK_PER_TRADE    = 0.01   # 1% vốn mỗi lệnh
MAX_DRAWDOWN_STOP = 0.20   # Dừng bot nếu drawdown > 20%
MAX_DAILY_LOSS    = 0.05   # Dừng trong ngày nếu lỗ > 5% vốn

# Giờ trade (UTC) — 6h-17h UTC = 13h-00h giờ VN (mở rộng thêm 1h London open)
TRADE_HOURS_UTC = list(range(6, 18))

# Chu kỳ kiểm tra (giây) — 60s để tránh spam API
CHECK_INTERVAL = 60

# ══════════════════════════════════════════════════════════════
# 2. LOGGING
# ══════════════════════════════════════════════════════════════

VN_TZ = timezone(timedelta(hours=7))

log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)
log_file = log_dir / f"bot_{datetime.now(VN_TZ).strftime('%Y%m%d_%H%M%S')}.log"

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
# 3. OKX CLIENT (tích hợp sẵn, không cần import file khác)
# ══════════════════════════════════════════════════════════════

class OKXError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(f"OKX [{code}]: {message}")


class OKXClient:
    TIMEOUT     = 20    # tăng từ 10s lên 20s cho EC2
    MAX_RETRIES = 3     # tự retry khi timeout

    def __init__(self):
        self.base_url  = BASE_URL
        self.simulated = SIMULATED

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
            "OK-ACCESS-KEY":       API_KEY,
            "OK-ACCESS-SIGN":      self._sign(ts, method, path, body),
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": API_PASSPHRASE,
            "Content-Type":        "application/json",
            "x-simulated-trading": self.simulated,
        }

    def _request(self, method, path, body=""):
        url     = self.base_url + path
        headers = self._headers(method, path, body)

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
                logger.warning("⏱ Timeout lần %d/%d: %s %s", attempt, self.MAX_RETRIES, method, path)
                if attempt == self.MAX_RETRIES:
                    raise
                time.sleep(5 * attempt)   # chờ 5s, 10s trước khi retry

            except requests.RequestException as e:
                logger.warning("🌐 Lỗi mạng lần %d/%d: %s", attempt, self.MAX_RETRIES, e)
                if attempt == self.MAX_RETRIES:
                    raise
                time.sleep(5 * attempt)

    def get_candles(self, limit=200):
        path = f"/api/v5/market/candles?instId={SYMBOL}&bar={TIMEFRAME}&limit={limit}"
        r = requests.get(self.base_url + path, timeout=self.TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if "data" not in data or not data["data"]:
            raise ValueError(f"Không lấy được nến: {data}")
        # OKX trả mới nhất trước → đảo lại
        return list(reversed(data["data"]))

    def get_equity(self):
        data  = self._request("GET", "/api/v5/account/balance")
        total = 0.0
        for acc in data.get("data", []):
            for d in acc.get("details", []):
                if d.get("ccy") == "USDT":
                    total += float(d.get("eq", 0))
        return total

    def get_positions(self):
        path = f"/api/v5/account/positions?instId={SYMBOL}"
        data = self._request("GET", path)
        return [p for p in data.get("data", []) if float(p.get("pos", 0)) != 0]

    def get_open_orders(self):
        path = f"/api/v5/trade/orders-pending?instId={SYMBOL}"
        data = self._request("GET", path)
        return data.get("data", [])

    def place_order(self, side, size, ord_type="market", price=None,
                    sl_price=None, tp_price=None):
        """
        Bước 1: Đặt lệnh market/limit thuần (không kèm SL/TP).
        Bước 2: Đặt SL/TP riêng qua algo order sau khi lệnh chính thành công.
        OKX demo không hỗ trợ gắn SL/TP trực tiếp vào lệnh market.
        """
        pos_side = "long" if side == "buy" else "short"

        # ── Bước 1: Lệnh chính ───────────────────────────────────
        body_dict = {
            "instId":  SYMBOL,
            "tdMode":  "cross",
            "side":    side,
            "posSide": pos_side,
            "ordType": ord_type,
            "sz":      str(size),
        }
        if price:
            body_dict["px"] = str(price)

        body = json.dumps(body_dict)
        response = self._request("POST", "/api/v5/trade/order", body)

        # ── Bước 2: Đặt SL/TP riêng (algo order) ────────────────
        if sl_price or tp_price:
            time.sleep(1)   # chờ lệnh chính khớp trước
            self._place_algo_sltp(pos_side, size, sl_price, tp_price)

        return response

    def _place_algo_sltp(self, pos_side, size, sl_price=None, tp_price=None):
        """
        Đặt SL/TP riêng qua conditional orders.
        OKX demo không hỗ trợ OCO → đặt SL và TP thành 2 lệnh riêng.
        SL/TP phải tính từ entry thật, không phải giá ticker.
        """
        # Lấy entry price thật từ vị thế
        try:
            positions = self.get_positions()
            if positions:
                entry = float(positions[0]["avgPx"])
                atr_sl = abs(sl_price - entry) if sl_price else 0
                atr_tp = abs(tp_price - entry) if tp_price else 0
                # Tính lại SL/TP từ entry thật
                if pos_side == "long":
                    sl_price = round(entry - atr_sl, 2) if sl_price else None
                    tp_price = round(entry + atr_tp, 2) if tp_price else None
                else:
                    sl_price = round(entry + atr_sl, 2) if sl_price else None
                    tp_price = round(entry - atr_tp, 2) if tp_price else None
                logger.info("📌 Entry thật: %.2f | SL: %s | TP: %s", entry, sl_price, tp_price)
        except Exception as e:
            logger.warning("Không lấy được entry thật: %s", e)

        # Đặt SL và TP thành 2 lệnh conditional riêng biệt
        self._place_single_algo(pos_side, size, sl_price, is_sl=True)
        self._place_single_algo(pos_side, size, tp_price, is_sl=False)

    def _place_single_algo(self, pos_side, size, trigger_price, is_sl: bool):
        """
        Đặt SL hoặc TP riêng lẻ.
        OKX quy tắc:
          - SL short: slTriggerPx > giá hiện tại  → ordType=conditional, chỉ dùng slTriggerPx
          - TP short: tpTriggerPx < giá hiện tại  → ordType=conditional, chỉ dùng tpTriggerPx
          - SL long:  slTriggerPx < giá hiện tại
          - TP long:  tpTriggerPx > giá hiện tại
        Không được gộp cả sl và tp trong cùng 1 conditional order.
        """
        if not trigger_price:
            return
        close_side = "sell" if pos_side == "long" else "buy"

        if is_sl:
            trigger_key = "slTriggerPx"
            ord_px_key  = "slOrdPx"
            type_key    = "slTriggerPxType"
        else:
            trigger_key = "tpTriggerPx"
            ord_px_key  = "tpOrdPx"
            type_key    = "tpTriggerPxType"

        body = json.dumps({
            "instId":    SYMBOL,
            "tdMode":    "cross",
            "side":      close_side,
            "posSide":   pos_side,
            "ordType":   "conditional",
            "sz":        str(size),
            trigger_key: str(round(trigger_price, 2)),
            ord_px_key:  "-1",   # market order khi chạm trigger
            type_key:    "last",
        })
        try:
            self._request("POST", "/api/v5/trade/order-algo", body)
            label = "SL" if is_sl else "TP"
            logger.info("🛡 %s đặt thành công @ %.2f", label, trigger_price)
        except OKXError as e:
            logger.error("❌ Đặt %s thất bại: %s", "SL" if is_sl else "TP", e)

    def close_position(self, pos_side):
        body = json.dumps({
            "instId":  SYMBOL,
            "mgnMode": "cross",
            "posSide": pos_side,
        })
        return self._request("POST", "/api/v5/trade/close-position", body)

    def cancel_all_orders(self):
        orders = self.get_open_orders()
        if not orders:
            return
        cancel = [{"instId": SYMBOL, "ordId": o["ordId"]} for o in orders]
        self._request("POST", "/api/v5/trade/cancel-batch-orders", json.dumps(cancel))
        logger.info("Đã huỷ %d lệnh chờ.", len(cancel))


# ══════════════════════════════════════════════════════════════
# 4. CHỈ BÁO KỸ THUẬT
# ══════════════════════════════════════════════════════════════

def compute_indicators(candles: list) -> pd.DataFrame:
    """Chuyển dữ liệu nến OKX thành DataFrame với đầy đủ chỉ báo."""
    df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close",
                                         "vol", "volCcy", "volCcyQuote", "confirm"])
    for col in ["open", "high", "low", "close", "vol"]:
        df[col] = df[col].astype(float)
    df["ts"] = pd.to_datetime(df["ts"].astype(float), unit="ms", utc=True)
    df.set_index("ts", inplace=True)

    # Chỉ lấy nến đã đóng (confirm == "1")
    df = df[df["confirm"] == "1"].copy()

    # EMA
    df["ema_fast"]  = df["close"].ewm(span=EMA_FAST,  adjust=False).mean()
    df["ema_slow"]  = df["close"].ewm(span=EMA_SLOW,  adjust=False).mean()
    df["ema_trend"] = df["close"].ewm(span=EMA_TREND, adjust=False).mean()

    # ATR
    hl  = df["high"] - df["low"]
    hpc = (df["high"] - df["close"].shift(1)).abs()
    lpc = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=ATR_PERIOD, adjust=False).mean()

    # Crossover signals (nến cuối cùng đã đóng)
    df["cross_up"]   = (df["ema_fast"] > df["ema_slow"]) & \
                       (df["ema_fast"].shift(1) <= df["ema_slow"].shift(1))
    df["cross_down"] = (df["ema_fast"] < df["ema_slow"]) & \
                       (df["ema_fast"].shift(1) >= df["ema_slow"].shift(1))
    df["uptrend"]    = df["close"] > df["ema_trend"]
    df["downtrend"]  = df["close"] < df["ema_trend"]

    return df.dropna()


def get_signal(df: pd.DataFrame):
    """
    Phân tích nến cuối cùng đã đóng → trả về tín hiệu.
    Returns: ("long" | "short" | None, entry, sl, tp, atr)
    """
    last = df.iloc[-1]

    signal = None
    if last["cross_up"] and last["uptrend"]:
        signal = "long"
    elif last["cross_down"] and last["downtrend"]:
        signal = "short"

    if signal is None:
        return None, None, None, None, None

    entry = last["close"]
    atr   = last["atr"]

    if signal == "long":
        sl = entry - ATR_SL_MULT * atr
        tp = entry + ATR_TP_MULT * atr
    else:
        sl = entry + ATR_SL_MULT * atr
        tp = entry - ATR_TP_MULT * atr

    return signal, entry, sl, tp, atr


# ══════════════════════════════════════════════════════════════
# 5. QUẢN LÝ TRẠNG THÁI BOT
# ══════════════════════════════════════════════════════════════

class BotState:
    def __init__(self, initial_equity: float):
        self.initial_equity   = initial_equity
        self.peak_equity      = initial_equity
        self.daily_start_eq   = initial_equity
        self.daily_date       = datetime.now(timezone.utc).date()
        self.total_trades     = 0
        self.wins             = 0
        self.losses           = 0
        self.last_signal_bar  = None   # tránh vào lệnh cùng 1 nến 2 lần
        self.running          = True

    def update_equity(self, equity: float):
        self.peak_equity = max(self.peak_equity, equity)

        # Reset daily stats nếu qua ngày mới
        today = datetime.now(timezone.utc).date()
        if today != self.daily_date:
            self.daily_date     = today
            self.daily_start_eq = equity
            logger.info("📅 Ngày mới – reset daily stats. Equity: $%.2f", equity)

    def drawdown(self, equity: float) -> float:
        return (self.peak_equity - equity) / self.peak_equity

    def daily_loss(self, equity: float) -> float:
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
        total = self.wins + self.losses
        wr    = self.wins / total * 100 if total > 0 else 0
        pnl   = equity - self.initial_equity
        pnl_pct = pnl / self.initial_equity * 100
        logger.info(
            "📊 Stats | Equity: $%.2f | PnL: %+.2f%% | "
            "Trades: %d | WR: %.1f%% | DD: %.1f%%",
            equity, pnl_pct, total, wr, self.drawdown(equity) * 100
        )


# ══════════════════════════════════════════════════════════════
# 6. LOGIC CHÍNH CỦA BOT
# ══════════════════════════════════════════════════════════════

def calculate_size(equity: float, entry: float, sl: float) -> float:
    """Tính size lệnh dựa trên % vốn rủi ro."""
    risk_amount = equity * RISK_PER_TRADE
    sl_distance = abs(entry - sl)
    if sl_distance <= 0:
        return 0

    # OKX XAUUSDT-SWAP: 1 contract = 1 oz vàng
    # size = số contract
    size = risk_amount / sl_distance
    size = max(1, round(size))   # tối thiểu 1 contract, làm tròn
    return size


def is_trade_hour() -> bool:
    hour = datetime.now(timezone.utc).hour
    return hour in TRADE_HOURS_UTC


def run_bot():
    logger.info("=" * 60)
    logger.info("🤖 BOT XAU/USD KHỞI ĐỘNG")
    logger.info("   Symbol:    %s", SYMBOL)
    logger.info("   Strategy:  EMA %d/%d/%d | ATR %d | SL %.1fx | TP %.1fx",
                EMA_FAST, EMA_SLOW, EMA_TREND, ATR_PERIOD, ATR_SL_MULT, ATR_TP_MULT)
    logger.info("   Risk/trade: %.0f%% | Max DD: %.0f%% | Max daily loss: %.0f%%",
                RISK_PER_TRADE * 100, MAX_DRAWDOWN_STOP * 100, MAX_DAILY_LOSS * 100)
    logger.info("   Mode:      %s", "🔵 SIMULATED" if SIMULATED == "1" else "🔴 LIVE")
    logger.info("=" * 60)

    client = OKXClient()

    # Lấy equity ban đầu
    try:
        equity = client.get_equity()
        logger.info("💰 Equity khởi động: $%.2f USDT", equity)
    except Exception as e:
        logger.critical("Không lấy được equity: %s", e)
        return

    state = BotState(equity)
    loop_count = 0

    while state.running:
        loop_count += 1
        try:
            _tick(client, state, loop_count)
        except KeyboardInterrupt:
            logger.info("\n⌨️  Ctrl+C nhận được. Đang dừng bot an toàn...")
            state.running = False
            _safe_shutdown(client)
            break
        except OKXError as e:
            logger.error("OKX Error: %s", e)
            time.sleep(30)
        except (requests.Timeout, requests.ConnectionError) as e:
            # Lỗi mạng tạm thời — log ngắn gọn, không cần traceback
            logger.warning("🌐 Mạng tạm thời lỗi, thử lại sau 30s: %s", type(e).__name__)
            time.sleep(30)
        except Exception as e:
            logger.exception("Lỗi không mong đợi: %s", e)
            time.sleep(30)

        if state.running:
            logger.debug("⏳ Chờ %ds...", CHECK_INTERVAL)
            time.sleep(CHECK_INTERVAL)

    equity = client.get_equity()
    state.log_stats(equity)
    logger.info("🏁 Bot đã dừng. Equity cuối: $%.2f", equity)


def _tick(client: OKXClient, state: BotState, loop_count: int):
    """Mỗi tick: kiểm tra equity, tín hiệu, và quản lý lệnh."""

    # Cập nhật equity
    equity = client.get_equity()
    state.update_equity(equity)

    # Kiểm tra điều kiện dừng
    if state.check_stop_conditions(equity):
        state.running = False
        _safe_shutdown(client)
        return

    # Log stats mỗi 30 ticks (~30 phút)
    if loop_count % 30 == 0:
        state.log_stats(equity)

    # Kiểm tra giờ trade
    if not is_trade_hour():
        if loop_count % 10 == 0:
            logger.info("🕐 Ngoài giờ trade (UTC %dh). Đang chờ...",
                        datetime.now(timezone.utc).hour)
        return

    # Kiểm tra có đang giữ vị thế không
    positions = client.get_positions()
    has_position = len(positions) > 0

    if has_position:
        pos = positions[0]
        logger.debug(
            "📌 Đang giữ vị thế: %s | Size: %s | PnL: %s USDT",
            pos.get("posSide"), pos.get("pos"), pos.get("upl")
        )
        return   # Đang giữ lệnh → không tìm tín hiệu mới

    # Lấy nến và tính toán
    candles = client.get_candles(limit=200)
    df      = compute_indicators(candles)

    if len(df) < EMA_TREND + 10:
        logger.warning("Không đủ dữ liệu nến (%d). Bỏ qua.", len(df))
        return

    # Lấy tín hiệu
    signal, entry, sl, tp, atr = get_signal(df)

    # Tránh vào lệnh cùng 1 nến 2 lần
    last_bar_ts = df.index[-1]
    if signal and last_bar_ts == state.last_signal_bar:
        logger.debug("Tín hiệu %s nhưng đã xử lý nến này rồi.", signal)
        return

    if signal is None:
        logger.debug("Không có tín hiệu. EMA fast=%.2f slow=%.2f trend=%.2f",
                     df["ema_fast"].iloc[-1], df["ema_slow"].iloc[-1], df["ema_trend"].iloc[-1])
        return

    # Tính size
    size = calculate_size(equity, entry, sl)
    if size <= 0:
        logger.warning("Size tính ra 0. Bỏ qua lệnh.")
        return

    rr = ATR_TP_MULT / ATR_SL_MULT
    logger.info("🎯 TÍN HIỆU %s | Entry: %.2f | SL: %.2f | TP: %.2f | ATR: %.2f | R:R 1:%.1f | Size: %d",
                signal.upper(), entry, sl, tp, atr, rr, size)

    # Xác nhận và đặt lệnh
    side = "buy" if signal == "long" else "sell"
    try:
        response = client.place_order(
            side      = side,
            size      = size,
            ord_type  = "market",
            sl_price  = round(sl, 2),
            tp_price  = round(tp, 2),
        )
        # Chỉ đánh dấu đã xử lý nến này khi lệnh thành công
        state.last_signal_bar = last_bar_ts
        state.total_trades   += 1
        logger.info("✅ Lệnh đặt thành công: %s", response)

    except OKXError as e:
        logger.error("❌ Đặt lệnh thất bại: %s", e)
        # Đánh dấu nến này để không spam retry liên tục
        # Sẽ thử lại ở nến tiếp theo
        state.last_signal_bar = last_bar_ts


def _safe_shutdown(client: OKXClient):
    """Dừng an toàn: huỷ lệnh chờ, KHÔNG đóng vị thế (để SL/TP tự xử lý)."""
    logger.info("🔒 Đang dừng an toàn...")
    try:
        client.cancel_all_orders()
    except Exception as e:
        logger.error("Lỗi khi huỷ lệnh: %s", e)
    logger.info("✅ Đã huỷ lệnh chờ. Vị thế đang mở vẫn giữ nguyên (SL/TP tự quản lý).")


# ══════════════════════════════════════════════════════════════
# 7. ENTRY POINT
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    run_bot()