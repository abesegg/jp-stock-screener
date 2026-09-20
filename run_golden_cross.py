import pandas as pd

from filters import (
    GOLDEN_CROSS_LONG,
    GOLDEN_CROSS_LOOKBACK,
    GOLDEN_CROSS_SHORT,
    golden_cross_tickers,
    liquidity_passed_tickers,
    load_close_wide,
)

tickers = liquidity_passed_tickers()
close_wide = load_close_wide()
cross_dates = golden_cross_tickers(tickers, close_wide, GOLDEN_CROSS_SHORT, GOLDEN_CROSS_LONG, GOLDEN_CROSS_LOOKBACK)

universe = pd.read_csv("universe.csv", dtype={"コード": str})
result = pd.DataFrame(
    [{"ticker": t, "cross_date": d.strftime("%Y-%m-%d")} for t, d in cross_dates.items()]
)
if not result.empty:
    result = result.merge(universe[["ticker", "銘柄名", "33業種区分"]], on="ticker", how="left")
    result = result.sort_values("cross_date", ascending=False)

result.to_csv("golden_cross.csv", index=False)

print(f"流動性フィルタ通過: {len(tickers)}銘柄")
print(f"ゴールデンクロス検出: {len(result)}銘柄（{GOLDEN_CROSS_SHORT}EMAが{GOLDEN_CROSS_LONG}EMAを直近{GOLDEN_CROSS_LOOKBACK}営業日以内に上抜け）")
print(result.head(10))
