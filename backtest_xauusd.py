"""
╔══════════════════════════════════════════════════════════════╗
║   GRID SEARCH ĐA COIN – BTC / XRP / SOL                    ║
║   So sánh 1H vs 4H | Tham số tối ưu riêng từng coin        ║
║                                                              ║
║   Chạy:  python grid_search_multicoin.py                    ║
║   Thời gian ước tính: 30–60 phút (song song)               ║
╚══════════════════════════════════════════════════════════════╝
"""

import pandas as pd
import numpy as np
import itertools
import time
import json
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

try:
    import yfinance as yf
except ImportError:
    print("Chưa cài yfinance. Chạy: pip install yfinance pandas numpy")
    exit()

# ══════════════════════════════════════════════════════════════
# 1. CẤU HÌNH
# ══════════════════════════════════════════════════════════════

# Mapping OKX symbol → yfinance ticker
COINS = {
    "BTC-USDT-SWAP": "BTC-USD",
    "XRP-USDT-SWAP": "XRP-USD",
    "SOL-USDT-SWAP": "SOL-USD",
}

TIMEFRAMES = ["1h", "4h"]

PARAM_GRID = {
    "ema_fast":    [5, 9, 15, 21],
    "ema_slow":    [21, 26, 34, 50],
    "ema_trend":   [80, 100, 150, 200],
    "atr_period":  [10, 14, 20],
    "atr_sl_mult": [1.5, 2.0, 2.5, 3.0],
    "atr_tp_mult": [2.5, 3.0, 3.5, 4.0],
}

TRADE_HOURS   = list(range(6, 18))   # 6h–17h UTC (tốt nhất từ backtest XAU)
INITIAL_CAP   = 10_000
RISK_PER_TRADE = 0.01
MAX_DD_STOP   = 0.30
TRAIN_MONTHS  = 18
TEST_MONTHS   = 6
MIN_TRADES    = 10

# Ngưỡng lọc
MIN_PF        = 1.3
MIN_WR        = 38.0
MAX_DD        = 25.0
MIN_MONTHLY   = 0.5

# ══════════════════════════════════════════════════════════════
# 2. DATA
# ══════════════════════════════════════════════════════════════

def fetch_data(ticker: str, interval: str) -> pd.DataFrame:
    # yfinance giới hạn: 1h → max 2y | 4h → max 2y (730 ngày)
    period = "2y"
    df = yf.download(ticker, period=period, interval=interval,
                     auto_adjust=True, progress=False)
    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                  for c in df.columns]
    df.index = pd.to_datetime(df.index, utc=True)
    df.dropna(inplace=True)
    return df


def split(df: pd.DataFrame):
    cutoff = df.index[-1] - pd.DateOffset(months=TEST_MONTHS)
    return df[df.index < cutoff].copy(), df[df.index >= cutoff].copy()


# ══════════════════════════════════════════════════════════════
# 3. BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════

def add_indicators(df, p):
    d = df.copy()
    d["ema_fast"]  = d["close"].ewm(span=p["ema_fast"],  adjust=False).mean()
    d["ema_slow"]  = d["close"].ewm(span=p["ema_slow"],  adjust=False).mean()
    d["ema_trend"] = d["close"].ewm(span=p["ema_trend"], adjust=False).mean()

    hl  = d["high"] - d["low"]
    hpc = (d["high"] - d["close"].shift(1)).abs()
    lpc = (d["low"]  - d["close"].shift(1)).abs()
    d["atr"] = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)\
                 .ewm(span=p["atr_period"], adjust=False).mean()

    d["cross_up"]   = (d["ema_fast"] > d["ema_slow"]) & \
                      (d["ema_fast"].shift(1) <= d["ema_slow"].shift(1))
    d["cross_down"] = (d["ema_fast"] < d["ema_slow"]) & \
                      (d["ema_fast"].shift(1) >= d["ema_slow"].shift(1))
    d["uptrend"]    = d["close"] > d["ema_trend"]
    d["downtrend"]  = d["close"] < d["ema_trend"]
    d["valid_hour"] = d.index.hour.isin(TRADE_HOURS)
    return d.dropna()


