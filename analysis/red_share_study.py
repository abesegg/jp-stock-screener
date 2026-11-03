"""監視中の銘柄のうちRSがマイナスの割合（ダッシュボードの「赤の割合」）と、TOPIXの動きの関係の検証

毎日、監視中の銘柄（PO新規+RSプラスの翌日から20営業日目まで。filters.pyのwatch_list()と同じ）のうち、
RSがマイナスの銘柄の割合を「赤の割合」とする。赤の割合で日を5等分して、次を比べる。
1. 同時の関係（記述のみ）: その日までのTOPIXの騰落率（5・21営業日）と、監視中の銘柄の騰落率の平均（21営業日）
2. 先行きの関係: その日の終値から5・10・20営業日後までのTOPIXの騰落率

合格の基準（先行きの関係。結果を見る前に決めたもの）: 赤の割合が高いほどその後のTOPIXが弱い向きが、
1. 5等分の平均の並びがおおむね単調（順位相関-0.9以下）
2. 2025年・2026年のどちらでも、最も赤の多いグループの平均が最も少ないグループより低い
3. 5・10・20営業日のどれでも1・2を満たす
すべて満たせば「傾向あり」。

実行（プロジェクトフォルダで）: uv run python analysis/red_share_study.py
データは読むだけで書き換えない。

注意: データは約2年分で、ほとんどが上昇相場。大きな下げの局面は数回しかなく、先行きの関係はそれに左右される。
毎日の値を使うが前後の日の期間が重なるため、日数ほどの確からしさはない。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis.signal_study import load_wide  # noqa: E402
from filters import (  # noqa: E402
    BENCHMARK_TICKER,
    WATCH_DAYS,
    fresh_perfect_order_wide,
    liquidity_passed_tickers,
    relative_strength_wide,
)

SIGNAL_FROM = "2025-01-01"  # この日以降の日を対象にする（2024年10〜12月はEMAの助走期間）
MIN_WATCH = 20  # 監視中の銘柄がこれより少ない日は除く（割合がぶれやすいため）
PAST_DAYS = [5, 21]  # 同時の関係: その日までのTOPIXの騰落率の日数
FORWARD_DAYS = [5, 10, 20]  # 先行きの関係: その日から何営業日後までのTOPIXの騰落率
N_GROUPS = 5
MAX_RHO = -0.9  # 合格の基準: 順位相関


def red_share(close_wide, tickers):
    # 日ごとの監視中の銘柄数と赤の割合、監視中の銘柄の21営業日の騰落率の平均 → DataFrame
    close = close_wide[tickers]
    rs = relative_strength_wide(close_wide)[tickers]
    signal = fresh_perfect_order_wide(close) & (rs > 0)
    # 前日までのWATCH_DAYS営業日にシグナルがあれば監視中（シグナルの翌日が1日目）
    watching = signal.shift(1, fill_value=False).astype(float).rolling(WATCH_DAYS, min_periods=1).max() > 0
    watching &= rs.notna()
    ret21 = close.ffill().pct_change(21, fill_method=None)
    return pd.DataFrame({
        "監視中": watching.sum(axis=1),
        "赤の割合": (watching & (rs < 0)).sum(axis=1) / watching.sum(axis=1),
        "監視中の騰落率21日": ret21.where(watching).mean(axis=1),
    })


def rank_corr(values):
    # グループの順番と値の順位相関（scipyを使わない）
    return pd.Series(range(len(values))).corr(pd.Series(list(values)).rank())


def main():
    close_wide, _ = load_wide(with_history=True)
    tickers = [t for t in liquidity_passed_tickers(ROOT / "screening_result.csv") if t in close_wide.columns]
    bench = close_wide[BENCHMARK_TICKER].ffill()
    df = red_share(close_wide, tickers)
    for k in PAST_DAYS:
        df[f"TOPIX 過去{k}日"] = bench.pct_change(k, fill_method=None)
    for k in FORWARD_DAYS:
        df[f"TOPIX {k}日後"] = bench.shift(-k) / bench - 1
    df = df[(df.index >= SIGNAL_FROM) & (df["監視中"] >= MIN_WATCH)]
    df["年"] = df.index.year
    df["グループ"] = pd.qcut(df["赤の割合"], N_GROUPS, labels=False)

    print(f"対象: 流動性フィルタ通過{len(tickers)}銘柄、{SIGNAL_FROM}〜{df.index[-1]:%Y-%m-%d}の{len(df)}日"
          f"（監視中が{MIN_WATCH}銘柄以上の日）。監視中の銘柄数は中央値{df['監視中'].median():.0f}")
    print(f"赤の割合: 中央値{df['赤の割合'].median():.0%}、範囲{df['赤の割合'].min():.0%}〜{df['赤の割合'].max():.0%}")

    cols_now = [f"TOPIX 過去{k}日" for k in PAST_DAYS] + ["監視中の騰落率21日"]
    cols_fwd = [f"TOPIX {k}日後" for k in FORWARD_DAYS]
    table = pd.DataFrame({
        f"{int(q) + 1}（{g['赤の割合'].min():.0%}〜{g['赤の割合'].max():.0%}）":
            {"日数": len(g)} | {c: g[c].mean() for c in cols_now + cols_fwd}
        for q, g in df.groupby("グループ")}).T
    print("\n■ 赤の割合で5等分した平均（騰落率）")
    print(table.to_string(formatters={c: "{:+.1%}".format for c in cols_now + cols_fwd} | {"日数": "{:.0f}".format}))

    print("\n■ 合格の判定（先行きの関係）")
    reasons = []
    for c in cols_fwd:
        means = df.groupby("グループ")[c].mean()
        rho = rank_corr(means.to_numpy())
        line = [f"{c}: 順位相関{rho:+.2f}"]
        if not rho <= MAX_RHO + 1e-9:
            reasons.append(f"{c}の並びが単調でない")
        for y, g in df.groupby("年"):
            m = g.groupby("グループ")[c].mean()
            hi, lo = m.get(N_GROUPS - 1, np.nan), m.get(0, np.nan)
            line.append(f"{y}年 最多{hi:+.1%}・最少{lo:+.1%}")
            if not hi < lo:
                reasons.append(f"{c}の{y}年は向きが違う")
        print("  " + "、".join(line))
    print("  → " + ("傾向あり" if not reasons else "傾向なし（" + "・".join(reasons) + "）"))

    print("\n■ 月ごとの赤の割合の平均とTOPIXの月間騰落率")
    month = df.groupby(df.index.to_period("M"))["赤の割合"].mean()
    bench_m = bench.groupby(bench.index.to_period("M")).last().pct_change()
    print(pd.DataFrame({"赤の割合": month, "TOPIX": bench_m.reindex(month.index)}).to_string(
        formatters={"赤の割合": "{:.0%}".format, "TOPIX": "{:+.1%}".format}))


if __name__ == "__main__":
    main()
