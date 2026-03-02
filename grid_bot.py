"""
╔══════════════════════════════════════════════════════════════╗
║         GRID BOT – XAU/USD Futures trên OKX                 ║
║         Chiến lược: Grid Trading tự động theo ATR            ║
║                                                              ║
║  Chạy:   python grid_bot.py                                  ║
║  Dừng:   Ctrl+C  (tự động huỷ toàn bộ lưới)                ║
╚══════════════════════════════════════════════════════════════╝

NGUYÊN LÝ HOẠT ĐỘNG:
  1. Lấy giá hiện tại và ATR(14) khung 1H
  2. Tính range lưới = ATR × ATR_RANGE_MULT (mặc định 3x)
  3. Chia đều 10 mức giá trong range → grid_levels
  4. Đặt lệnh limit BUY ở các mức dưới giá hiện tại
     Đặt lệnh limit SELL ở các mức trên giá hiện tại
  5. Khi một lệnh khớp → đặt lệnh đối chiều (buy→sell, sell→buy)
     với khoảng cách = grid_step để chốt lời
  6. Nếu giá thoát ra ngoài range → reset toàn bộ lưới

THAM SỐ QUAN TRỌNG:
  GRID_LEVELS      = 10     mức lưới
  ATR_RANGE_MULT   = 3.0    range = ATR × 3
  SIZE_PER_GRID    = 1      contract/lệnh
  MAX_DRAWDOWN     = 20%    dừng nếu lỗ quá
"""

import time
import logging
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

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

SYMBOL    = "XAU-USDT-SWAP"
TIMEFRAME = "1H"

# ── Grid settings ─────────────────────────────────────────────
GRID_LEVELS      = 10      # số mức lưới
ATR_PERIOD       = 14      # ATR period
ATR_RANGE_MULT   = 3.0     # range lưới = ATR × này (rộng hơn = ít reset hơn)
SIZE_PER_GRID    = 1       # số contract mỗi lệnh lưới (tối thiểu OKX = 1)

# ── Quản lý rủi ro ────────────────────────────────────────────
MAX_DRAWDOWN_STOP = 0.20   # dừng bot nếu drawdown > 20%
MAX_DAILY_LOSS    = 0.05   # dừng hôm nay nếu lỗ > 5%

# ── Vận hành ──────────────────────────────────────────────────
CHECK_INTERVAL    = 30     # kiểm tra mỗi 30s (grid cần nhanh hơn EMA bot)
RESET_THRESHOLD   = 0.7    # reset lưới nếu giá thoát > 70% range


# ══════════════════════════════════════════════════════════════
# 2. LOGGING (giờ Việt Nam)
# ══════════════════════════════════════════════════════════════

VN_TZ = timezone(timedelta(hours=7))

class VNFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=VN_TZ)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

log_dir  = Path("logs")
log_dir.mkdir(exist_ok=True)
log_file = log_dir / f"grid_{datetime.now(VN_TZ).strftime('%Y%m%d_%H%M%S')}.log"

fmt     = VNFormatter("%(asctime)s [%(levelname)s] %(message)s")
handler_file   = logging.FileHandler(log_file, encoding="utf-8")
handler_stream = logging.StreamHandler(sys.stdout)
handler_file.setFormatter(fmt)
handler_stream.setFormatter(fmt)

logging.basicConfig(level=logging.INFO, handlers=[handler_file, handler_stream])
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# 3. OKX CLIENT
# ══════════════════════════════════════════════════════════════

class OKXError(Exception):
    def __init__(self, code, message):
        self.code    = code
        self.message = message
        super().__init__(f"OKX [{code}]: {message}")


