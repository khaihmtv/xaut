"""
╔══════════════════════════════════════════════════════════════╗
║         BACKTEST ENGINE – XAU/USD Futures                    ║
║         Chiến lược: EMA Crossover + ATR Trailing Stop        ║
║                                                              ║
║  Cài đặt:  pip install yfinance pandas numpy                 ║
║  Chạy:     python backtest_xauusd.py                         ║
╚══════════════════════════════════════════════════════════════╝
"""

import pandas as pd
import numpy as np

# ─── Cài nếu chưa có: pip install yfinance ───────────────────
try:
    import yfinance as yf
except ImportError:
    print("Chưa cài yfinance. Chạy: pip install yfinance pandas numpy")
    exit()


# ══════════════════════════════════════════════════════════════
# 1. THAM SỐ – CHỈNH TẠI ĐÂY
# ══════════════════════════════════════════════════════════════

SYMBOL        = "GC=F"       # XAU/USD Futures trên Yahoo Finance
INTERVAL      = "15m"        # ← Khung 15 phút
PERIOD        = "60d"        # ← yfinance giới hạn 60 ngày cho 15m

# EMA — dùng tham số tốt nhất từ grid search 1H (#2)
EMA_FAST      = 9
EMA_SLOW      = 34
EMA_TREND     = 100

# ATR
ATR_PERIOD    = 14
ATR_SL_MULT   = 2.0
ATR_TP_MULT   = 3.0

# News filter
NEWS_BLACKOUT_HOURS = []

# Quản lý vốn
INITIAL_CAPITAL   = 10_000
RISK_PER_TRADE    = 0.01
MAX_DRAWDOWN_STOP = 0.30

# Chỉ trade trong giờ thanh khoản cao (UTC)
TRADE_HOURS_UTC = list(range(7, 17))


# ══════════════════════════════════════════════════════════════
# 2. LẤY DỮ LIỆU
# ══════════════════════════════════════════════════════════════

def fetch_data(symbol: str, period: str, interval: str) -> pd.DataFrame:
    print(f"📥 Đang tải dữ liệu {symbol} ({interval}, {period})...")
    df = yf.download(symbol, period=period, interval=interval, auto_adjust=True)

    if df.empty:
        raise ValueError(f"Không lấy được dữ liệu cho {symbol}. Kiểm tra kết nối.")

    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index, utc=True)
    df.dropna(inplace=True)

    print(f"✅ Lấy được {len(df)} nến từ {df.index[0].date()} đến {df.index[-1].date()}")
    return df


# ══════════════════════════════════════════════════════════════
# 3. CHỈ BÁO KỸ THUẬT
# ══════════════════════════════════════════════════════════════

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # EMA
    df["ema_fast"]  = df["close"].ewm(span=EMA_FAST,  adjust=False).mean()
    df["ema_slow"]  = df["close"].ewm(span=EMA_SLOW,  adjust=False).mean()
    df["ema_trend"] = df["close"].ewm(span=EMA_TREND, adjust=False).mean()

    # ATR (True Range)
    high_low   = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close  = (df["low"]  - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=ATR_PERIOD, adjust=False).mean()

    # Signal: crossover
    df["cross_up"]   = (df["ema_fast"] > df["ema_slow"]) & (df["ema_fast"].shift(1) <= df["ema_slow"].shift(1))
    df["cross_down"] = (df["ema_fast"] < df["ema_slow"]) & (df["ema_fast"].shift(1) >= df["ema_slow"].shift(1))

    # Trend filter
    df["uptrend"]   = df["close"] > df["ema_trend"]
    df["downtrend"] = df["close"] < df["ema_trend"]

    # Trade hour filter
    df["valid_hour"] = df.index.hour.isin(TRADE_HOURS_UTC)

    df.dropna(inplace=True)
    return df


# ══════════════════════════════════════════════════════════════
# 4. BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════