def backtest(df, p):
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
                cp   = position["sl"] if hit_sl else position["tp"]
                mult = 1 if position["side"] == "long" else -1
                pnl  = mult * (cp - position["entry"]) * position["size"]
                capital += pnl
                peak     = max(peak, capital)
                trades.append({"pnl": pnl, "win": hit_tp})
                position = None

        if not position and row["valid_hour"]:
            signal = None
            if row["cross_up"]   and row["uptrend"]:   signal = "long"
            elif row["cross_down"] and row["downtrend"]: signal = "short"

            if signal:
                entry   = row["close"]
                atr     = row["atr"]
                sl_dist = p["atr_sl_mult"] * atr
                tp_dist = p["atr_tp_mult"] * atr
                sl      = entry - sl_dist if signal == "long" else entry + sl_dist
                tp      = entry + tp_dist if signal == "long" else entry - tp_dist

                if sl_dist > 0:
                    size = (capital * RISK_PER_TRADE) / sl_dist
                    position = {"side": signal, "entry": entry,
                                "sl": sl, "tp": tp, "size": size}

        equity.append(capital)

    return trades, pd.Series(equity)


def calc_metrics(trades, equity, n_months):
    if len(trades) < MIN_TRADES:
        return None
    df_t  = pd.DataFrame(trades)
    wins  = df_t[df_t["pnl"] > 0]
    loss  = df_t[df_t["pnl"] <= 0]
    pf_d  = abs(loss["pnl"].sum())
    pf    = wins["pnl"].sum() / pf_d if pf_d > 0 else 0
    roll  = equity.cummax()
    max_dd = ((equity - roll) / roll).min() * 100
    total_ret  = (equity.iloc[-1] - INITIAL_CAP) / INITIAL_CAP * 100
    avg_monthly = total_ret / max(1, n_months)
    winrate = len(wins) / len(df_t) * 100

    score = (pf / 3.0) * 0.4 + (avg_monthly / 5.0) * 0.35 + (1 + max_dd / 25) * 0.25

    return {
        "trades":      len(df_t),
        "per_month":   round(len(df_t) / n_months, 1),
        "winrate":     round(winrate, 1),
        "pf":          round(pf, 3),
        "max_dd":      round(max_dd, 1),
        "avg_monthly": round(avg_monthly, 2),
        "total_ret":   round(total_ret, 2),
        "score":       round(score, 4),
    }


def passes_filter(m):
    if not m:
        return False
    return (m["pf"] >= MIN_PF and
            m["winrate"] >= MIN_WR and
            abs(m["max_dd"]) <= MAX_DD and
            m["avg_monthly"] >= MIN_MONTHLY)


# ══════════════════════════════════════════════════════════════
# 4. GRID SEARCH CHO 1 COIN + 1 TIMEFRAME
# ══════════════════════════════════════════════════════════════

def run_single(args):
    """Chạy trong subprocess — không dùng logger, chỉ return dict."""
    okx_symbol, ticker, interval = args

    try:
        df = fetch_data(ticker, interval)
    except Exception as e:
        return {"symbol": okx_symbol, "interval": interval, "error": str(e), "results": []}

    train_df, test_df = split(df)

    # Số tháng thực tế
    delta = df.index[-1] - df.index[0]
    total_months = delta.days / 30
    n_train = max(1, total_months * TRAIN_MONTHS / (TRAIN_MONTHS + TEST_MONTHS))
    n_test  = max(1, total_months * TEST_MONTHS  / (TRAIN_MONTHS + TEST_MONTHS))

    keys   = list(PARAM_GRID.keys())
    values = list(PARAM_GRID.values())
    combos = list(itertools.product(*values))

    passed = []
    for combo in combos:
        p = dict(zip(keys, combo))
        if p["ema_fast"] >= p["ema_slow"]:
            continue

        tr_ind = add_indicators(train_df, p)
        tr_trades, tr_eq = backtest(tr_ind, p)
        tr_m = calc_metrics(tr_trades, tr_eq, n_train)

        if not passes_filter(tr_m):
            continue

        te_ind = add_indicators(test_df, p)
        te_trades, te_eq = backtest(te_ind, p)
        te_m = calc_metrics(te_trades, te_eq, n_test)

        passed.append({
            "params":  p,
            "train":   tr_m,
            "test":    te_m,
            "score":   tr_m["score"],
        })

    # Sắp xếp theo score train
    passed.sort(key=lambda x: x["score"], reverse=True)

    return {
        "symbol":   okx_symbol,
        "interval": interval,
        "error":    None,
        "candles":  len(df),
        "results":  passed[:10],   # top 10
    }


# ══════════════════════════════════════════════════════════════
# 5. MAIN – SONG SONG
# ══════════════════════════════════════════════════════════════

