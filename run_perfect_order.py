import pandas as pd

from filters import (
    PO_LOOKBACK,
    fresh_perfect_order_tickers,
    liquidity_passed_tickers,
    load_close_wide,
    relative_strength,
)

tickers = liquidity_passed_tickers()
close_wide = load_close_wide()
po_dates = fresh_perfect_order_tickers(tickers, close_wide)

universe = pd.read_csv("universe.csv", dtype={"コード": str})
# 該当0件の日もヘッダー行を書き出すため列名を明示する（notify.pyが読めるように）
result = pd.DataFrame(
    [{"ticker": t, "po_date": d.strftime("%Y-%m-%d")} for t, d in po_dates.items()],
    columns=["ticker", "po_date"],
)
result["relative_strength"] = result["ticker"].map(relative_strength(tickers, close_wide))
result = result.merge(universe[["ticker", "銘柄名", "33業種区分"]], on="ticker", how="left")
result = result.sort_values(["po_date", "relative_strength"], ascending=[False, False])

result.to_csv("perfect_order.csv", index=False)

print(f"流動性フィルタ通過: {len(tickers)}銘柄")
print(f"PO転換検出: {len(result)}銘柄（直近{PO_LOOKBACK}営業日以内にパーフェクトオーダーが新規成立し、成立日のRSがプラス）")
print(result.head(10))
