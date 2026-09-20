import time

import pandas as pd
import yfinance as yf

from storage import append_dedup

BATCH_SIZE = 200
SLEEP_BETWEEN_BATCHES = 2
OHLCV_FILE = "daily_ohlcv.csv"
FIELDS = ["Open", "High", "Low", "Close", "Volume"]
BENCHMARK_TICKER = "1306.T"  # TOPIX連動ETF（相対強度算出用のベンチマーク）

universe = pd.read_csv("universe.csv", dtype={"コード": str})
tickers = universe["ticker"].tolist() + [BENCHMARK_TICKER]
batches = [tickers[i:i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]

rows, failed_tickers = [], []

for i, batch in enumerate(batches, start=1):
    print(f"バッチ {i}/{len(batches)}（{len(batch)}銘柄）取得中...")
    try:
        data = yf.download(batch, period="5d", progress=False, threads=True)
    except Exception as e:
        print(f"  バッチ全体が失敗: {e}")
        failed_tickers.extend(batch)
        continue

    latest = {field: data[field].iloc[-1] for field in FIELDS}
    date = data.index[-1].strftime("%Y-%m-%d")

    for ticker in batch:
        if ticker in latest["Close"].index and pd.notna(latest["Close"][ticker]):
            rows.append({
                "date": date,
                "ticker": ticker,
                "open": latest["Open"][ticker],
                "high": latest["High"][ticker],
                "low": latest["Low"][ticker],
                "close": latest["Close"][ticker],
                "volume": latest["Volume"][ticker],
            })
        else:
            failed_tickers.append(ticker)

    if i < len(batches):
        time.sleep(SLEEP_BETWEEN_BATCHES)


combined = append_dedup(pd.DataFrame(rows), OHLCV_FILE, ["date", "ticker"])

print(f"\n取得成功: {len(rows)}銘柄 / 取得失敗: {len(failed_tickers)}銘柄")
print(f"{OHLCV_FILE}: 累計{len(combined)}行")

if failed_tickers:
    with open("daily_failed_tickers.txt", "w") as f:
        f.write("\n".join(failed_tickers))
