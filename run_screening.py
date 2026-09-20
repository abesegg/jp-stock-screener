import pandas as pd

from filters import BENCHMARK_TICKER, RS_PERIOD, liquidity_passed_tickers, load_close_wide, relative_strength

TOP_PCT = 0.10

tickers = liquidity_passed_tickers()
close_wide = load_close_wide()
rs = relative_strength(tickers, close_wide, BENCHMARK_TICKER, RS_PERIOD)

top_n = max(1, int(len(rs) * TOP_PCT))
top = rs.head(top_n)

universe = pd.read_csv("universe.csv", dtype={"コード": str})
result = top.rename("relative_strength").reset_index()
result = result.merge(universe[["ticker", "銘柄名", "33業種区分"]], on="ticker", how="left")

result.to_csv("rs_ranking.csv", index=False)

print(f"流動性フィルタ通過: {len(tickers)}銘柄")
print(f"相対強度 計算成功: {len(rs)}銘柄")
print(f"上位{TOP_PCT * 100:.0f}%（{top_n}銘柄）を rs_ranking.csv に保存しました")
print(result.head(10))
