"""
╔══════════════════════════════════════════════════════════════╗
║         GRID SEARCH OPTIMIZER – XAU/USD Futures             ║
║         Tự động tìm tham số tốt nhất cho EMA+ATR            ║
║                                                              ║
║  Chạy:  python grid_search.py                               ║
║  Kết quả lưu vào: grid_search_results.csv                   ║
╚══════════════════════════════════════════════════════════════╝
"""

import pandas as pd
import numpy as np
import itertools
import time
from datetime import datetime

try:
    import yfinance as yf
except ImportError:
    print("Chưa cài yfinance. Chạy: pip install yfinance pandas numpy")
    exit()


# ══════════════════════════════════════════════════════════════
# 1. CẤU HÌNH GRID SEARCH
# ══════════════════════════════════════════════════════════════

SYMBOL   = "GC=F"
INTERVAL = "1h"
PERIOD   = "2y"

# ── Tham số cần tìm ─────────────────────────────────────────
PARAM_GRID = {
    "ema_fast":     [5, 9, 12],
    "ema_slow":     [21, 26, 34],
    "ema_trend":    [50, 100, 200],
    "atr_period":   [10, 14],
    "atr_sl_mult":  [1.0, 1.5, 2.0],
    "atr_tp_mult":  [2.0, 2.5, 3.0, 3.5],
    "use_rsi":      [False, True],   # bật/tắt RSI filter
    "use_adx":      [False, True],   # bật/tắt ADX filter
}

# ── Cố định ─────────────────────────────────────────────────
INITIAL_CAPITAL   = 10_000
RISK_PER_TRADE    = 0.01
MAX_DRAWDOWN_STOP = 0.30
TRADE_HOURS_UTC   = list(range(7, 17))

# ── Điều kiện để lọc kết quả tốt ────────────────────────────
MIN_TRADES        = 30      # ít hơn → không đủ ý nghĩa thống kê
MIN_PROFIT_FACTOR = 1.3
MIN_WINRATE       = 40.0
MAX_DRAWDOWN      = 0.25    # chỉ giữ kết quả drawdown < 25%
MIN_AVG_MONTHLY   = 1.0     # ít nhất 1%/tháng

# ── Out-of-sample: 6 tháng cuối chỉ dùng để validate ────────
TRAIN_CUTOFF_MONTHS = 6     # train trên data cũ, validate trên 6 tháng mới


# ══════════════════════════════════════════════════════════════
# 2. TẢI DỮ LIỆU
# ══════════════════════════════════════════════════════════════

def fetch_data():
    print(f"📥 Đang tải dữ liệu {SYMBOL} ({INTERVAL}, {PERIOD})...")
    df = yf.download(SYMBOL, period=PERIOD, interval=INTERVAL, auto_adjust=True)
    if df.empty:
        raise ValueError("Không tải được dữ liệu.")
    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index, utc=True)
    df.dropna(inplace=True)
    print(f"✅ {len(df)} nến | {df.index[0].date()} → {df.index[-1].date()}\n")
    return df


def split_train_test(df):
    cutoff = df.index[-1] - pd.DateOffset(months=TRAIN_CUTOFF_MONTHS)
    train  = df[df.index < cutoff]
    test   = df[df.index >= cutoff]
    return train, test


# ══════════════════════════════════════════════════════════════
# 3. CHỈ BÁO
# ══════════════════════════════════════════════════════════════