def print_result(r):
    sym = r["symbol"]
    tf  = r["interval"].upper()

    if r.get("error"):
        print(f"\n❌ {sym} {tf}: {r['error']}")
        return

    results = r["results"]
    print(f"\n{'═'*70}")
    print(f"  📊 {sym} | {tf} | {r['candles']} nến | {len(results)} tổ hợp passed")
    print(f"{'═'*70}")

    if not results:
        print("  ⚠️  Không có tổ hợp nào vượt ngưỡng lọc.")
        return

    for i, res in enumerate(results[:5], 1):
        p  = res["params"]
        tr = res["train"]
        te = res["test"]

        print(f"\n  #{i}  EMA {p['ema_fast']}/{p['ema_slow']}/{p['ema_trend']} | "
              f"ATR {p['atr_period']} | SL {p['atr_sl_mult']}x | TP {p['atr_tp_mult']}x")
        print(f"  {'─'*60}")
        print(f"  {'':35} {'TRAIN':>10}  {'TEST':>10}")
        print(f"  {'Số lệnh':35} {tr['trades']:>10}  {te['trades'] if te else '–':>10}")
        print(f"  {'Lệnh/tháng':35} {tr['per_month']:>10}  {te['per_month'] if te else '–':>10}")
        print(f"  {'Winrate':35} {str(tr['winrate'])+'%':>10}  {str(te['winrate'])+'%' if te else '–':>10}")
        print(f"  {'Profit Factor':35} {tr['pf']:>10}  {te['pf'] if te else '–':>10}")
        print(f"  {'Max Drawdown':35} {str(tr['max_dd'])+'%':>10}  {str(te['max_dd'])+'%' if te else '–':>10}")
        print(f"  {'Avg Monthly':35} {str(tr['avg_monthly'])+'%':>10}  {str(te['avg_monthly'])+'%' if te else '–':>10}")
        print(f"  {'Score':35} {tr['score']:>10}")


def main():
    print("=" * 70)
    print("  🔍 GRID SEARCH ĐA COIN – SONG SONG")
    print(f"  Coins: {', '.join(COINS.keys())}")
    print(f"  Timeframes: {', '.join(TIMEFRAMES)}")
    print(f"  Tổ hợp/coin/tf: {len(list(itertools.product(*PARAM_GRID.values()))):,}")
    print(f"  Tổng tasks: {len(COINS) * len(TIMEFRAMES)}")
    print("=" * 70)

    tasks = [
        (okx_sym, ticker, tf)
        for okx_sym, ticker in COINS.items()
        for tf in TIMEFRAMES
    ]

    t0 = time.time()
    all_results = []

    # Song song với ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=len(tasks)) as executor:
        futures = {executor.submit(run_single, task): task for task in tasks}
        done = 0
        for future in as_completed(futures):
            done += 1
            task = futures[future]
            try:
                result = future.result()
                all_results.append(result)
                print(f"  ✅ [{done}/{len(tasks)}] Xong: {task[0]} {task[2].upper()}")
            except Exception as e:
                print(f"  ❌ [{done}/{len(tasks)}] Lỗi: {task[0]} {task[2].upper()} — {e}")

    elapsed = time.time() - t0
    print(f"\n⏱ Hoàn thành trong {elapsed:.0f}s")

    # In kết quả
    # Sắp xếp theo symbol rồi timeframe
    all_results.sort(key=lambda x: (x["symbol"], x["interval"]))
    for r in all_results:
        print_result(r)

    # ── Bảng so sánh tổng hợp ────────────────────────────────
    print(f"\n\n{'═'*70}")
    print("  🏆 BẢNG SO SÁNH – TOP 1 MỖI COIN/TIMEFRAME")
    print(f"{'═'*70}")
    print(f"  {'Coin + TF':<25} {'Params':<28} {'Tr.PF':>6} {'Te.PF':>6} {'Te.DD':>7} {'Te.Mo%':>8}")
    print(f"  {'─'*70}")

    summary = []
    for r in all_results:
        if r.get("error") or not r["results"]:
            continue
        best = r["results"][0]
        p    = best["params"]
        tr   = best["train"]
        te   = best["test"]
        label = f"{r['symbol']} {r['interval'].upper()}"
        params = f"EMA {p['ema_fast']}/{p['ema_slow']}/{p['ema_trend']} SL{p['atr_sl_mult']} TP{p['atr_tp_mult']}"
        te_pf  = f"{te['pf']:.3f}" if te else "–"
        te_dd  = f"{te['max_dd']:.1f}%" if te else "–"
        te_mon = f"{te['avg_monthly']:.2f}%" if te else "–"
        star   = " ⭐" if te and te["pf"] >= 1.8 else ""
        print(f"  {label:<25} {params:<28} {tr['pf']:>6.3f} {te_pf:>6} {te_dd:>7} {te_mon:>8}{star}")
        summary.append({"label": label, "params": p, "train": tr, "test": te})

    # Lưu kết quả JSON
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = Path(f"grid_multicoin_{ts}.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n💾 Đã lưu kết quả: {out_file}")
    print(f"{'═'*70}\n")


if __name__ == "__main__":
    main()