def run_backtest(df: pd.DataFrame) -> tuple[list, pd.Series]:
    capital    = INITIAL_CAPITAL
    peak_cap   = INITIAL_CAPITAL
    trades     = []
    equity_curve = [capital]

    position   = None   # None | dict
    stopped_by_drawdown = False

    for i in range(1, len(df)):
        row  = df.iloc[i]
        prev = df.iloc[i - 1]

        # ── Kiểm tra drawdown tổng ──────────────────────────
        drawdown = (peak_cap - capital) / peak_cap
        if drawdown >= MAX_DRAWDOWN_STOP:
            if not stopped_by_drawdown:
                print(f"\n🛑 Drawdown {drawdown:.1%} vượt ngưỡng {MAX_DRAWDOWN_STOP:.0%}. Bot dừng tại {row.name.date()}.")
                stopped_by_drawdown = True
            equity_curve.append(capital)
            continue

        # ── Đang giữ lệnh → kiểm tra SL/TP ─────────────────
        if position is not None:
            hit_sl = (position["side"] == "long"  and row["low"]  <= position["sl"]) or \
                     (position["side"] == "short" and row["high"] >= position["sl"])
            hit_tp = (position["side"] == "long"  and row["high"] >= position["tp"]) or \
                     (position["side"] == "short" and row["low"]  <= position["tp"])

            close_price = None
            close_reason = None

            if hit_sl:
                close_price  = position["sl"]
                close_reason = "SL"
            elif hit_tp:
                close_price  = position["tp"]
                close_reason = "TP"

            if close_price is not None:
                if position["side"] == "long":
                    pnl = (close_price - position["entry"]) * position["size"]
                else:
                    pnl = (position["entry"] - close_price) * position["size"]

                capital += pnl
                peak_cap = max(peak_cap, capital)

                trades.append({
                    "open_time":  position["open_time"],
                    "close_time": row.name,
                    "side":       position["side"],
                    "entry":      position["entry"],
                    "close":      close_price,
                    "sl":         position["sl"],
                    "tp":         position["tp"],
                    "size":       position["size"],
                    "pnl":        round(pnl, 2),
                    "result":     close_reason,
                    "capital":    round(capital, 2),
                })
                position = None

        # ── Tìm tín hiệu vào lệnh mới ───────────────────────
        if position is None and row["valid_hour"]:
            signal = None

            if row["cross_up"] and row["uptrend"]:
                signal = "long"
            elif row["cross_down"] and row["downtrend"]:
                signal = "short"

            if signal is not None:
                entry = row["close"]
                atr   = row["atr"]

                if signal == "long":
                    sl = entry - ATR_SL_MULT * atr
                    tp = entry + ATR_TP_MULT * atr
                else:
                    sl = entry + ATR_SL_MULT * atr
                    tp = entry - ATR_TP_MULT * atr

                risk_amount = capital * RISK_PER_TRADE
                sl_distance = abs(entry - sl)
                size = risk_amount / sl_distance if sl_distance > 0 else 0

                if size > 0:
                    position = {
                        "side":      signal,
                        "entry":     entry,
                        "sl":        sl,
                        "tp":        tp,
                        "size":      size,
                        "open_time": row.name,
                    }

        equity_curve.append(capital)

    # Đóng lệnh cuối nếu còn
    if position is not None:
        last = df.iloc[-1]
        close_price = last["close"]
        if position["side"] == "long":
            pnl = (close_price - position["entry"]) * position["size"]
        else:
            pnl = (position["entry"] - close_price) * position["size"]
        capital += pnl
        trades.append({
            "open_time":  position["open_time"],
            "close_time": last.name,
            "side":       position["side"],
            "entry":      position["entry"],
            "close":      close_price,
            "sl":         position["sl"],
            "tp":         position["tp"],
            "size":       position["size"],
            "pnl":        round(pnl, 2),
            "result":     "OPEN→CLOSED",
            "capital":    round(capital, 2),
        })
        equity_curve.append(capital)

    equity_series = pd.Series(equity_curve, index=df.index[:len(equity_curve)])
    return trades, equity_series