class OKXClient:
    TIMEOUT     = 20
    MAX_RETRIES = 3

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

    def get_ticker(self) -> float:
        """Lấy giá hiện tại."""
        path = f"/api/v5/market/ticker?instId={SYMBOL}"
        r    = requests.get(self.base_url + path, timeout=self.TIMEOUT)
        data = r.json()
        return float(data["data"][0]["last"])

    def get_candles(self, limit=50) -> list:
        path = f"/api/v5/market/candles?instId={SYMBOL}&bar={TIMEFRAME}&limit={limit}"
        r    = requests.get(self.base_url + path, timeout=self.TIMEOUT)
        data = r.json()
        if "data" not in data or not data["data"]:
            raise ValueError(f"Không lấy được nến: {data}")
        return list(reversed(data["data"]))

    def get_equity(self) -> float:
        data  = self._request("GET", "/api/v5/account/balance")
        total = 0.0
        for acc in data.get("data", []):
            for d in acc.get("details", []):
                if d.get("ccy") == "USDT":
                    total += float(d.get("eq", 0))
        return total

    def get_open_orders(self) -> list:
        path = f"/api/v5/trade/orders-pending?instId={SYMBOL}&ordType=limit"
        data = self._request("GET", path)
        return data.get("data", [])

    def get_positions(self) -> list:
        path = f"/api/v5/account/positions?instId={SYMBOL}"
        data = self._request("GET", path)
        return [p for p in data.get("data", []) if float(p.get("pos", 0)) != 0]

    def place_limit_order(self, side: str, price: float, size: int) -> Optional[str]:
        """Đặt lệnh limit, trả về ordId."""
        pos_side = "long" if side == "buy" else "short"
        body = json.dumps({
            "instId":  SYMBOL,
            "tdMode":  "cross",
            "side":    side,
            "posSide": pos_side,
            "ordType": "limit",
            "px":      str(round(price, 2)),
            "sz":      str(size),
        })
        try:
            data = self._request("POST", "/api/v5/trade/order", body)
            ord_id = data["data"][0]["ordId"]
            logger.info("📌 %s limit @ %.2f | ordId: %s", side.upper(), price, ord_id)
            return ord_id
        except OKXError as e:
            logger.error("❌ Đặt lệnh %s @ %.2f thất bại: %s", side, price, e)
            return None

    def cancel_order(self, ord_id: str) -> bool:
        body = json.dumps([{"instId": SYMBOL, "ordId": ord_id}])
        try:
            self._request("POST", "/api/v5/trade/cancel-batch-orders", body)
            return True
        except OKXError:
            return False

    def cancel_all_orders(self):
        orders = self.get_open_orders()
        if not orders:
            return 0
        cancel = [{"instId": SYMBOL, "ordId": o["ordId"]} for o in orders]
        try:
            self._request("POST", "/api/v5/trade/cancel-batch-orders", json.dumps(cancel))
            logger.info("🗑 Đã huỷ %d lệnh lưới.", len(cancel))
        except OKXError as e:
            logger.error("Lỗi huỷ lệnh: %s", e)
        return len(cancel)

    def close_all_positions(self):
        for pos_side in ("long", "short"):
            body = json.dumps({"instId": SYMBOL, "mgnMode": "cross", "posSide": pos_side})
            try:
                self._request("POST", "/api/v5/trade/close-position", body)
                logger.info("🔒 Đóng vị thế %s.", pos_side)
            except OKXError:
                pass


# ══════════════════════════════════════════════════════════════
# 4. TÍNH ATR VÀ THIẾT KẾ LƯỚI
# ══════════════════════════════════════════════════════════════

def compute_atr(client: OKXClient) -> float:
    candles = client.get_candles(limit=50)
    df = pd.DataFrame(candles, columns=["ts","open","high","low","close","vol","volCcy","volCcyQuote","confirm"])
    for c in ["high","low","close"]:
        df[c] = df[c].astype(float)
    hl  = df["high"] - df["low"]
    hpc = (df["high"] - df["close"].shift(1)).abs()
    lpc = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    atr = tr.ewm(span=ATR_PERIOD, adjust=False).mean().iloc[-1]
    return atr


def build_grid(current_price: float, atr: float) -> dict:
    """
    Tạo lưới 10 mức xung quanh giá hiện tại.
    Returns: {
        grid_low, grid_high, grid_step,
        levels: [price1, price2, ...],   # từ thấp đến cao
        buy_levels: [...],               # dưới giá hiện tại
        sell_levels: [...],              # trên giá hiện tại
    }
    """
    half_range = atr * ATR_RANGE_MULT / 2
    grid_low   = round(current_price - half_range, 2)
    grid_high  = round(current_price + half_range, 2)
    grid_step  = round((grid_high - grid_low) / GRID_LEVELS, 2)

    levels = [round(grid_low + i * grid_step, 2) for i in range(GRID_LEVELS + 1)]

    buy_levels  = [p for p in levels if p < current_price]
    sell_levels = [p for p in levels if p > current_price]

    return {
        "grid_low":    grid_low,
        "grid_high":   grid_high,
        "grid_step":   grid_step,
        "levels":      levels,
        "buy_levels":  buy_levels,
        "sell_levels": sell_levels,
        "center":      current_price,
        "atr":         atr,
    }


# ══════════════════════════════════════════════════════════════
# 5. GRID STATE
# ══════════════════════════════════════════════════════════════

