import pandas as pd

from filters import liquidity_passed_tickers
from ohlcv import download_ohlcv, replace_history

BACKFILL_PERIOD = "1y"  # 週足表示・将来の長期EMA計算に十分な余裕を持たせる
MIN_HISTORY_DAYS = 200  # 蓄積がこれ未満の銘柄をバックフィル対象にする（1年分は約245営業日）
OHLCV_FILE = "daily_ohlcv.csv"

# 流動性フィルタ通過銘柄のうち、蓄積が足りない銘柄だけを対象にする（月次更新で新たに通過した銘柄など）
# ベンチマーク（1306.T）は日次で取得済みで、異常値を手動修正した日もあるため対象外
existing = pd.read_csv(OHLCV_FILE)
counts = existing["ticker"].value_counts()
tickers = [t for t in liquidity_passed_tickers() if counts.get(t, 0) < MIN_HISTORY_DAYS]
if not tickers:
    print(f"蓄積が{MIN_HISTORY_DAYS}日未満の銘柄はありません。バックフィル不要です。")
    raise SystemExit

new_data, failed_tickers = download_ohlcv(tickers, period=BACKFILL_PERIOD)
# 既存行（日次取得分）は取得した期間の分を置き換える。既存行は分割・配当落ちの前に取得した調整前の値のことがあり、
# 足りない日付だけを足すと、既存行と補完した行の間に段差ができるため
combined = replace_history(existing, new_data, extend=True) if not new_data.empty else existing
combined.to_csv(OHLCV_FILE, index=False)

print(f"\nバックフィル対象: {len(tickers)}銘柄")
print(f"取得行数: {len(new_data)}行")
print(f"追加行数: {len(combined) - len(existing)}行")
print(f"{OHLCV_FILE}: 累計{len(combined)}行")

if failed_tickers:
    with open("backfill_failed_tickers.txt", "w") as f:
        f.write("\n".join(failed_tickers))
    print(f"取得失敗: {len(failed_tickers)}銘柄（backfill_failed_tickers.txtに保存）")
