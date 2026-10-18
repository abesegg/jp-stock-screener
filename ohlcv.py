"""daily_ohlcv.csvの取得・置き換えの共通処理（分割・配当落ちによる段差の修正）"""
import time

import pandas as pd
import yfinance as yf

FIELDS = ["Open", "High", "Low", "Close", "Volume"]
MISMATCH_TOLERANCE = 0.005  # 保存済みの終値と取得した終値がこれ以上ずれたら、分割・配当落ちで過去の価格が調整されたとみなす


def download_ohlcv(tickers, batch_size=200, sleep=2, **kwargs):
    """yfinanceから取得して縦長（date, ticker, open, high, low, close, volume）で返す → (DataFrame, 取得失敗の銘柄)"""
    frames, failed = [], []
    batches = [tickers[i:i + batch_size] for i in range(0, len(tickers), batch_size)]
    for i, batch in enumerate(batches, start=1):
        print(f"バッチ {i}/{len(batches)}（{len(batch)}銘柄）取得中...")
        try:
            data = yf.download(batch, progress=False, threads=True, **kwargs)
        except Exception as e:
            print(f"  バッチ全体が失敗: {e}")
            failed.extend(batch)
            continue
        data = data.copy()  # yfinanceの戻り値は断片化しており、そのまま縦長に変換するとPerformanceWarningが大量に出るため
        long = pd.concat(
            [data[f].reset_index().melt(id_vars="Date", var_name="ticker", value_name=f.lower()).set_index(["Date", "ticker"])
             for f in FIELDS],
            axis=1,
        ).reset_index().rename(columns={"Date": "date"})
        long["date"] = long["date"].dt.strftime("%Y-%m-%d")
        long = long.dropna(subset=["close"])
        fetched = set(long["ticker"])
        failed.extend(t for t in batch if t not in fetched)
        frames.append(long)
        if i < len(batches):
            time.sleep(sleep)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(), failed


def mismatched_tickers(fetched, existing, tolerance=MISMATCH_TOLERANCE):
    """同じ日付で保存済みの終値と取得した終値がずれている銘柄（過去の価格が調整された銘柄）"""
    merged = fetched.merge(existing, on=["date", "ticker"], suffixes=("", "_old"))
    diff = (merged["close"] / merged["close_old"] - 1).abs()
    return sorted(merged.loc[diff >= tolerance, "ticker"].unique())


def replace_history(existing, fresh):
    """freshに含まれる銘柄の、freshの最初の日以降の行をfreshで置き換える（それより前の行は残す）
    置き換えるのは各銘柄が今持っている期間だけで、それより前の行は足さない（新たに足すのはbackfill_history.pyの役割）"""
    first = existing.groupby("ticker")["date"].min()
    fresh = fresh[fresh["date"] >= fresh["ticker"].map(first)]
    start = fresh.groupby("ticker")["date"].min()
    stale = existing["ticker"].isin(start.index) & (existing["date"] >= existing["ticker"].map(start))
    combined = pd.concat([existing[~stale], fresh])
    return combined.sort_values(["date", "ticker"]).reset_index(drop=True)
