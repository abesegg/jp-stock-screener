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

rows = []
missing_all = []     # 5日間すべて取得できない（上場廃止・売買停止の可能性）
missing_latest = []  # 最終日のみ未反映（取引のなかった銘柄など。次回実行で自動補完される）

for i, batch in enumerate(batches, start=1):
    print(f"バッチ {i}/{len(batches)}（{len(batch)}銘柄）取得中...")
    try:
        data = yf.download(batch, period="5d", progress=False, threads=True)
    except Exception as e:
        print(f"  バッチ全体が失敗: {e}")
        missing_all.extend(batch)
        continue

    # 取得した全営業日を保存する（実行漏れの日があっても直近5日以内なら次回実行で自動補完される）
    for ts in data.index:
        date = ts.strftime("%Y-%m-%d")
        day = {field: data[field].loc[ts] for field in FIELDS}

        for ticker in batch:
            if ticker in day["Close"].index and pd.notna(day["Close"][ticker]):
                rows.append({
                    "date": date,
                    "ticker": ticker,
                    "open": day["Open"][ticker],
                    "high": day["High"][ticker],
                    "low": day["Low"][ticker],
                    "close": day["Close"][ticker],
                    "volume": day["Volume"][ticker],
                })

    # 取得できなかった銘柄を「5日間すべて欠損」と「最終日のみ欠損」に分類する
    for ticker in batch:
        close = data["Close"][ticker] if ticker in data["Close"].columns else pd.Series(dtype=float)
        if close.isna().all():
            missing_all.append(ticker)
        elif pd.isna(close.iloc[-1]):
            missing_latest.append(ticker)

    if i < len(batches):
        time.sleep(SLEEP_BETWEEN_BATCHES)


combined = append_dedup(pd.DataFrame(rows), OHLCV_FILE, ["date", "ticker"])

print(f"\n取得成功: {len(tickers) - len(missing_all) - len(missing_latest)}銘柄（{len(rows)}行）")
print(f"最終日のみ未反映: {len(missing_latest)}銘柄（次回実行で自動補完）")
print(f"5日間すべて取得できず: {len(missing_all)}銘柄（上場廃止・売買停止の可能性）")
print(f"{OHLCV_FILE}: 累計{len(combined)}行")

# 失敗が0件の日も書き出し、前回の内容が残らないようにする
with open("daily_failed_tickers.txt", "w") as f:
    f.write("# 5日間すべて取得できず（上場廃止・売買停止の可能性）\n")
    f.write("".join(f"{t}\n" for t in missing_all))
    f.write("# 最終日のみ未反映（次回実行で自動補完）\n")
    f.write("".join(f"{t}\n" for t in missing_latest))
