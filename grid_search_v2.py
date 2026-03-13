"""
So sánh hiệu quả các khung giờ trade
Tham số cố định: EMA 15/26/80 | ATR 14 | SL 2.5x | TP 4.0x
Chạy: python backtest_hours.py
"""

import pandas as pd
import numpy as np

try:
    import yfinance as yf
except ImportError:
    print("Chưa cài yfinance. Chạy: pip install yfinance pandas numpy")
    exit()

# ── Tham số cố định (tốt nhất từ grid search v2) ─────────────
SYMBOL        = "GC=F"
INTERVAL      = "1h"
PERIOD        = "2y"
EMA_FAST      = 15
EMA_SLOW      = 26
EMA_TREND     = 80
ATR_PERIOD    = 14
ATR_SL_MULT   = 2.5
ATR_TP_MULT   = 4.0
INITIAL_CAP   = 10_000
RISK_PER_TRADE = 0.01
MAX_DD_STOP   = 0.30
TRAIN_MONTHS  = 18
TEST_MONTHS   = 6

# ── Các khung giờ cần so sánh ────────────────────────────────
HOUR_CONFIGS = {
    "6h–17h UTC (hiện tại)":  list(range(6, 18)),
    "0h–24h UTC (24/7)":      list(range(0, 24)),
    "0h–17h UTC (mở rộng sáng)": list(range(0, 18)),
    "6h–23h UTC (mở rộng tối)":  list(range(6, 24)),
    "2h–17h UTC (thêm Asia)": list(range(2, 18)),
}


# ══════════════════════════════════════════════════════════════

def fetch_data():
    print(f"📥 Đang tải {SYMBOL} ({INTERVAL}, {PERIOD})...")
    df = yf.download(SYMBOL, period=PERIOD, interval=INTERVAL,
                     auto_adjust=True, progress=False)
    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                  for c in df.columns]
    df.index = pd.to_datetime(df.index, utc=True)
    df.dropna(inplace=True)
    print(f"✅ {len(df)} nến | {df.index[0].date()} → {df.index[-1].date()}\n")
    return df


def split(df):
    cutoff = df.index[-1] - pd.DateOffset(months=TEST_MONTHS)
    return df[df.index < cutoff].copy(), df[df.index >= cutoff].copy()


def add_indicators(df, trade_hours):
    d = df.copy()
    d["ema_fast"]  = d["close"].ewm(span=EMA_FAST,  adjust=False).mean()
    d["ema_slow"]  = d["close"].ewm(span=EMA_SLOW,  adjust=False).mean()
    d["ema_trend"] = d["close"].ewm(span=EMA_TREND, adjust=False).mean()

    hl  = d["high"] - d["low"]
    hpc = (d["high"] - d["close"].shift(1)).abs()
    lpc = (d["low"]  - d["close"].shift(1)).abs()
    d["atr"] = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)\
                 .ewm(span=ATR_PERIOD, adjust=False).mean()

    d["cross_up"]   = (d["ema_fast"] > d["ema_slow"]) & \
                      (d["ema_fast"].shift(1) <= d["ema_slow"].shift(1))
    d["cross_down"] = (d["ema_fast"] < d["ema_slow"]) & \
                      (d["ema_fast"].shift(1) >= d["ema_slow"].shift(1))
    d["uptrend"]    = d["close"] > d["ema_trend"]
    d["downtrend"]  = d["close"] < d["ema_trend"]
    d["valid_hour"] = d.index.hour.isin(trade_hours)
    return d.dropna()


def backtest(df):
    capital  = INITIAL_CAP
    peak     = INITIAL_CAP
    trades   = []
    equity   = [capital]
    position = None

    for i in range(1, len(df)):
        row = df.iloc[i]
        if (peak - capital) / peak >= MAX_DD_STOP:
            equity.append(capital)
            continue

        if position:
            hit_sl = (position["side"] == "long"  and row["low"]  <= position["sl"]) or \
                     (position["side"] == "short" and row["high"] >= position["sl"])
            hit_tp = (position["side"] == "long"  and row["high"] >= position["tp"]) or \
                     (position["side"] == "short" and row["low"]  <= position["tp"])

            if hit_sl or hit_tp:
                cp     = position["sl"] if hit_sl else position["tp"]
                mult   = 1 if position["side"] == "long" else -1
                pnl    = mult * (cp - position["entry"]) * position["size"]
                capital += pnl
                peak    = max(peak, capital)
                trades.append({"pnl": pnl, "result": "SL" if hit_sl else "TP",
                               "side": position["side"]})
                position = None

        if not position and row["valid_hour"]:
            signal = None
            if row["cross_up"]   and row["uptrend"]:   signal = "long"
            elif row["cross_down"] and row["downtrend"]: signal = "short"

            if signal:
                entry   = row["close"]
                atr     = row["atr"]
                sl      = entry - ATR_SL_MULT * atr if signal == "long" \
                          else entry + ATR_SL_MULT * atr
                tp      = entry + ATR_TP_MULT * atr if signal == "long" \
                          else entry - ATR_TP_MULT * atr
                sl_dist = abs(entry - sl)
                if sl_dist > 0:
                    size = (capital * RISK_PER_TRADE) / sl_dist
                    position = {"side": signal, "entry": entry,
                                "sl": sl, "tp": tp, "size": size}

        equity.append(capital)

    return trades, pd.Series(equity)


