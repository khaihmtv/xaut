import requests
import time
import hmac
import hashlib
import base64
from config import *

class OKXClient:

    def __init__(self):
        self.base_url = BASE_URL

    def _headers(self, method, path, body=""):
        timestamp = str(time.time())
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
            "Content-Type": "application/json"
        }

    def get_candles(self):
        path = f"/api/v5/market/candles?instId={SYMBOL}&bar={TIMEFRAME}&limit=300"
        r = requests.get(self.base_url + path)
        return r.json()["data"]

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