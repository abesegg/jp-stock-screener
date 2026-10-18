"""検証用に、daily_ohlcv.csvより前の過去データを取得してohlcv_history.csvに保存する（1回だけ使う）

使い方（プロジェクトフォルダで）:
    uv run python analysis/fetch_history.py           # 試し実行（書き出さずに集計だけ表示）
    uv run python analysis/fetch_history.py --write   # ohlcv_history.csvに書き出す

日次運用のdaily_ohlcv.csvは読むだけで書き換えない。
yfinanceの価格は取得時点で配当・分割を調整した値のため、そのまま継ぐとdaily_ohlcv.csvとの境目で段差ができる。
銘柄ごとに重なる期間の終値の比率（daily_ohlcv.csv ÷ 今回取得分）を求め、古い分の価格に掛けて揃える。
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from filters import BENCHMARK_TICKER, liquidity_passed_tickers  # noqa: E402
from ohlcv import download_ohlcv  # noqa: E402

PERIOD = "2y"
OVERLAP_DAYS = 20  # 比率を求めるのに使う、重なる期間の日数（daily_ohlcv.csvの最初の20営業日）
RATIO_CHECK = 0.005  # daily_ohlcv.csvは一括修正で今の調整に揃っているため、比率は1になるはず。これ以上ずれる銘柄を一覧にする
PRICE_COLS = ["open", "high", "low", "close"]
OHLCV_FILE = ROOT / "daily_ohlcv.csv"
HISTORY_FILE = ROOT / "ohlcv_history.csv"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="ohlcv_history.csvに書き出す（付けなければ試し実行）")
    args = parser.parse_args()

    tickers = liquidity_passed_tickers(ROOT / "screening_result.csv") + [BENCHMARK_TICKER]
    existing = pd.read_csv(OHLCV_FILE)
    existing = existing[existing["ticker"].isin(tickers)]
    first_date = existing.groupby("ticker")["date"].min()

    new, failed = download_ohlcv(tickers, period=PERIOD)

    histories, ratios = [], {}
    for ticker, rows in new.groupby("ticker"):
        if ticker not in first_date:
            continue
        # 重なる期間の終値の比率（中央値）で、古い分の価格をdaily_ohlcv.csvに揃える。出来高は調整しない
        overlap = rows.merge(existing[existing["ticker"] == ticker], on=["date", "ticker"], suffixes=("", "_old"))
        overlap = overlap.sort_values("date").head(OVERLAP_DAYS)
        older = rows[rows["date"] < first_date[ticker]].copy()
        if overlap.empty or older.empty:
            continue
        ratio = overlap["close_old"] / overlap["close"]
        ratios[ticker] = {"ratio": ratio.median(), "spread": ratio.max() / ratio.min() - 1}
        older[PRICE_COLS] = older[PRICE_COLS] * ratio.median()
        histories.append(older)

    history = pd.concat(histories, ignore_index=True).sort_values(["date", "ticker"]).reset_index(drop=True)
    ratios = pd.DataFrame(ratios).T

    print(f"\n対象: {len(tickers)}銘柄（取得失敗 {len(failed)}銘柄、古い分あり {history['ticker'].nunique()}銘柄）")
    print(f"追加行数: {len(history)}行（{history['date'].min()}〜{history['date'].max()}）")
    print(f"ファイルサイズの見込み: 約{len(history) * OHLCV_FILE.stat().st_size / (len(existing) or 1) / 1e6:.0f}MB")
    print("\n比率の分布:")
    print(ratios["ratio"].describe(percentiles=[0.01, 0.05, 0.5, 0.95, 0.99]).to_string())
    off = ratios[(ratios["ratio"] - 1).abs() >= RATIO_CHECK].sort_values("ratio")
    print(f"\n比率が1から{RATIO_CHECK:.1%}以上ずれる銘柄（一括修正後に分割・配当落ちがあった銘柄など）: {len(off)}銘柄")
    print(off.to_string())
    unstable = ratios[ratios["spread"] >= 0.01]
    print(f"\n重なる期間で比率が1%以上ぶれる銘柄（期間中に分割・配当落ちがあった可能性）: {len(unstable)}銘柄")
    print(unstable.sort_values("spread", ascending=False).head(20).to_string())

    if args.write:
        history.to_csv(HISTORY_FILE, index=False)
        print(f"\n{HISTORY_FILE.name}に書き出しました")
    else:
        print("\n試し実行のため書き出していません（書き出すには--writeを付ける）")


if __name__ == "__main__":
    main()
