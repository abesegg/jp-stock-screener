import time

import pandas as pd
import yfinance as yf

BATCH_SIZE = 200
SLEEP_BETWEEN_BATCHES = 2  # 秒。レート制限回避のための待機
THRESHOLD = 100_000_000  # 1億円

universe = pd.read_csv("universe.csv", dtype={"コード": str})
tickers = universe["ticker"].tolist()

batches = [tickers[i:i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]

results = []
failed_tickers = []

for i, batch in enumerate(batches, start=1):
    print(f"バッチ {i}/{len(batches)}（{len(batch)}銘柄）取得中...")
    try:
        data = yf.download(batch, period="1mo", progress=False, threads=True)
    except Exception as e:
        print(f"  バッチ全体が失敗: {e}")
        failed_tickers.extend(batch)
        continue

    close = data["Close"]
    volume = data["Volume"]
    turnover = close * volume

    turnover_mean_10d = turnover.rolling(10).mean().iloc[-1]
    turnover_median_10d = turnover.rolling(10).median().iloc[-1]

    batch_result = pd.DataFrame({
        "10日平均売買代金": turnover_mean_10d,
        "10日中央値売買代金": turnover_median_10d,
    })

    ok = batch_result.dropna(how="all")
    ng = batch_result[batch_result.isna().all(axis=1)].index.tolist()
    failed_tickers.extend(ng)

    results.append(ok)

    if i < len(batches):
        time.sleep(SLEEP_BETWEEN_BATCHES)

result = pd.concat(results)
result["除外"] = result["10日中央値売買代金"] < THRESHOLD
result = result.merge(
    universe[["ticker", "銘柄名", "市場・商品区分", "33業種区分"]],
    left_index=True, right_on="ticker",
).set_index("ticker")

result.to_csv("screening_result.csv", encoding="utf-8-sig")

print(f"\n取得成功: {len(result)}銘柄 / 全{len(tickers)}銘柄")
print(f"取得失敗: {len(failed_tickers)}銘柄")
print(f"除外（流動性基準未達）: {result['除外'].sum()}銘柄")
print("結果を screening_result.csv に保存しました")

if failed_tickers:
    with open("failed_tickers.txt", "w") as f:
        f.write("\n".join(failed_tickers))
    print("取得失敗銘柄を failed_tickers.txt に保存しました")
