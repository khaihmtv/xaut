from config import *
from indicators import calculate_ema, calculate_atr
import pandas as pd

class GridEngine:

    def __init__(self, client):
        self.client = client

    def analyze(self, candles):
        df = pd.DataFrame(candles).iloc[::-1]
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

        trend = "buy" if price > ema else "sell"

        grid_spacing = atr * 0.5
        grid_size = (TOTAL_CAPITAL * BOT_RATIO) / GRID_LEVELS

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
                "size": round(size, 4)
            })

        return orders