class GridState:
    def __init__(self, equity: float, grid: dict):
        self.initial_equity  = equity
        self.peak_equity     = equity
        self.daily_start_eq  = equity
        self.daily_date      = datetime.now(VN_TZ).date()

        self.grid            = grid
        self.active_orders   = {}   # ordId → {"side", "price", "filled": bool}
        self.filled_count    = 0
        self.profit_realized = 0.0
        self.running         = True
        self.loop_count      = 0

    def update_equity(self, equity: float):
        self.peak_equity = max(self.peak_equity, equity)
        today = datetime.now(VN_TZ).date()
        if today != self.daily_date:
            self.daily_date    = today
            self.daily_start_eq = equity
            logger.info("📅 Ngày mới – reset daily. Equity: $%.2f", equity)

    def drawdown(self, equity: float) -> float:
        return (self.peak_equity - equity) / self.peak_equity if self.peak_equity > 0 else 0

    def daily_loss(self, equity: float) -> float:
        return (self.daily_start_eq - equity) / self.daily_start_eq if self.daily_start_eq > 0 else 0

    def check_stop(self, equity: float) -> bool:
        dd = self.drawdown(equity)
        dl = self.daily_loss(equity)
        if dd >= MAX_DRAWDOWN_STOP:
            logger.critical("🛑 DRAWDOWN %.1f%% vượt ngưỡng! Dừng bot.", dd * 100)
            return True
        if dl >= MAX_DAILY_LOSS:
            logger.warning("⛔ Daily loss %.1f%% vượt ngưỡng! Dừng hôm nay.", dl * 100)
            return True
        return False

    def price_out_of_range(self, price: float) -> bool:
        g = self.grid
        margin = (g["grid_high"] - g["grid_low"]) * RESET_THRESHOLD
        return price < g["grid_low"] - margin or price > g["grid_high"] + margin

    def log_stats(self, equity: float):
        pnl_pct = (equity - self.initial_equity) / self.initial_equity * 100
        logger.info(
            "📊 Grid Stats | Equity: $%.2f | PnL: %+.2f%% | "
            "Filled: %d | DD: %.1f%% | Range: %.2f–%.2f | Step: %.2f",
            equity, pnl_pct, self.filled_count,
            self.drawdown(equity) * 100,
            self.grid["grid_low"], self.grid["grid_high"], self.grid["grid_step"]
        )


# ══════════════════════════════════════════════════════════════
# 6. LOGIC CHÍNH
# ══════════════════════════════════════════════════════════════

def setup_grid(client: OKXClient, state: GridState):
    """Huỷ lưới cũ và đặt lưới mới."""
    logger.info("🔧 Đang thiết lập lưới mới...")
    client.cancel_all_orders()
    time.sleep(2)

    grid = state.grid
    placed = 0

    # Đặt lệnh BUY ở các mức dưới giá
    for price in grid["buy_levels"]:
        ord_id = client.place_limit_order("buy", price, SIZE_PER_GRID)
        if ord_id:
            state.active_orders[ord_id] = {"side": "buy", "price": price, "filled": False}
            placed += 1
        time.sleep(0.2)

    # Đặt lệnh SELL ở các mức trên giá
    for price in grid["sell_levels"]:
        ord_id = client.place_limit_order("sell", price, SIZE_PER_GRID)
        if ord_id:
            state.active_orders[ord_id] = {"side": "sell", "price": price, "filled": False}
            placed += 1
        time.sleep(0.2)

    logger.info(
        "✅ Lưới sẵn sàng | %d lệnh | Range: %.2f–%.2f | Step: %.2f | ATR: %.2f",
        placed, grid["grid_low"], grid["grid_high"], grid["grid_step"], grid["atr"]
    )


