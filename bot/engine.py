from config import *
from indicators import calculate_ema, calculate_atr
import pandas as pd

class GridEngine:

    def __init__(self, client):
        self.client = client
    def analyze(self, candles):

        if not candles:
            print("No candle data")
            return None, None, None, None

        df = pd.DataFrame(candles)

        if df.empty:
            print("DataFrame empty")
            return None, None, None, None

        if df.shape[1] < 9:
            print("Unexpected candle format:", df.head())
            return None, None, None, None

        df = df.iloc[::-1]

        df.columns = ["ts","open","high","low","close","vol",
                    "volCcy","volCcyQuote","confirm"]

        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)

        df["ema"] = calculate_ema(df, EMA_PERIOD)
        df["atr"] = calculate_atr(df, ATR_PERIOD)

        price = df["close"].iloc[-1]
        ema = df["ema"].iloc[-1]
        atr = df["atr"].iloc[-1]

        if pd.isna(ema) or pd.isna(atr):
            print("Indicator not ready")
            return None, None, None, None

        trend = "buy" if price > ema else "sell"

        grid_spacing = atr * 0.5
        usdt_per_grid = (TOTAL_CAPITAL * BOT_RATIO) / GRID_LEVELS

        contract_value = price * 0.001   # 1 contract = 0.001 XAU
        grid_size = usdt_per_grid / contract_value

        return trend, price, grid_spacing, grid_size
    
    def build_grid(self, trend, price, spacing, size):
        orders = []

        for i in range(1, GRID_LEVELS + 1):
            if trend == "buy":
                level_price = price - spacing * i
                side = "buy"
            else:
                level_price = price + spacing * i
                side = "sell"

            orders.append({
                "side": side,
                "price": round(level_price, 2),
                "size": max(1, int(size))
            })

        return orders