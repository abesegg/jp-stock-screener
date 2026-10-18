"""分割・配当落ちによる段差を直すため、daily_ohlcv.csvの全銘柄を取り直して置き換える（2026-10-19の一括修正用）

使い方:
    uv run python repair_ohlcv.py           # 試し実行（書き出さずに集計だけ表示）
    uv run python repair_ohlcv.py --write   # daily_ohlcv.csv.bakにコピーしてから書き出す

ベンチマーク（1306.T）は手動修正した異常値があるため対象外にし、比率だけ表示する。
"""
import argparse
import shutil

import pandas as pd

from filters import BENCHMARK_TICKER, liquidity_passed_tickers
from ohlcv import download_ohlcv, replace_history

OHLCV_FILE = "daily_ohlcv.csv"
STEP_THRESHOLD = 0.05  # 新しい値 ÷ 今の値が1からこれ以上ずれる日がある銘柄を「段差があった銘柄」として表示する
DROP_CHECK = -0.4  # 置き換え後に1日でこれより大きく下落する銘柄が残っていないか確認する


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="daily_ohlcv.csvに書き出す（付けなければ試し実行）")
    args = parser.parse_args()

    existing = pd.read_csv(OHLCV_FILE)
    # スクリーニングに使う流動性フィルタ通過銘柄だけを対象にする（通過しない銘柄には、上場廃止などでyfinanceの値が壊れた銘柄がある）
    tickers = [t for t in liquidity_passed_tickers() if t != BENCHMARK_TICKER]
    fresh, failed = download_ohlcv(tickers + [BENCHMARK_TICKER], start=existing["date"].min())

    # ベンチマークは比率の確認のためだけに取得し、置き換えない
    benchmark = fresh[fresh["ticker"] == BENCHMARK_TICKER]
    fresh = fresh[fresh["ticker"] != BENCHMARK_TICKER]

    merged = fresh.merge(existing, on=["date", "ticker"], suffixes=("", "_old"))
    merged["ratio"] = merged["close"] / merged["close_old"]
    deviation = (merged["ratio"] - 1).abs().groupby(merged["ticker"]).max()
    stepped = deviation[deviation >= STEP_THRESHOLD].sort_values(ascending=False)

    repaired = replace_history(existing, fresh)
    def worst_drop(df):
        wide = df[df["ticker"].isin(tickers)].pivot(index="date", columns="ticker", values="close").sort_index()
        return wide.pct_change(fill_method=None).min()

    worst = worst_drop(repaired)
    drops = pd.DataFrame({"置き換え後": worst, "置き換え前": worst_drop(existing)})
    drops = drops[drops["置き換え後"] < DROP_CHECK].sort_values("置き換え後")

    print(f"\n対象: 流動性フィルタ通過 {len(tickers)}銘柄（{BENCHMARK_TICKER}を除く、取得失敗 {len([t for t in failed if t != BENCHMARK_TICKER])}銘柄）")
    print(f"置き換え: 取得 {len(fresh)}行、行数 {len(existing)} → {len(repaired)}")
    print(f"\n新しい値 ÷ 今の値が{STEP_THRESHOLD:.0%}以上ずれる日がある銘柄（段差があった銘柄）: {len(stepped)}銘柄")
    print(stepped.head(10).to_string())
    small = deviation[(deviation >= 0.005) & (deviation < STEP_THRESHOLD)]
    print(f"0.5〜{STEP_THRESHOLD:.0%}ずれる銘柄（配当落ちなど）: {len(small)}銘柄")
    print(f"\n置き換え後に1日で{-DROP_CHECK:.0%}超下落する銘柄: {len(drops)}銘柄（各銘柄の1日の最大下落率）")
    print(drops.to_string())

    b = benchmark.merge(existing, on=["date", "ticker"], suffixes=("", "_old"))
    b_ratio = b["close"] / b["close_old"]
    print(f"\n{BENCHMARK_TICKER}（置き換えない）: 新しい値 ÷ 今の値 中央値 {b_ratio.median():.4f}、範囲 {b_ratio.min():.4f}〜{b_ratio.max():.4f}")

    if args.write:
        shutil.copy(OHLCV_FILE, OHLCV_FILE + ".bak")
        repaired.to_csv(OHLCV_FILE, index=False)
        print(f"\n{OHLCV_FILE}.bakにコピーしてから{OHLCV_FILE}に書き出しました")
    else:
        print("\n試し実行のため書き出していません（書き出すには--writeを付ける）")


if __name__ == "__main__":
    main()