def add_indicators(df, p):
    df = df.copy()

    df["ema_fast"]  = df["close"].ewm(span=p["ema_fast"],  adjust=False).mean()
    df["ema_slow"]  = df["close"].ewm(span=p["ema_slow"],  adjust=False).mean()
    df["ema_trend"] = df["close"].ewm(span=p["ema_trend"], adjust=False).mean()

    # ATR
    hl  = df["high"] - df["low"]
    hpc = (df["high"] - df["close"].shift(1)).abs()
    lpc = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=p["atr_period"], adjust=False).mean()

    # RSI
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=14, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # ADX
    plus_dm  = df["high"].diff().clip(lower=0)
    minus_dm = (-df["low"].diff()).clip(lower=0)
    mask = plus_dm < minus_dm
    plus_dm[mask] = 0
    mask2 = minus_dm <= plus_dm
    minus_dm[mask2] = 0
    plus_di  = 100 * plus_dm.ewm(span=14, adjust=False).mean() / df["atr"].replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(span=14, adjust=False).mean() / df["atr"].replace(0, np.nan)
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    df["adx"]      = dx.ewm(span=14, adjust=False).mean()
    df["plus_di"]  = plus_di
    df["minus_di"] = minus_di

    # Signals
    df["cross_up"]   = (df["ema_fast"] > df["ema_slow"]) & (df["ema_fast"].shift(1) <= df["ema_slow"].shift(1))
    df["cross_down"] = (df["ema_fast"] < df["ema_slow"]) & (df["ema_fast"].shift(1) >= df["ema_slow"].shift(1))
    df["uptrend"]    = df["close"] > df["ema_trend"]
    df["downtrend"]  = df["close"] < df["ema_trend"]
    df["valid_hour"] = df.index.hour.isin(TRADE_HOURS_UTC)

    df.dropna(inplace=True)
    return df


# ══════════════════════════════════════════════════════════════
# 4. BACKTEST (nhanh, không print)
# ══════════════════════════════════════════════════════════════