# ══════════════════════════════════════════════════════════════
# 5. THỐNG KÊ KẾT QUẢ
# ══════════════════════════════════════════════════════════════

def analyze_results(trades: list, equity: pd.Series) -> None:
    if not trades:
        print("❌ Không có lệnh nào được thực hiện.")
        return

    df_t = pd.DataFrame(trades)

    total_trades = len(df_t)
    wins         = df_t[df_t["pnl"] > 0]
    losses       = df_t[df_t["pnl"] <= 0]
    winrate      = len(wins) / total_trades * 100
    total_pnl    = df_t["pnl"].sum()
    avg_win      = wins["pnl"].mean()   if len(wins)   > 0 else 0
    avg_loss     = losses["pnl"].mean() if len(losses) > 0 else 0
    profit_factor = wins["pnl"].sum() / abs(losses["pnl"].sum()) if losses["pnl"].sum() != 0 else float("inf")

    # Drawdown
    roll_max  = equity.cummax()
    drawdown  = (equity - roll_max) / roll_max
    max_dd    = drawdown.min()

    # Monthly return
    eq_monthly   = equity.resample("ME").last()
    monthly_ret  = eq_monthly.pct_change().dropna() * 100
    avg_monthly  = monthly_ret.mean()
    best_month   = monthly_ret.max()
    worst_month  = monthly_ret.min()

    # Sharpe (đơn giản, không risk-free rate)
    daily_ret = equity.resample("D").last().pct_change().dropna()
    sharpe    = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252) if daily_ret.std() > 0 else 0

    final_cap  = equity.iloc[-1]
    total_ret  = (final_cap - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

    # ── In kết quả ──────────────────────────────────────────
    sep = "═" * 55
    print(f"\n{sep}")
    print(f"  📊  KẾT QUẢ BACKTEST – XAU/USD ({INTERVAL})")
    print(sep)
    print(f"  Vốn ban đầu       : ${INITIAL_CAPITAL:>10,.2f}")
    print(f"  Vốn cuối          : ${final_cap:>10,.2f}")
    print(f"  Tổng lợi nhuận    : {total_ret:>+10.2f}%")
    print(f"  Số lệnh           : {total_trades:>10}")
    print(f"  Winrate           : {winrate:>10.1f}%")
    print(f"  Avg win / Avg loss: ${avg_win:>8,.2f} / ${avg_loss:>8,.2f}")
    print(f"  Profit factor     : {profit_factor:>10.2f}  (>1.5 là tốt)")
    print(f"  Sharpe ratio      : {sharpe:>10.2f}  (>1.0 là chấp nhận được)")
    print(f"  Max drawdown      : {max_dd:>10.1%}")
    print(sep)
    print(f"  📅  MONTHLY RETURNS")
    print(sep)
    print(f"  Avg monthly return: {avg_monthly:>+10.2f}%")
    print(f"  Best month        : {best_month:>+10.2f}%")
    print(f"  Worst month       : {worst_month:>+10.2f}%")
    print(sep)

    # Cảnh báo
    print("\n  ⚠️  ĐÁNH GIÁ:")
    if profit_factor < 1.0:
        print("  ❌ Profit factor < 1 → chiến lược THUA trong dài hạn.")
        print("     Cần điều chỉnh tham số hoặc đổi chiến lược.")
    elif profit_factor < 1.5:
        print("  🟡 Profit factor 1.0–1.5 → chiến lược có edge nhỏ.")
        print("     Cần tối ưu thêm hoặc thêm filter.")
    else:
        print("  ✅ Profit factor > 1.5 → chiến lược có edge.")

    if abs(max_dd) > 0.30:
        print(f"  ❌ Max drawdown {max_dd:.1%} vượt ngưỡng 30% chịu đựng của bạn.")
    else:
        print(f"  ✅ Max drawdown {max_dd:.1%} trong ngưỡng 30%.")

    if avg_monthly >= 3:
        print(f"  ✅ Avg monthly {avg_monthly:.2f}% đạt mục tiêu 3–5%.")
    else:
        print(f"  🟡 Avg monthly {avg_monthly:.2f}% chưa đạt mục tiêu 3%.")

    # Monthly breakdown
    print(f"\n  📆  CHI TIẾT TỪNG THÁNG:")
    for period, ret in monthly_ret.items():
        bar = "█" * int(abs(ret) / 1) if abs(ret) < 50 else "█" * 20
        sign = "+" if ret >= 0 else ""
        color = "✅" if ret >= 0 else "❌"
        print(f"  {color} {period.strftime('%Y-%m')}  {sign}{ret:6.2f}%  {bar}")

    print(f"\n{sep}\n")

    # Lưu file trades
    df_t.to_csv("trades_result.csv", index=False)
    print("  💾 Chi tiết lệnh đã lưu vào: trades_result.csv")
    print(sep)


# ══════════════════════════════════════════════════════════════
# 6. HƯỚNG DẪN TỐI ƯU THAM SỐ
# ══════════════════════════════════════════════════════════════

def print_optimization_guide():
    print("""
  📌  HƯỚNG DẪN TỐI ƯU SAU KHI CHẠY:
  ─────────────────────────────────────────────────────
  Nếu winrate thấp (<45%):
    → Thêm filter: RSI < 30 để mua, RSI > 70 để bán
    → Tăng EMA_TREND lên 100 để lọc trend mạnh hơn

  Nếu profit factor thấp (<1.3):
    → Tăng ATR_TP_MULT lên 3.0–3.5 (bắt trend xa hơn)
    → Giảm ATR_SL_MULT xuống 1.2 (cắt lỗ nhanh hơn)

  Nếu max drawdown quá lớn:
    → Giảm RISK_PER_TRADE xuống 0.005 (0.5%)
    → Thêm MAX_OPEN_TRADES = 1 (không giữ 2 lệnh cùng lúc)

  Nếu avg monthly chưa đạt 3%:
    → Thử INTERVAL = "4h" (ít lệnh hơn, chất lượng hơn)
    → Mở rộng TRADE_HOURS_UTC = list(range(6, 20))
  ─────────────────────────────────────────────────────
  ⚠️  CẢNH BÁO OVERFITTING:
  Tối ưu quá nhiều trên dữ liệu cũ sẽ không hoạt động
  trong tương lai. Luôn giữ lại 6 tháng cuối để test
  riêng (out-of-sample) — KHÔNG dùng để tối ưu.
    """)


# ══════════════════════════════════════════════════════════════
# 7. MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 55)
    print("  🥇 BACKTEST XAU/USD – EMA Crossover + ATR Stop")
    print("=" * 55)
    print(f"  Tham số: EMA {EMA_FAST}/{EMA_SLOW}/{EMA_TREND} | "
          f"ATR {ATR_PERIOD} | SL {ATR_SL_MULT}x | TP {ATR_TP_MULT}x")
    print(f"  Risk/trade: {RISK_PER_TRADE:.0%} | "
          f"Vốn: ${INITIAL_CAPITAL:,} | Max DD: {MAX_DRAWDOWN_STOP:.0%}")
    print()

    # 1. Tải dữ liệu
    df = fetch_data(SYMBOL, PERIOD, INTERVAL)

    # 2. Thêm chỉ báo
    df = add_indicators(df)

    # 3. Chạy backtest
    print("⚙️  Đang chạy backtest...")
    trades, equity = run_backtest(df)
    print(f"✅ Hoàn thành. Tổng {len(trades)} lệnh.")

    # 4. Phân tích kết quả
    analyze_results(trades, equity)

    # 5. Hướng dẫn tối ưu
    print_optimization_guide()