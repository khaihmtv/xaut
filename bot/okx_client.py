import requests
import time
import hmac
import hashlib
import base64
from config import *
from datetime import datetime, timezone

class OKXClient:

    def __init__(self):
        self.base_url = BASE_URL

    def _request(self, method, path, body=""):
        url = self.base_url + path

        headers = self._headers(method, path, body)

        if method == "GET":
            response = requests.get(url, headers=headers)
        elif method == "POST":
            response = requests.post(url, headers=headers, data=body)
        else:
            raise ValueError("Unsupported method")

        return response.json()
    
    def place_limit_order(self, side, price, size):
        """
        Đặt lệnh LIMIT futures
        side: "buy" hoặc "sell"
        price: giá limit
        size: số contract
        """

        path = "/api/v5/trade/order"

        body = {
            "instId": SYMBOL,
            "tdMode": "cross",     # dùng cross margin
            "side": side,
            "ordType": "limit",
            "px": str(price),
            "sz": str(size)
        }

        import json
        body_str = json.dumps(body)

        response = self._request("POST", path, body_str)

        print("Limit order response:", response)

        return response
    
    def cancel_all_orders(self):
        """
        Huỷ tất cả lệnh đang mở của SYMBOL
        """
        path = "/api/v5/trade/cancel-batch-orders"

        # Lấy danh sách lệnh đang mở trước
        orders = self.get_open_orders()

        if not orders:
            print("No open orders to cancel.")
            return

        cancel_list = [{"instId": SYMBOL, "ordId": o["ordId"]} for o in orders]

        import json
        body_str = json.dumps(cancel_list)

        headers = self._headers("POST", path, body_str)
        r = requests.post(self.base_url + path, headers=headers, data=body_str)

        print("Cancel response:", r.json())
        return r.json()
    
    def get_open_orders(self):
        path = f"/api/v5/trade/orders-pending?instId={SYMBOL}"
        response = self._request("GET", path)

        if "data" not in response:
            print("Get open orders error:", response)
            return []

        return response["data"]

    def close_all_positions(self):
        path = "/api/v5/trade/close-position"

        body = {
            "instId": SYMBOL,
            "mgnMode": "cross"
        }

        import json
        body_str = json.dumps(body)

        response = self._request("POST", path, body_str)

        print("Close position response:", response)

        return response


    def _headers(self, method, path, body=""):
        

        timestamp = datetime.now(timezone.utc) \
            .isoformat(timespec='milliseconds') \
            .replace('+00:00', 'Z')
        message = timestamp + method + path + body
        mac = hmac.new(
            bytes(API_SECRET, encoding="utf8"),
            bytes(message, encoding="utf-8"),
            hashlib.sha256
        )
        sign = base64.b64encode(mac.digest()).decode()

        return {
            "OK-ACCESS-KEY": API_KEY,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": API_PASSPHRASE,
            "Content-Type": "application/json",
            "x-simulated-trading": "1"   # 👈 thêm dòng này
        }

    def get_candles(self):
        path = f"/api/v5/market/candles?instId={SYMBOL}&bar={TIMEFRAME}&limit=300"
        r = requests.get(self.base_url + path)
        data = r.json()

        if "data" not in data:
            print("Candle error:", data)
            return []
        
        return data["data"]

    def place_order(self, side, size):
        path = "/api/v5/trade/order"
        body = {
            "instId": SYMBOL,
            "tdMode": "cross",
            "side": side,
            "ordType": "market",
            "sz": str(size)
        }

        import json
        body_str = json.dumps(body)

        headers = self._headers("POST", path, body_str)
        r = requests.post(self.base_url + path, headers=headers, data=body_str)
        return r.json()
    
    def get_funding_rate(self):
        path = f"/api/v5/public/funding-rate?instId={SYMBOL}"
        r = requests.get(self.base_url + path)
        data = r.json()

        if "data" in data and len(data["data"]) > 0:
            return float(data["data"][0]["fundingRate"])

        return 0

    def get_equity(self):
        path = "/api/v5/account/balance"
        response = self._request("GET", path)

        if "data" not in response:
            print("Balance error:", response)
            return 0

        total_equity = 0

        for acc in response["data"]:
            for detail in acc.get("details", []):
                if detail.get("ccy") == "USDT":
                    total_equity += float(detail.get("eq", 0))

        return total_equity
