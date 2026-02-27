"""
Tìm symbol vàng đúng trên OKX Demo
Chạy: python find_symbol.py
"""
import requests

BASE_URL = "https://www.okx.com"

def find_xau_symbols():
    print("🔍 Đang tìm symbol vàng trên OKX...\n")

    # Lấy tất cả SWAP instruments
    r = requests.get(f"{BASE_URL}/api/v5/public/instruments?instType=SWAP", timeout=10)
    data = r.json()

    if "data" not in data:
        print("❌ Không lấy được danh sách symbol:", data)
        return

    # Lọc các symbol có chứa "XAU" hoặc "GOLD"
    xau_symbols = [
        inst["instId"] for inst in data["data"]
        if "XAU" in inst["instId"] or "GOLD" in inst["instId"].upper()
    ]

    if xau_symbols:
        print("✅ Tìm thấy các symbol vàng:")
        for s in xau_symbols:
            print(f"   → {s}")
        print()
        print(f"📌 Copy symbol đúng vào bot_xauusd.py dòng:")
        print(f'   SYMBOL = "{xau_symbols[0]}"')
    else:
        print("❌ Không tìm thấy symbol vàng nào.")
        print("   OKX Demo có thể không hỗ trợ XAU.")
        print("   Thử dùng tài khoản live (vẫn để SIMULATED = '1')")

if __name__ == "__main__":
    find_xau_symbols()