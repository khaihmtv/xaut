"""
╔══════════════════════════════════════════════════════════════╗
║      GRID SEARCH v2 – XAU/USD Tối ưu tham số               ║
║      Mở rộng từ kết quả tốt nhất (EMA 9/34/100)            ║
║                                                              ║
║  Chạy:  python grid_search_v2.py                            ║
║  Thời gian ước tính: 10–20 phút                             ║
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
# 1. LƯỚI TÌM KIẾM – mở rộng quanh tham số tốt nhất
# ══════════════════════════════════════════════════════════════

PARAM_GRID = {
    # EMA — thử các biến thể quanh 9/34/100
    "ema_fast":     [5, 9, 12, 15],
    "ema_slow":     [26, 34, 42, 50],
    "ema_trend":    [80, 100, 150, 200],

    # ATR
    "atr_period":   [10, 14, 20],
    "atr_sl_mult":  [1.5, 2.0, 2.5, 3.0],
    "atr_tp_mult":  [2.5, 3.0, 3.5, 4.0],

    # Trade hours — thử mở rộng window giao dịch
    "trade_hours":  [
        list(range(7, 17)),    # London + NY overlap (cũ)
        list(range(6, 18)),    # Mở rộng hơn
        list(range(0, 24)),    # 24/7
    ],
}

# ── Cấu hình cố định ──────────────────────────────────────────
SYMBOL          = "GC=F"
INTERVAL        = "1h"
PERIOD          = "2y"
INITIAL_CAPITAL = 10_000
RISK_PER_TRADE  = 0.01
MAX_DRAWDOWN    = 0.30

# ── Train/Test split ──────────────────────────────────────────
TRAIN_MONTHS    = 18   # 18 tháng train
TEST_MONTHS     = 6    # 6 tháng test (out-of-sample)

# ── Ngưỡng lọc kết quả ───────────────────────────────────────
MIN_TRADES      = 20       # tối thiểu 20 lệnh để có ý nghĩa thống kê
MIN_PF          = 1.3      # profit factor tối thiểu
MIN_WINRATE     = 40.0     # winrate tối thiểu
MAX_DD          = 0.25     # max drawdown tối đa
MIN_MONTHLY     = 0.5      # avg monthly tối thiểu


# ══════════════════════════════════════════════════════════════
# 2. LẤY DỮ LIỆU
# ══════════════════════════════════════════════════════════════

def fetch_data():
    print(f"📥 Đang tải dữ liệu {SYMBOL} ({INTERVAL}, {PERIOD})...")
    df = yf.download(SYMBOL, period=PERIOD, interval=INTERVAL,
                     auto_adjust=True, progress=False)
    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                  for c in df.columns]
    df.index = pd.to_datetime(df.index, utc=True)
    df.dropna(inplace=True)
    print(f"✅ {len(df)} nến | {df.index[0].date()} → {df.index[-1].date()}")
    return df


def split_data(df):
    cutoff = df.index[-1] - pd.DateOffset(months=TEST_MONTHS)
    train  = df[df.index < cutoff].copy()
    test   = df[df.index >= cutoff].copy()
    return train, test


# ══════════════════════════════════════════════════════════════
# 3. CHỈ BÁO KỸ THUẬT
# ══════════════════════════════════════════════════════════════

def add_indicators(df, ema_fast, ema_slow, ema_trend, atr_period, trade_hours):
    d = df.copy()
    d["ema_fast"]  = d["close"].ewm(span=ema_fast,  adjust=False).mean()
    d["ema_slow"]  = d["close"].ewm(span=ema_slow,  adjust=False).mean()
    d["ema_trend"] = d["close"].ewm(span=ema_trend, adjust=False).mean()

    hl  = d["high"] - d["low"]
    hpc = (d["high"] - d["close"].shift(1)).abs()
    lpc = (d["low"]  - d["close"].shift(1)).abs()
    tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    d["atr"] = tr.ewm(span=atr_period, adjust=False).mean()

    d["cross_up"]   = (d["ema_fast"] > d["ema_slow"]) & \
                      (d["ema_fast"].shift(1) <= d["ema_slow"].shift(1))
    d["cross_down"] = (d["ema_fast"] < d["ema_slow"]) & \
                      (d["ema_fast"].shift(1) >= d["ema_slow"].shift(1))
    d["uptrend"]    = d["close"] > d["ema_trend"]
    d["downtrend"]  = d["close"] < d["ema_trend"]
    d["valid_hour"] = d.index.hour.isin(trade_hours)

    return d.dropna()


# ══════════════════════════════════════════════════════════════
# 4. BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════

def backtest(df, atr_sl_mult, atr_tp_mult):
    capital  = INITIAL_CAPITAL
    peak_cap = INITIAL_CAPITAL
    trades   = []
    equity   = [capital]
    position = None

    for i in range(1, len(df)):
        row = df.iloc[i]

        if (peak_cap - capital) / peak_cap >= MAX_DRAWDOWN:
            equity.append(capital)
            continue

        # Kiểm tra SL/TP
        if position is not None:
            hit_sl = (position["side"] == "long"  and row["low"]  <= position["sl"]) or \
                     (position["side"] == "short" and row["high"] >= position["sl"])
            hit_tp = (position["side"] == "long"  and row["high"] >= position["tp"]) or \
                     (position["side"] == "short" and row["low"]  <= position["tp"])

            close_price = close_reason = None
            if hit_sl:
                close_price, close_reason = position["sl"], "SL"
            elif hit_tp:
                close_price, close_reason = position["tp"], "TP"

            if close_price:
                mult = 1 if position["side"] == "long" else -1
                pnl  = mult * (close_price - position["entry"]) * position["size"]
                capital  += pnl
                peak_cap  = max(peak_cap, capital)
                trades.append({"pnl": pnl, "result": close_reason,
                               "close_time": row.name})
                position = None

        # Tìm tín hiệu mới
        if position is None and row["valid_hour"]:
            signal = None
            if row["cross_up"]   and row["uptrend"]:   signal = "long"
            elif row["cross_down"] and row["downtrend"]: signal = "short"

            if signal:
                entry = row["close"]
                atr   = row["atr"]
                sl    = entry - atr_sl_mult * atr if signal == "long" \
                        else entry + atr_sl_mult * atr
                tp    = entry + atr_tp_mult * atr if signal == "long" \
                        else entry - atr_tp_mult * atr
                sl_dist = abs(entry - sl)
                if sl_dist > 0:
                    size = (capital * RISK_PER_TRADE) / sl_dist
                    position = {"side": signal, "entry": entry,
                                "sl": sl, "tp": tp, "size": size}

        equity.append(capital)

    return trades, pd.Series(equity)


# ══════════════════════════════════════════════════════════════
# 5. TÍNH METRICS
# ══════════════════════════════════════════════════════════════

def calc_metrics(trades, equity_series):
    if len(trades) < MIN_TRADES:
        return None

    df_t  = pd.DataFrame(trades)
    wins  = df_t[df_t["pnl"] > 0]
    loss  = df_t[df_t["pnl"] <= 0]

    total    = len(df_t)
    winrate  = len(wins) / total * 100
    pf_denom = abs(loss["pnl"].sum())
    pf       = wins["pnl"].sum() / pf_denom if pf_denom > 0 else 0

    roll_max = equity_series.cummax()
    max_dd   = ((equity_series - roll_max) / roll_max).min()

    # Tính đơn giản: 720 nến 1h ≈ 1 tháng
    final       = equity_series.iloc[-1]
    total_ret   = (final - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    n_months    = max(1, len(equity_series) / 720)
    avg_monthly = total_ret / n_months

    daily_ret = equity_series.pct_change().dropna()
    sharpe    = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252 * 24) \
                if daily_ret.std() > 0 else 0

    return {
        "trades":      total,
        "winrate":     round(winrate, 1),
        "pf":          round(pf, 3),
        "max_dd":      round(max_dd, 4),
        "avg_monthly": round(avg_monthly, 2),
        "total_ret":   round(total_ret, 2),
        "sharpe":      round(sharpe, 2),
    }


def passes_filter(m):
    if m is None: return False
    return (m["trades"]      >= MIN_TRADES  and
            m["pf"]          >= MIN_PF       and
            m["winrate"]     >= MIN_WINRATE  and
            m["max_dd"]      >= -MAX_DD      and
            m["avg_monthly"] >= MIN_MONTHLY)


def score(m):
    """Điểm tổng hợp: ưu tiên PF + monthly + drawdown."""
    pf_score      = min(m["pf"] / 3.0, 1.0)           # chuẩn hoá tối đa 3.0
    monthly_score = min(m["avg_monthly"] / 5.0, 1.0)  # chuẩn hoá tối đa 5%
    dd_score      = 1.0 + m["max_dd"]                 # max_dd âm → gần 0 là tốt
    return 0.40 * pf_score + 0.35 * monthly_score + 0.25 * dd_score


# ══════════════════════════════════════════════════════════════
# 6. GRID SEARCH
# ══════════════════════════════════════════════════════════════

def run_grid_search(train_df, test_df):
    keys   = list(PARAM_GRID.keys())
    values = list(PARAM_GRID.values())
    combos = list(itertools.product(*values))
    total  = len(combos)

    print(f"\n🔍 Tổng số tổ hợp: {total:,}")
    print(f"   Train: {len(train_df)} nến | Test: {len(test_df)} nến")
    print(f"   Ngưỡng lọc: PF>{MIN_PF} | WR>{MIN_WINRATE}% | "
          f"DD<{MAX_DD:.0%} | Monthly>{MIN_MONTHLY}%\n")

    results  = []
    start    = time.time()
    passed   = 0

    for idx, combo in enumerate(combos, 1):
        params = dict(zip(keys, combo))

        # Bỏ qua nếu ema_fast >= ema_slow
        if params["ema_fast"] >= params["ema_slow"]:
            continue

        # Thêm chỉ báo
        try:
            tr_ind = add_indicators(
                train_df,
                params["ema_fast"], params["ema_slow"], params["ema_trend"],
                params["atr_period"], params["trade_hours"]
            )
            te_ind = add_indicators(
                test_df,
                params["ema_fast"], params["ema_slow"], params["ema_trend"],
                params["atr_period"], params["trade_hours"]
            )
        except Exception:
            continue

        # Backtest train
        tr_trades, tr_eq = backtest(tr_ind, params["atr_sl_mult"], params["atr_tp_mult"])
        tr_m = calc_metrics(tr_trades, tr_eq)

        if not passes_filter(tr_m):
            continue

        # Backtest test (out-of-sample)
        te_trades, te_eq = backtest(te_ind, params["atr_sl_mult"], params["atr_tp_mult"])
        te_m = calc_metrics(te_trades, te_eq)

        passed += 1
        tr_score = score(tr_m)

        results.append({
            "rank":           0,
            # Params
            "ema":            f"{params['ema_fast']}/{params['ema_slow']}/{params['ema_trend']}",
            "atr_period":     params["atr_period"],
            "sl_mult":        params["atr_sl_mult"],
            "tp_mult":        params["atr_tp_mult"],
            "hours":          f"{min(params['trade_hours'])}-{max(params['trade_hours'])}h",
            # Train metrics
            "tr_trades":      tr_m["trades"],
            "tr_wr":          tr_m["winrate"],
            "tr_pf":          tr_m["pf"],
            "tr_dd":          f"{tr_m['max_dd']:.1%}",
            "tr_monthly":     tr_m["avg_monthly"],
            # Test metrics
            "te_trades":      te_m["trades"] if te_m else "-",
            "te_wr":          te_m["winrate"] if te_m else "-",
            "te_pf":          te_m["pf"] if te_m else "-",
            "te_dd":          f"{te_m['max_dd']:.1%}" if te_m else "-",
            "te_monthly":     te_m["avg_monthly"] if te_m else "-",
            # Score
            "score":          round(tr_score, 4),
        })

        # Progress mỗi 200 combo
        if idx % 200 == 0:
            elapsed = time.time() - start
            eta     = elapsed / idx * (total - idx)
            print(f"  [{idx:>5}/{total}] Passed: {passed} | "
                  f"Elapsed: {elapsed:.0f}s | ETA: {eta:.0f}s")

    return sorted(results, key=lambda x: x["score"], reverse=True)


# ══════════════════════════════════════════════════════════════
# 7. IN KẾT QUẢ
# ══════════════════════════════════════════════════════════════

def print_results(results):
    sep = "═" * 80
    print(f"\n{sep}")
    print(f"  🏆 KẾT QUẢ GRID SEARCH – TOP {min(10, len(results))} TỔ HỢP TỐT NHẤT")
    print(sep)

    if not results:
        print("  ❌ Không có tổ hợp nào vượt qua tất cả ngưỡng lọc.")
        print("  💡 Thử giảm MIN_PF hoặc MIN_WINRATE trong script.")
        return

    for i, r in enumerate(results[:10], 1):
        r["rank"] = i
        print(f"\n  #{i}  EMA {r['ema']} | ATR {r['atr_period']} | "
              f"SL {r['sl_mult']}x | TP {r['tp_mult']}x | "
              f"Hours {r['hours']} UTC")
        print(f"  {'─'*60}")
        print(f"  {'':20} {'TRAIN (18th)':>20} {'TEST (6th)':>20}")
        print(f"  {'Số lệnh':20} {str(r['tr_trades']):>20} {str(r['te_trades']):>20}")
        print(f"  {'Winrate':20} {str(r['tr_wr'])+'%':>20} {str(r['te_wr'])+'%' if r['te_wr']!='-' else '-':>20}")
        print(f"  {'Profit Factor':20} {str(r['tr_pf']):>20} {str(r['te_pf']):>20}")
        print(f"  {'Max Drawdown':20} {str(r['tr_dd']):>20} {str(r['te_dd']):>20}")
        print(f"  {'Avg Monthly':20} {str(r['tr_monthly'])+'%':>20} {str(r['te_monthly'])+'%' if r['te_monthly']!='-' else '-':>20}")
        print(f"  {'Score':20} {r['score']:>20.4f}")

    # So sánh với tham số hiện tại
    print(f"\n{sep}")
    print("  📌 THAM SỐ HIỆN TẠI CỦA BOT: EMA 9/34/100 | ATR 14 | SL 2.0x | TP 3.0x")
    print(f"{sep}\n")

    best = results[0]
    ema_parts = best['ema'].split('/')
    print("  💡 KHUYẾN NGHỊ:")
    if best['score'] > 0.5:
        print(f"  ✅ Tìm thấy tham số tốt hơn:")
        print(f"     EMA {best['ema']} | ATR {best['atr_period']} | "
              f"SL {best['sl_mult']}x | TP {best['tp_mult']}x")
        print(f"     Train PF: {best['tr_pf']} | Test PF: {best['te_pf']}")

        # Kiểm tra có thực sự tốt hơn không
        if isinstance(best['te_pf'], (int, float)) and best['te_pf'] > 1.5:
            print(f"  ✅ Test PF > 1.5 → Tham số mới đáng tin cậy!")
        else:
            print(f"  ⚠️  Test PF thấp hơn train → Cẩn thận overfitting")
    else:
        print("  🟡 Tham số hiện tại 9/34/100 vẫn là tốt nhất.")
        print("     Không cần thay đổi gì.")


def save_results(results):
    if not results:
        return
    df = pd.DataFrame(results)
    fname = f"grid_search_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    df.to_csv(fname, index=False)
    print(f"  💾 Đã lưu {len(results)} kết quả vào: {fname}")


# ══════════════════════════════════════════════════════════════
# 8. MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  🔍 GRID SEARCH v2 – XAU/USD EMA + ATR")
    print("=" * 60)

    # 1. Tải dữ liệu
    df = fetch_data()

    # 2. Split train/test
    train_df, test_df = split_data(df)
    print(f"  Train: {len(train_df)} nến ({train_df.index[0].date()} → {train_df.index[-1].date()})")
    print(f"  Test : {len(test_df)} nến ({test_df.index[0].date()} → {test_df.index[-1].date()})")

    # 3. Grid search
    t0      = time.time()
    results = run_grid_search(train_df, test_df)
    elapsed = time.time() - t0

    print(f"\n✅ Hoàn thành trong {elapsed:.0f}s | "
          f"Tổng passed: {len(results)}")

    # 4. In kết quả
    print_results(results)

    # 5. Lưu file
    save_results(results)