def check_filled_orders(client: OKXClient, state: GridState):
    """Kiểm tra lệnh nào đã khớp → đặt lệnh đối chiều."""
    open_order_ids = {o["ordId"] for o in client.get_open_orders()}

    for ord_id, info in list(state.active_orders.items()):
        if info["filled"]:
            continue

        # Nếu ordId không còn trong open orders → đã khớp
        if ord_id not in open_order_ids:
            info["filled"] = True
            state.filled_count += 1
            filled_price = info["price"]
            filled_side  = info["side"]

            logger.info(
                "🎯 Lệnh KHỚP! %s @ %.2f | Tổng khớp: %d",
                filled_side.upper(), filled_price, state.filled_count
            )

            # Đặt lệnh đối chiều để chốt lời
            step = state.grid["grid_step"]
            if filled_side == "buy":
                tp_price = round(filled_price + step, 2)
                new_side = "sell"
            else:
                tp_price = round(filled_price - step, 2)
                new_side = "buy"

            # Kiểm tra tp_price còn trong range không
            g = state.grid
            if g["grid_low"] <= tp_price <= g["grid_high"]:
                new_id = client.place_limit_order(new_side, tp_price, SIZE_PER_GRID)
                if new_id:
                    state.active_orders[new_id] = {
                        "side": new_side, "price": tp_price, "filled": False
                    }
                    logger.info("↩️  Đặt lệnh đối chiều %s @ %.2f", new_side.upper(), tp_price)
            else:
                logger.warning("⚠️ TP price %.2f ngoài range, bỏ qua.", tp_price)


def reset_grid(client: OKXClient, state: GridState, current_price: float):
    """Reset lưới khi giá thoát range."""
    logger.warning(
        "🔄 Giá %.2f thoát range [%.2f–%.2f]. Reset lưới...",
        current_price, state.grid["grid_low"], state.grid["grid_high"]
    )
    atr       = compute_atr(client)
    new_grid  = build_grid(current_price, atr)
    state.grid = new_grid
    state.active_orders = {}
    setup_grid(client, state)


# ══════════════════════════════════════════════════════════════
# 7. MAIN LOOP
# ══════════════════════════════════════════════════════════════

def run_grid_bot():
    logger.info("=" * 60)
    logger.info("🤖 GRID BOT XAU/USD KHỞI ĐỘNG")
    logger.info("   Symbol:     %s", SYMBOL)
    logger.info("   Grid:       %d levels | ATR×%.1f range", GRID_LEVELS, ATR_RANGE_MULT)
    logger.info("   Size/grid:  %d contract | Check: %ds", SIZE_PER_GRID, CHECK_INTERVAL)
    logger.info("   Max DD:     %.0f%% | Max daily loss: %.0f%%",
                MAX_DRAWDOWN_STOP * 100, MAX_DAILY_LOSS * 100)
    logger.info("   Mode:       %s", "🔵 SIMULATED" if SIMULATED == "1" else "🔴 LIVE")
    logger.info("=" * 60)

    client = OKXClient()

    # Khởi động
    try:
        equity = client.get_equity()
        logger.info("💰 Equity: $%.2f USDT", equity)
        current_price = client.get_ticker()
        logger.info("💹 Giá hiện tại: %.2f", current_price)
        atr = compute_atr(client)
        logger.info("📐 ATR(14): %.2f | Range lưới: ±%.2f", atr, atr * ATR_RANGE_MULT / 2)
    except Exception as e:
        logger.critical("Khởi động thất bại: %s", e)
        return

    grid  = build_grid(current_price, atr)
    state = GridState(equity, grid)

    # Thiết lập lưới ban đầu
    setup_grid(client, state)

    # Main loop
    while state.running:
        state.loop_count += 1
        try:
            # 1. Equity & stop check
            equity = client.get_equity()
            state.update_equity(equity)

            if state.check_stop(equity):
                state.running = False
                break

            # 2. Log stats mỗi 20 ticks (~10 phút)
            if state.loop_count % 20 == 0:
                state.log_stats(equity)

            # 3. Giá hiện tại
            current_price = client.get_ticker()

            # 4. Kiểm tra lệnh đã khớp
            check_filled_orders(client, state)

            # 5. Reset nếu giá thoát range
            if state.price_out_of_range(current_price):
                reset_grid(client, state, current_price)

        except KeyboardInterrupt:
            logger.info("\n⌨️  Ctrl+C – Đang dừng an toàn...")
            state.running = False
            break
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.warning("🌐 Mạng lỗi tạm thời: %s", type(e).__name__)
            time.sleep(30)
        except OKXError as e:
            logger.error("OKX Error: %s", e)
            time.sleep(30)
        except Exception as e:
            logger.exception("Lỗi không mong đợi: %s", e)
            time.sleep(30)

        time.sleep(CHECK_INTERVAL)

    # Shutdown
    logger.info("🔒 Đang dừng – huỷ toàn bộ lưới...")
    client.cancel_all_orders()
    client.close_all_positions()

    equity = client.get_equity()
    state.log_stats(equity)
    logger.info("🏁 Grid bot đã dừng. Equity cuối: $%.2f", equity)


# ══════════════════════════════════════════════════════════════
# 8. ENTRY POINT
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    run_grid_bot()