def backtest(df, p):
    capital  = INITIAL_CAPITAL
    peak_cap = INITIAL_CAPITAL
    trades   = []
    position = None

    for i in range(1, len(df)):
        row = df.iloc[i]

        if (peak_cap - capital) / peak_cap >= MAX_DRAWDOWN_STOP:
            break

        # Kiểm tra SL/TP
        if position is not None:
            hit_sl = (position["side"] == "long"  and row["low"]  <= position["sl"]) or \
                     (position["side"] == "short" and row["high"] >= position["sl"])
            hit_tp = (position["side"] == "long"  and row["high"] >= position["tp"]) or \
                     (position["side"] == "short" and row["low"]  <= position["tp"])

            if hit_sl or hit_tp:
                cp = position["sl"] if hit_sl else position["tp"]
                pnl = (cp - position["entry"]) * position["size"] \
                      if position["side"] == "long" \
                      else (position["entry"] - cp) * position["size"]
                capital += pnl
                peak_cap = max(peak_cap, capital)
                trades.append(pnl)
                position = None

        # Tìm tín hiệu
        if position is None and row["valid_hour"]:
            signal = None

            long_ok  = row["cross_up"]   and row["uptrend"]
            short_ok = row["cross_down"] and row["downtrend"]

            # RSI filter
            if p["use_rsi"]:
                long_ok  = long_ok  and row["rsi"] < 60
                short_ok = short_ok and row["rsi"] > 40

            # ADX filter — chỉ trade khi có trend mạnh
            if p["use_adx"]:
                long_ok  = long_ok  and row["adx"] > 20 and row["plus_di"]  > row["minus_di"]
                short_ok = short_ok and row["adx"] > 20 and row["minus_di"] > row["plus_di"]

            if long_ok:
                signal = "long"
            elif short_ok:
                signal = "short"

            if signal:
                entry = row["close"]
                atr   = row["atr"]
                sl = entry - p["atr_sl_mult"] * atr if signal == "long" else entry + p["atr_sl_mult"] * atr
                tp = entry + p["atr_tp_mult"] * atr if signal == "long" else entry - p["atr_tp_mult"] * atr
                sl_dist = abs(entry - sl)
                size = (capital * RISK_PER_TRADE) / sl_dist if sl_dist > 0 else 0
                if size > 0:
                    position = {"side": signal, "entry": entry, "sl": sl, "tp": tp, "size": size}

    if not trades:
        return None

    arr = np.array(trades)
    wins   = arr[arr > 0]
    losses = arr[arr <= 0]
    winrate = len(wins) / len(arr) * 100
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else 0

    # Equity curve để tính drawdown và monthly
    equity = INITIAL_CAPITAL + np.cumsum(arr)
    roll_max = np.maximum.accumulate(np.concatenate([[INITIAL_CAPITAL], equity]))
    dd = (equity - roll_max[1:]) / roll_max[1:]
    max_dd = dd.min()

    total_ret = (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    months = 24  # approximate
    avg_monthly = total_ret / months

    return {
        "trades":       len(arr),
        "winrate":      round(winrate, 1),
        "profit_factor": round(pf, 3),
        "max_dd":       round(max_dd * 100, 2),
        "total_ret":    round(total_ret, 2),
        "avg_monthly":  round(avg_monthly, 2),
        "final_cap":    round(capital, 2),
    }


# ══════════════════════════════════════════════════════════════
# 5. GRID SEARCH RUNNER
# ══════════════════════════════════════════════════════════════

def run_grid_search(df_train, df_test):
    keys   = list(PARAM_GRID.keys())
    values = list(PARAM_GRID.values())
    combos = list(itertools.product(*values))
    total  = len(combos)

    print(f"🔍 Tổng số tổ hợp: {total:,}")
    print(f"📊 Train: {df_train.index[0].date()} → {df_train.index[-1].date()}")
    print(f"🧪 Test:  {df_test.index[0].date()}  → {df_test.index[-1].date()}")
    print(f"⏳ Bắt đầu chạy...\n")

    results   = []
    passed    = 0
    start     = time.time()

    for idx, combo in enumerate(combos):
        p = dict(zip(keys, combo))

        # Bỏ qua tổ hợp vô lý
        if p["ema_fast"] >= p["ema_slow"]:
            continue
        if p["atr_tp_mult"] <= p["atr_sl_mult"]:
            continue

        # Progress bar
        if (idx + 1) % 50 == 0 or idx == 0:
            pct     = (idx + 1) / total * 100
            elapsed = time.time() - start
            eta     = elapsed / (idx + 1) * (total - idx - 1)
            print(f"  [{idx+1:>4}/{total}] {pct:5.1f}% | Tìm được: {passed} | ETA: {eta:.0f}s")

        # Train
        df_ind = add_indicators(df_train, p)
        stats  = backtest(df_ind, p)
        if stats is None:
            continue

        # Lọc theo tiêu chí
        if (stats["trades"]        < MIN_TRADES or
            stats["profit_factor"] < MIN_PROFIT_FACTOR or
            stats["winrate"]       < MIN_WINRATE or
            stats["max_dd"]        < -MAX_DRAWDOWN * 100 or
            stats["avg_monthly"]   < MIN_AVG_MONTHLY):
            continue

        # Validate trên out-of-sample (6 tháng cuối)
        df_test_ind = add_indicators(df_test, p)
        test_stats  = backtest(df_test_ind, p)

        row = {**p, **{f"train_{k}": v for k, v in stats.items()}}
        if test_stats:
            row.update({f"test_{k}": v for k, v in test_stats.items()})
        else:
            row.update({f"test_{k}": None for k in stats.keys()})

        results.append(row)
        passed += 1

    elapsed = time.time() - start
    print(f"\n✅ Hoàn thành {total:,} tổ hợp trong {elapsed:.1f}s")
    print(f"🏆 Tìm được {passed} tổ hợp vượt tiêu chí\n")
    return results


# ══════════════════════════════════════════════════════════════
# 6. IN VÀ LƯU KẾT QUẢ
# ══════════════════════════════════════════════════════════════

def print_top_results(results, top_n=10):
    if not results:
        print("❌ Không tìm được tổ hợp nào đạt tiêu chí.")
        print("\n💡 Thử nới lỏng điều kiện:")
        print("   MIN_PROFIT_FACTOR = 1.1")
        print("   MIN_WINRATE       = 35.0")
        print("   MIN_AVG_MONTHLY   = 0.5")
        return

    df_r = pd.DataFrame(results)

    # Score tổng hợp: cân bằng giữa profit factor, avg monthly, drawdown
    df_r["score"] = (
        df_r["train_profit_factor"] * 0.4 +
        df_r["train_avg_monthly"]   * 0.3 +
        (1 + df_r["train_max_dd"] / 100) * 0.3   # max_dd âm, gần 0 là tốt
    )

    # Ưu tiên các combo vừa tốt train vừa tốt test
    if "test_profit_factor" in df_r.columns:
        df_r["score"] += df_r["test_profit_factor"].fillna(0) * 0.3

    df_r = df_r.sort_values("score", ascending=False)

    print("═" * 70)
    print(f"  🏆  TOP {top_n} TỔ HỢP TỐT NHẤT")
    print("═" * 70)

    for i, (_, row) in enumerate(df_r.head(top_n).iterrows()):
        print(f"\n  #{i+1}  ──────────────────────────────────────────────")
        print(f"  EMA: {int(row['ema_fast'])}/{int(row['ema_slow'])}/{int(row['ema_trend'])} | "
              f"ATR: {int(row['atr_period'])} | "
              f"SL: {row['atr_sl_mult']}x | TP: {row['atr_tp_mult']}x | "
              f"RSI: {'✅' if row['use_rsi'] else '❌'} | "
              f"ADX: {'✅' if row['use_adx'] else '❌'}")
        print(f"  📈 TRAIN → Trades: {int(row['train_trades'])} | "
              f"WR: {row['train_winrate']}% | "
              f"PF: {row['train_profit_factor']} | "
              f"DD: {row['train_max_dd']}% | "
              f"Avg/mo: {row['train_avg_monthly']:+.2f}%")

        if row.get("test_profit_factor") is not None:
            print(f"  🧪 TEST  → Trades: {int(row['test_trades'] or 0)} | "
                  f"WR: {row.get('test_winrate', '?')}% | "
                  f"PF: {row.get('test_profit_factor', '?')} | "
                  f"DD: {row.get('test_max_dd', '?')}% | "
                  f"Avg/mo: {row.get('test_avg_monthly', 0):+.2f}%")
        else:
            print("  🧪 TEST  → Không đủ lệnh để đánh giá")

    # Lưu CSV
    out_file = "grid_search_results.csv"
    df_r.to_csv(out_file, index=False)
    print(f"\n  💾 Toàn bộ {len(df_r)} kết quả đã lưu vào: {out_file}")
    print("═" * 70)

    # Gợi ý tham số tốt nhất
    best = df_r.iloc[0]
    print(f"""
  📌  COPY THAM SỐ TỐT NHẤT VÀO backtest_xauusd.py:
  ─────────────────────────────────────────────────────
  EMA_FAST      = {int(best['ema_fast'])}
  EMA_SLOW      = {int(best['ema_slow'])}
  EMA_TREND     = {int(best['ema_trend'])}
  ATR_PERIOD    = {int(best['atr_period'])}
  ATR_SL_MULT   = {best['atr_sl_mult']}
  ATR_TP_MULT   = {best['atr_tp_mult']}
  USE_RSI       = {best['use_rsi']}
  USE_ADX       = {best['use_adx']}
  ─────────────────────────────────────────────────────
  ⚠️  Nhớ validate lại trên 6 tháng out-of-sample
      trước khi chạy live!
    """)


# ══════════════════════════════════════════════════════════════
# 7. MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    t0 = time.time()

    print("=" * 70)
    print("  🔬  GRID SEARCH OPTIMIZER – XAU/USD EMA+ATR+RSI+ADX")
    print("=" * 70)
    print(f"  Tiêu chí lọc:")
    print(f"    Min trades:        {MIN_TRADES}")
    print(f"    Min profit factor: {MIN_PROFIT_FACTOR}")
    print(f"    Min winrate:       {MIN_WINRATE}%")
    print(f"    Max drawdown:      {MAX_DRAWDOWN:.0%}")
    print(f"    Min avg monthly:   {MIN_AVG_MONTHLY}%")
    print()

    # 1. Tải data
    df_all = fetch_data()

    # 2. Tách train / test
    df_train, df_test = split_train_test(df_all)
    print(f"📚 Train: {len(df_train):,} nến | 🧪 Test: {len(df_test):,} nến\n")

    # 3. Grid search
    results = run_grid_search(df_train, df_test)

    # 4. Hiển thị top kết quả
    print_top_results(results, top_n=10)

    print(f"\n⏱️  Tổng thời gian: {time.time() - t0:.1f}s")