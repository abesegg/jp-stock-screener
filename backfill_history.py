import time

import pandas as pd
import yfinance as yf

from filters import liquidity_passed_tickers
from storage import append_dedup

BATCH_SIZE = 200
SLEEP_BETWEEN_BATCHES = 2
BACKFILL_PERIOD = "1y"  # 週足表示・将来の長期EMA計算に十分な余裕を持たせる
OHLCV_FILE = "daily_ohlcv.csv"
FIELDS = ["Open", "High", "Low", "Close", "Volume"]
BENCHMARK_TICKER = "1306.T"

tickers = liquidity_passed_tickers() + [BENCHMARK_TICKER]
batches = [tickers[i:i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]

frames, failed_tickers = [], []

for i, batch in enumerate(batches, start=1):
    print(f"バッチ {i}/{len(batches)}（{len(batch)}銘柄）取得中...")
    try:
        data = yf.download(batch, period=BACKFILL_PERIOD, progress=False, threads=True)
    except Exception as e:
        print(f"  バッチ全体が失敗: {e}")
        failed_tickers.extend(batch)
        continue

    field_frames = []
    for field in FIELDS:
        long = data[field].reset_index().melt(
            id_vars="Date", var_name="ticker", value_name=field.lower()
        )
        field_frames.append(long.set_index(["Date", "ticker"]))

    batch_df = pd.concat(field_frames, axis=1).reset_index()
    batch_df = batch_df.rename(columns={"Date": "date"})
    batch_df["date"] = batch_df["date"].dt.strftime("%Y-%m-%d")
    batch_df = batch_df.dropna(subset=["close"])
    frames.append(batch_df)

    if i < len(batches):
        time.sleep(SLEEP_BETWEEN_BATCHES)

new_data = pd.concat(frames, ignore_index=True)
combined = append_dedup(new_data, OHLCV_FILE, ["date", "ticker"])

print(f"\nバックフィル対象: {len(tickers)}銘柄")
print(f"取得行数: {len(new_data)}行")
print(f"{OHLCV_FILE}: 累計{len(combined)}行")

if failed_tickers:
    with open("backfill_failed_tickers.txt", "w") as f:
        f.write("\n".join(failed_tickers))
    print(f"取得失敗: {len(failed_tickers)}銘柄（backfill_failed_tickers.txtに保存）")
