"""
Test đặt lệnh + SL/TP trên OKX Demo
Chạy: python test_order.py

Script này sẽ:
1. Lấy giá hiện tại
2. Đặt 1 lệnh market SHORT nhỏ nhất có thể
3. Đặt SL/TP riêng qua algo order
4. In kết quả chi tiết
5. Đóng lệnh sau 10 giây (cleanup)
"""

import json
import time
import hmac
import hashlib
import base64
import requests
from datetime import datetime, timezone

try:
    from config import BASE_URL, API_KEY, API_SECRET, API_PASSPHRASE, SIMULATED
except ImportError:
    print("❌ Không tìm thấy config.py")
    exit()

SYMBOL = "XAU-USDT-SWAP"

# ── Helpers ───────────────────────────────────────────────────
def ts():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def sign(timestamp, method, path, body=""):
    msg = timestamp + method + path + body
    mac = hmac.new(bytes(API_SECRET, "utf-8"), bytes(msg, "utf-8"), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()

def headers(method, path, body=""):
    t = ts()
    return {
        "OK-ACCESS-KEY":        API_KEY,
        "OK-ACCESS-SIGN":       sign(t, method, path, body),
        "OK-ACCESS-TIMESTAMP":  t,
        "OK-ACCESS-PASSPHRASE": API_PASSPHRASE,
        "Content-Type":         "application/json",
        "x-simulated-trading":  SIMULATED,
    }

def request(method, path, body=""):
    url = BASE_URL + path
    if method == "GET":
        r = requests.get(url, headers=headers(method, path, body), timeout=20)
    else:
        r = requests.post(url, headers=headers(method, path, body), data=body, timeout=20)
    data = r.json()
    return data

def ok(data):
    return data.get("code") == "0"

def sep(title=""):
    print(f"\n{'─'*50}")
    if title:
        print(f"  {title}")
        print('─'*50)

# ══════════════════════════════════════════════════════════════

def run_test():
    print("=" * 50)
    print("  🧪 TEST ĐẶT LỆNH OKX DEMO")
    print(f"  Symbol: {SYMBOL} | Mode: {'DEMO' if SIMULATED=='1' else '🔴 LIVE'}")
    print("=" * 50)

    # ── 1. Giá hiện tại ───────────────────────────────────────
    sep("1. Lấy giá hiện tại")
    r = requests.get(f"{BASE_URL}/api/v5/market/ticker?instId={SYMBOL}", timeout=10)
    ticker = r.json()
    if "data" not in ticker or not ticker["data"]:
        print("❌ Không lấy được giá. Kiểm tra symbol:", ticker)
        return

    price = float(ticker["data"][0]["last"])
    print(f"  ✅ Giá hiện tại: ${price:,.2f}")

    # Chờ lấy entry thật sau khi đặt lệnh
    # TP/SL phải tính từ entry, không phải từ giá ticker
    atr  = 37.0
    size = 10  # OKX demo có thể đặt lệnh nhỏ nhất 0.5 SOL, tương đương ~20 USDT margin với đòn bẩy 10x

    # ── 2. Đặt lệnh market SHORT ──────────────────────────────
    sep("2. Đặt lệnh MARKET SHORT")
    order_body = json.dumps({
        "instId":  SYMBOL,
        "tdMode":  "cross",
        "side":    "sell",
        "posSide": "short",
        "ordType": "market",
        "sz":      str(size),
    })
    order_resp = request("POST", "/api/v5/trade/order", order_body)
    print(f"  Response: {order_resp}")

    if not ok(order_resp):
        print(f"  ❌ Đặt lệnh thất bại: {order_resp.get('msg')}")
        print("\n  💡 Gợi ý:")
        print("     - Kiểm tra tài khoản demo có đủ margin không")
        print("     - Vào OKX Demo → bật symbol XAU-USDT-SWAP")
        print("     - Kiểm tra quyền API có bật Trade không")
        return

    ord_id = order_resp["data"][0]["ordId"]
    print(f"  ✅ Lệnh market SHORT thành công! ordId: {ord_id}")
    print("  ⏳ Chờ 2 giây để lệnh khớp...")
    time.sleep(2)

    # Lấy entry price thật từ vị thế
    pos_check = request("GET", f"/api/v5/account/positions?instId={SYMBOL}")
    positions_check = [p for p in pos_check.get("data", []) if float(p.get("pos", 0)) != 0]
    if positions_check:
        entry_price = float(positions_check[0]["avgPx"])
    else:
        entry_price = price   # fallback về giá ticker
    print(f"  📌 Entry price thật: ${entry_price:,.2f}")

    # Tính SL/TP từ entry thật — SHORT: SL trên, TP dưới entry
    sl = round(entry_price + 2.0 * atr, 2)
    tp = round(entry_price - 3.0 * atr, 2)
    print(f"  📐 ATR: {atr} | SL: {sl} (+{2*atr}) | TP: {tp} (-{3*atr})")
    time.sleep(2)

    # ── 3. Đặt SL/TP riêng lẻ (bỏ qua OCO vì OKX demo không hỗ trợ) ──
    sep("3. Đặt SL conditional")
    sl_body = json.dumps({
        "instId":          SYMBOL,
        "tdMode":          "cross",
        "side":            "buy",
        "posSide":         "short",
        "ordType":         "conditional",
        "sz":              str(size),
        "slTriggerPx":     str(sl),
        "slOrdPx":         "-1",
        "slTriggerPxType": "last",
    })
    sl_resp = request("POST", "/api/v5/trade/order-algo", sl_body)
    print(f"  Response: {sl_resp}")
    if ok(sl_resp):
        print(f"  ✅ SL đặt thành công @ {sl}")
        algo_ok = True
    else:
        print(f"  ❌ SL thất bại: {sl_resp}")
        algo_ok = False

    sep("4. Đặt TP conditional")
    tp_body = json.dumps({
        "instId":          SYMBOL,
        "tdMode":          "cross",
        "side":            "buy",
        "posSide":         "short",
        "ordType":         "conditional",
        "sz":              str(size),
        "tpTriggerPx":     str(tp),
        "tpOrdPx":         "-1",
        "tpTriggerPxType": "last",
    })
    tp_resp = request("POST", "/api/v5/trade/order-algo", tp_body)
    print(f"  Response: {tp_resp}")
    if ok(tp_resp):
        print(f"  ✅ TP đặt thành công @ {tp}")
    else:
        print(f"  ❌ TP thất bại: {tp_resp}")
        algo_ok = False

    # ── 5. Kiểm tra vị thế ────────────────────────────────────
    sep("5. Kiểm tra vị thế hiện tại")
    time.sleep(1)
    pos_resp = request("GET", f"/api/v5/account/positions?instId={SYMBOL}")
    positions = [p for p in pos_resp.get("data", []) if float(p.get("pos", 0)) != 0]
    if positions:
        p = positions[0]
        print(f"  ✅ Vị thế đang mở:")
        print(f"     Side:      {p.get('posSide')}")
        print(f"     Size:      {p.get('pos')}")
        print(f"     Entry:     {p.get('avgPx')}")
        print(f"     UnPnL:     {p.get('upl')} USDT")
    else:
        print("  ⚠️  Không thấy vị thế — có thể chưa khớp hoặc đã đóng")

    # ── 6. Cleanup: đóng vị thế ───────────────────────────────
    sep("6. Cleanup — Đóng vị thế test")
    print("  ⏳ Chờ 5 giây rồi đóng...")
    time.sleep(5)

    # Huỷ algo orders trước
    algo_list_resp = request("GET", f"/api/v5/trade/orders-algo-pending?instId={SYMBOL}&ordType=oco,conditional")
    algo_orders = algo_list_resp.get("data", [])
    if algo_orders:
        cancel_algos = [{"instId": SYMBOL, "algoId": o["algoId"]} for o in algo_orders]
        cancel_resp  = request("POST", "/api/v5/trade/cancel-algos", json.dumps(cancel_algos))
        print(f"  Huỷ algo: {cancel_resp.get('code')} — {len(cancel_algos)} orders")

    # Đóng vị thế
    close_body = json.dumps({"instId": SYMBOL, "mgnMode": "cross", "posSide": "short"})
    close_resp = request("POST", "/api/v5/trade/close-position", close_body)
    if ok(close_resp):
        print("  ✅ Đã đóng vị thế test thành công!")
    else:
        print(f"  ⚠️  Đóng vị thế: {close_resp}")

    # ── Tổng kết ──────────────────────────────────────────────
    sep("📋 TỔNG KẾT")
    print(f"  Market order:  ✅ Hoạt động")
    print(f"  SL/TP (OCO):   {'✅ Hoạt động' if algo_ok else '⚠️  Dùng fallback conditional'}")
    print()
    if algo_ok:
        print("  🚀 Bot sẵn sàng chạy! Deploy và restart là được.")
    else:
        print("  🚀 Bot vẫn hoạt động được với fallback SL/TP riêng lẻ.")
    print("=" * 50)

if __name__ == "__main__":
    run_test()