def metrics(trades, equity, n_months):
    if len(trades) < 5:
        return None
    df_t  = pd.DataFrame(trades)
    wins  = df_t[df_t["pnl"] > 0]
    loss  = df_t[df_t["pnl"] <= 0]
    pf_d  = abs(loss["pnl"].sum())
    pf    = wins["pnl"].sum() / pf_d if pf_d > 0 else 0
    roll  = equity.cummax()
    max_dd = ((equity - roll) / roll).min()
    total_ret  = (equity.iloc[-1] - INITIAL_CAP) / INITIAL_CAP * 100
    avg_monthly = total_ret / n_months
    return {
        "trades":      len(df_t),
        "per_month":   round(len(df_t) / n_months, 1),
        "winrate":     round(len(wins) / len(df_t) * 100, 1),
        "pf":          round(pf, 3),
        "max_dd":      round(max_dd * 100, 1),
        "avg_monthly": round(avg_monthly, 2),
        "total_ret":   round(total_ret, 2),
    }


def run():
    print("=" * 65)
    print("  ⏰ SO SÁNH KHUNG GIỜ TRADE – EMA 15/26/80 | SL 2.5x | TP 4.0x")
    print("=" * 65)

    df = fetch_data()
    train_df, test_df = split(df)
    train_months = TRAIN_MONTHS
    test_months  = TEST_MONTHS

    print(f"  Train: {train_df.index[0].date()} → {train_df.index[-1].date()} ({train_months} tháng)")
    print(f"  Test : {test_df.index[0].date()} → {test_df.index[-1].date()} ({test_months} tháng)\n")

    results = []
    for name, hours in HOUR_CONFIGS.items():
        tr_ind = add_indicators(train_df, hours)
        te_ind = add_indicators(test_df,  hours)

        tr_trades, tr_eq = backtest(tr_ind)
        te_trades, te_eq = backtest(te_ind)

        tr_m = metrics(tr_trades, tr_eq, train_months)
        te_m = metrics(te_trades, te_eq, test_months)

        results.append((name, tr_m, te_m))

    # ── In bảng so sánh ──────────────────────────────────────
    sep = "─" * 65
    print(f"\n{'─'*65}")
    print(f"  {'Khung giờ':<30} {'Lệnh/th':>7} {'WR':>6} {'PF':>6} {'DD':>7} {'Monthly':>8}")
    print(f"{'─'*65}")

    for name, tr, te in results:
        if not tr:
            print(f"  {name:<30} {'–':>7} {'–':>6} {'–':>6} {'–':>7} {'–':>8}")
            continue

        # Train
        dd_icon = "✅" if abs(tr['max_dd']) < 10 else "⚠️ " if abs(tr['max_dd']) < 20 else "❌"
        pf_icon = "✅" if tr['pf'] >= 1.8 else "🟡" if tr['pf'] >= 1.3 else "❌"
        print(f"\n  📊 TRAIN — {name}")
        print(f"  {'':30} {tr['per_month']:>7} {tr['winrate']:>5}% {tr['pf']:>6} {tr['max_dd']:>6}% {tr['avg_monthly']:>7}%  {pf_icon}{dd_icon}")

        # Test
        if te:
            pf_icon_t = "✅" if te['pf'] >= 1.8 else "🟡" if te['pf'] >= 1.3 else "❌"
            dd_icon_t = "✅" if abs(te['max_dd']) < 10 else "⚠️ " if abs(te['max_dd']) < 20 else "❌"
            print(f"  📈 TEST  — {name}")
            print(f"  {'':30} {te['per_month']:>7} {te['winrate']:>5}% {te['pf']:>6} {te['max_dd']:>6}% {te['avg_monthly']:>7}%  {pf_icon_t}{dd_icon_t}")
        else:
            print(f"  📈 TEST  — không đủ lệnh để đánh giá")

    # ── Bảng tổng hợp dễ đọc ─────────────────────────────────
    print(f"\n\n{'═'*65}")
    print(f"  🏆 BẢNG TỔNG HỢP (sắp xếp theo Test Profit Factor)")
    print(f"{'═'*65}")
    print(f"  {'Khung giờ':<32} {'Train PF':>9} {'Test PF':>9} {'Test DD':>8} {'Test Monthly':>13}")
    print(f"{'─'*65}")

    sorted_results = sorted(results, key=lambda x: x[2]["pf"] if x[2] else 0, reverse=True)
    for name, tr, te in sorted_results:
        if not tr:
            continue
        te_pf  = f"{te['pf']:.3f}" if te else "–"
        te_dd  = f"{te['max_dd']:.1f}%" if te else "–"
        te_mon = f"{te['avg_monthly']:.2f}%" if te else "–"
        star   = " ⭐" if te and te['pf'] >= 2.0 else ""
        print(f"  {name:<32} {tr['pf']:>9.3f} {te_pf:>9} {te_dd:>8} {te_mon:>13}{star}")

    print(f"\n{'═'*65}")
    print("  📌 Tham số hiện tại bot: 6h–17h UTC")
    print(f"{'═'*65}\n")


if __name__ == "__main__":
    run()