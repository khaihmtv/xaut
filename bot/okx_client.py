import requests
import hmac
import hashlib
import base64
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from config import BASE_URL, API_KEY, API_SECRET, API_PASSPHRASE, SYMBOL, TIMEFRAME, SIMULATED

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class OKXError(Exception):
    """Exception cho các lỗi trả về từ OKX API."""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"OKX Error {code}: {message}")


class OKXClient:
    """
    Client giao tiếp với OKX API v5.
    Hỗ trợ: đặt lệnh, huỷ lệnh, đóng vị thế, lấy nến, số dư, funding rate.
    Mặc định dùng hedge mode (posSide bắt buộc).
    """

    REQUEST_TIMEOUT = 10  # giây

    def __init__(self):
        self.base_url = BASE_URL
        self.simulated = SIMULATED  # "1" = demo, "0" = live — lấy từ config

    # ─────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _timestamp(self) -> str:
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        message = timestamp + method + path + body
        mac = hmac.new(
            bytes(API_SECRET, encoding="utf-8"),
            bytes(message, encoding="utf-8"),
            hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode()

    def _headers(self, method: str, path: str, body: str = "") -> dict:
        ts = self._timestamp()
        return {
            "OK-ACCESS-KEY": API_KEY,
            "OK-ACCESS-SIGN": self._sign(ts, method, path, body),
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": API_PASSPHRASE,
            "Content-Type": "application/json",
            "x-simulated-trading": self.simulated,
        }

    def _request(self, method: str, path: str, body: str = "") -> dict:
        """
        Gửi HTTP request đến OKX, xử lý lỗi mạng và lỗi API.
        Raise OKXError nếu OKX trả về code != "0".
        """
        url = self.base_url + path
        headers = self._headers(method, path, body)

        try:
            if method == "GET":
                response = requests.get(url, headers=headers, timeout=self.REQUEST_TIMEOUT)
            elif method == "POST":
                response = requests.post(url, headers=headers, data=body, timeout=self.REQUEST_TIMEOUT)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()  # raise nếu 4xx / 5xx
            data = response.json()

        except requests.Timeout:
            logger.error("Request timeout: %s %s", method, path)
            raise
        except requests.ConnectionError:
            logger.error("Connection error: %s %s", method, path)
            raise
        except requests.HTTPError as e:
            logger.error("HTTP error %s: %s %s", e.response.status_code, method, path)
            raise

        # OKX trả HTTP 200 nhưng có thể báo lỗi trong body
        if data.get("code") != "0":
            code = data.get("code", "?")
            msg = data.get("msg", "Unknown error")
            logger.error("OKX API error [%s]: %s | %s %s", code, msg, method, path)
            raise OKXError(code, msg)

        return data

    def _pos_side(self, side: str) -> str:
        """Trả về posSide tương ứng với side khi dùng hedge mode."""
        return "long" if side == "buy" else "short"

    # ─────────────────────────────────────────────────────────────────────────
    # Trading
    # ─────────────────────────────────────────────────────────────────────────

    def place_market_order(self, side: str, size: float) -> dict:
        """
        Đặt lệnh thị trường (market order).
        side: "buy" | "sell"
        size: số lượng contract
        """
        path = "/api/v5/trade/order"
        body = json.dumps({
            "instId": SYMBOL,
            "tdMode": "cross",
            "side": side,
            "posSide": self._pos_side(side),
            "ordType": "market",
            "sz": str(size),
        })
        logger.info("Placing market order: side=%s size=%s", side, size)
        response = self._request("POST", path, body)
        logger.info("Market order response: %s", response)
        return response

    def place_limit_order(self, side: str, price: float, size: float) -> dict:
        """
        Đặt lệnh giới hạn (limit order).
        side: "buy" | "sell"
        """
        path = "/api/v5/trade/order"
        body = json.dumps({
            "instId": SYMBOL,
            "tdMode": "cross",
            "side": side,
            "posSide": self._pos_side(side),
            "ordType": "limit",
            "px": str(price),
            "sz": str(size),
        })
        logger.info("Placing limit order: side=%s price=%s size=%s", side, price, size)
        response = self._request("POST", path, body)
        logger.info("Limit order response: %s", response)
        return response

    def cancel_all_orders(self) -> Optional[dict]:
        """Huỷ tất cả lệnh đang mở của SYMBOL."""
        orders = self.get_open_orders()

        if not orders:
            logger.info("No open orders to cancel.")
            return None

        path = "/api/v5/trade/cancel-batch-orders"
        cancel_list = [{"instId": SYMBOL, "ordId": o["ordId"]} for o in orders]
        body = json.dumps(cancel_list)

        logger.info("Cancelling %d order(s)...", len(cancel_list))
        response = self._request("POST", path, body)
        logger.info("Cancel response: %s", response)
        return response

    def close_all_positions(self) -> list[dict]:
        """
        Đóng cả 2 chiều (long & short) của SYMBOL trong hedge mode.
        Trả về danh sách response cho mỗi chiều.
        """
        path = "/api/v5/trade/close-position"
        results = []

        for pos_side in ("long", "short"):
            body = json.dumps({
                "instId": SYMBOL,
                "mgnMode": "cross",
                "posSide": pos_side,
            })
            logger.info("Closing %s position...", pos_side)
            try:
                response = self._request("POST", path, body)
                logger.info("Close %s response: %s", pos_side, response)
                results.append(response)
            except OKXError as e:
                # Bỏ qua nếu không có vị thế chiều đó
                logger.warning("Could not close %s: %s", pos_side, e)

        return results

    # ─────────────────────────────────────────────────────────────────────────
    # Market data
    # ─────────────────────────────────────────────────────────────────────────

    def get_open_orders(self) -> list[dict]:
        """Lấy danh sách lệnh đang mở của SYMBOL."""
        path = f"/api/v5/trade/orders-pending?instId={SYMBOL}"
        response = self._request("GET", path)
        return response.get("data", [])

    def get_candles(self) -> list:
        """
        Lấy dữ liệu nến (không cần auth — public endpoint).
        Trả về list nến, mỗi nến là [ts, open, high, low, close, vol, ...].
        """
        path = f"/api/v5/market/candles?instId={SYMBOL}&bar={TIMEFRAME}&limit=300"
        # Public endpoint — không cần ký, nhưng vẫn dùng _request để có timeout & error handling
        url = self.base_url + path
        try:
            response = requests.get(url, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as e:
            logger.error("Failed to fetch candles: %s", e)
            return []

        if "data" not in data:
            logger.error("Candle error: %s", data)
            return []

        return data["data"]

    def get_funding_rate(self) -> float:
        """Lấy funding rate hiện tại của SYMBOL."""
        path = f"/api/v5/public/funding-rate?instId={SYMBOL}"
        url = self.base_url + path
        try:
            response = requests.get(url, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as e:
            logger.error("Failed to fetch funding rate: %s", e)
            return 0.0

        if data.get("data"):
            return float(data["data"][0].get("fundingRate", 0))

        return 0.0

    def get_equity(self) -> float:
        """Lấy tổng equity USDT trong tài khoản."""
        path = "/api/v5/account/balance"
        response = self._request("GET", path)

        total_equity = 0.0
        for acc in response.get("data", []):
            for detail in acc.get("details", []):
                if detail.get("ccy") == "USDT":
                    total_equity += float(detail.get("eq", 0))

        logger.info("Total USDT equity: %.4f", total_equity)
        return total_equity