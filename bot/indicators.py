# bot/indicators.py

import pandas as pd


def calculate_ema(df: pd.DataFrame, period: int = 21) -> pd.Series:
    """
    Calculate Exponential Moving Average
    """
    return df["close"].ewm(span=period, adjust=False).mean()


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculate Average True Range
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    return tr.rolling(period).mean()