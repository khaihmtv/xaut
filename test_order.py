"""
Test đặt lệnh cho XAU-USDT-SWAP và SOL-USDT-SWAP
Chạy: python test_multi.py
"""

import json
import time
import hmac
import hashlib
import base64
import sys
from datetime import datetime, timezone

import requests

try:
    from config import BASE_URL, API_KEY, API_SECRET, API_PASSPHRASE, SIMULATED
except ImportError:
    print("❌ Không tìm thấy config.py")
    sys.exit(1)

# ── Cấu hình test ─────────────────────────────────────────────
LEVERAGE       = 3
MARGIN_PCT     = 0.30   # 30% vốn

COINS = {
    "XAU-USDT-SWAP": {"ct_val": 0.001, "min_sz": 1,    "lot_sz": 1},
    "SOL-USDT-SWAP": {"ct_val": 1,     "min_sz": 0.01, "lot_sz": 0.01},
}

# ══════════════════════════════════════════════════════════════

class OKXClient:
    TIMEOUT = 20

    def __init__(self):
        self.base_url  = BASE_URL
        self.simulated = SIMULATED

    def _ts(self):
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

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

    def get(self, path):
        r = requests.get(self.base_url + path,
                         headers=self._headers("GET", path), timeout=self.TIMEOUT)
        r.raise_for_status()
        return r.json()

    def post(self, path, body: dict):
        body_str = json.dumps(body)
        r = requests.post(self.base_url + path,
                          headers=self._headers("POST", path, body_str),
                          data=body_str, timeout=self.TIMEOUT)
        r.raise_for_status()
        return r.json()

    def get_equity(self):
        data = self.get("/api/v5/account/balance")
        for acc in data.get("data", []):
            for d in acc.get("details", []):
                if d.get("ccy") == "USDT":
                    return float(d.get("eq", 0))
        return 0.0

    def get_ticker(self, symbol):
        data = self.get(f"/api/v5/market/ticker?instId={symbol}")
        return float(data["data"][0]["last"])

    def get_positions(self, symbol):
        data = self.get(f"/api/v5/account/positions?instId={symbol}")
        return [p for p in data.get("data", []) if float(p.get("pos", 0)) != 0]

    def cancel_algo_orders(self, symbol):
        path = f"/api/v5/trade/orders-algo-pending?instId={symbol}&ordType=conditional"
        data = self.get(path)
        orders = data.get("data", [])
        if not orders:
            return 0
        cancel = [{"instId": symbol, "algoId": o["algoId"]} for o in orders]
        self.post("/api/v5/trade/cancel-algos", cancel)
        return len(orders)

    def close_position(self, symbol, pos_side):
        return self.post("/api/v5/trade/close-position", {
            "instId":  symbol,
            "mgnMode": "cross",
            "posSide": pos_side,
        })


def calc_size(equity, price, cfg):
    margin   = equity * MARGIN_PCT
    notional = margin * LEVERAGE
    size     = notional / (price * cfg["ct_val"])
    size     = max(cfg["min_sz"], round(size / cfg["lot_sz"]) * cfg["lot_sz"])
    return round(size, 4)


def test_coin(client: OKXClient, symbol: str, cfg: dict, equity: float):
    sep = "─" * 52
    print(f"\n{sep}")
    print(f"  🪙 {symbol}")
    print(sep)

    # 1. Giá hiện tại
    print("  1. Lấy giá...")
    price = client.get_ticker(symbol)
    size  = calc_size(equity, price, cfg)
    margin_used = equity * MARGIN_PCT
    notional    = margin_used * LEVERAGE
    print(f"     Giá:      ${price:,.4f}")
    print(f"     Margin:   ${margin_used:.2f} ({MARGIN_PCT*100:.0f}% vốn)")
    print(f"     Notional: ${notional:.2f} (x{LEVERAGE} leverage)")
    print(f"     Size:     {size} contracts = {size * cfg['ct_val']:.4f} units")

    # 2. Set leverage
    print("  2. Set leverage x3...")
    for ps in ("long", "short"):
        r = client.post("/api/v5/account/set-leverage", {
            "instId": symbol, "lever": str(LEVERAGE),
            "mgnMode": "cross", "posSide": ps,
        })
        ok = "✅" if r.get("code") == "0" else f"❌ {r.get('msg')}"
        print(f"     {ps}: {ok}")

    # 3. Đặt lệnh SHORT market
    print("  3. Đặt lệnh SHORT market...")
    r = client.post("/api/v5/trade/order", {
        "instId":  symbol,
        "tdMode":  "cross",
        "side":    "sell",
        "posSide": "short",
        "ordType": "market",
        "sz":      str(size),
    })
    if r.get("code") != "0" or r["data"][0]["sCode"] != "0":
        print(f"     ❌ THẤT BẠI: {r}")
        return False
    ord_id = r["data"][0]["ordId"]
    print(f"     ✅ Thành công! ordId: {ord_id}")

    # 4. Lấy entry thật
    print("  4. Lấy entry price thật...")
    time.sleep(2)
    positions = client.get_positions(symbol)
    if not positions:
        print("     ⚠️  Không tìm thấy vị thế!")
        return False
    entry = float(positions[0]["avgPx"])
    print(f"     Entry thật: ${entry:,.4f}")

    # 5. Đặt SL/TP (giả lập ATR ~1%)
    sl = round(entry * 1.015, 4)  # SL +1.5% cho short
    tp = round(entry * 0.960, 4)  # TP -4.0% cho short
    print(f"  5. Đặt SL @ {sl} | TP @ {tp}...")

    for is_sl in [True, False]:
        trigger_px = sl if is_sl else tp
        tkey = "slTriggerPx" if is_sl else "tpTriggerPx"
        pkey = "slOrdPx"     if is_sl else "tpOrdPx"
        ttype = "slTriggerPxType" if is_sl else "tpTriggerPxType"
        label = "SL" if is_sl else "TP"
        r = client.post("/api/v5/trade/order-algo", {
            "instId":  symbol, "tdMode": "cross",
            "side":    "buy",  "posSide": "short",
            "ordType": "conditional", "sz": str(size),
            tkey: str(trigger_px), pkey: "-1", ttype: "last",
        })
        if r.get("code") == "0" and r["data"][0]["sCode"] == "0":
            print(f"     ✅ {label} @ {trigger_px}")
        else:
            print(f"     ❌ {label} thất bại: {r}")

    # 6. Kiểm tra vị thế
    print("  6. Kiểm tra vị thế...")
    positions = client.get_positions(symbol)
    if positions:
        pos = positions[0]
        print(f"     ✅ Vị thế: {pos['posSide']} | Size: {pos['pos']} | UnPnL: {pos['upl']} USDT")
    else:
        print("     ⚠️  Không có vị thế!")

    # 7. Cleanup
    print("  7. Cleanup (đóng vị thế test)...")
    time.sleep(3)
    n = client.cancel_algo_orders(symbol)
    print(f"     Huỷ {n} algo orders")
    r = client.close_position(symbol, "short")
    if r.get("code") == "0":
        print(f"     ✅ Đóng vị thế thành công!")
    else:
        print(f"     ❌ Đóng thất bại: {r}")

    return True


def main():
    mode = "🔵 SIMULATED" if SIMULATED == "1" else "🔴 LIVE"
    print("=" * 52)
    print(f"  🧪 TEST ĐẶT LỆNH MULTI-COIN")
    print(f"  Mode: {mode}")
    print(f"  Margin/trade: {MARGIN_PCT*100:.0f}% | Leverage: x{LEVERAGE}")
    print("=" * 52)

    client = OKXClient()

    print("\n💰 Lấy equity...")
    equity = client.get_equity()
    print(f"   Equity: ${equity:.2f} USDT")

    results = {}
    for symbol, cfg in COINS.items():
        try:
            ok = test_coin(client, symbol, cfg, equity)
            results[symbol] = "✅ OK" if ok else "❌ FAIL"
        except Exception as e:
            print(f"\n  ❌ Lỗi {symbol}: {e}")
            results[symbol] = f"❌ ERROR: {e}"

    print(f"\n{'═'*52}")
    print("  📋 KẾT QUẢ")
    print(f"{'═'*52}")
    for sym, res in results.items():
        print(f"  {sym}: {res}")
    print(f"{'═'*52}")


if __name__ == "__main__":
    main()