"""RS上位に入ってから何日目に入ると期待値が高いかの検証

RS上位10%に初めて入った日（直前20営業日に上位10%に入っていなかった銘柄が入った日）から、
k営業日目（入る日）の翌日の終値で買ったとして、60営業日後までのTOPIX超過リターンを入る日ごとに比べる。
日数は初めて入った日から数え、途中で上位10%を出てもリセットしない。
RSは結果（過去21営業日のTOPIX比の騰落率）のため、どの時点で気づけば有利かを調べる（confirm_study.mdの次の検証）。

比べる相手: 1件ごとに、入る日に判定できる全銘柄から無作為に5銘柄。
POが成立している銘柄に絞った集計では、入る日にPOが成立している銘柄からも無作為に5銘柄選ぶ

合格の基準（入る日ごと。結果を見る前に決めたもの）:
1. 平均が比べる相手を2ポイント以上上回り、+30%以上の割合が比べる相手の1.5倍以上
2. -30%以下の割合の増え方（比べる相手との差）が、+30%以上の割合の増え方より小さい
3. 2025年と2026年（入る日の年）のどちらでも、平均の差がプラスで、+30%以上の割合が比べる相手より多い
中央値は参考として並べるだけにする（上昇銘柄の成績は一部の大勝ちで決まるため）

実行（プロジェクトフォルダで）: uv run python analysis/rs_entry_study.py
データは読むだけで書き換えない。

注意: RSの順位は相対的な指標で、上位10%には相場全体の強弱にかかわらず常に1割の銘柄が入る。同じ日の銘柄と比べることで相場の影響はある程度打ち消せるが、
「他より強い」ことしか測っていない。取引の期間は重なり、同じ相場の影響を受けるため、件数ほどの確からしさはない。
流動性フィルタは現時点の通過銘柄を過去にも当てはめている。データは上昇相場の期間しか含まない。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis.confirm_study import forward_excess  # noqa: E402
from analysis.signal_study import RS_TOP_PCT, load_wide  # noqa: E402
from filters import (  # noqa: E402
    BENCHMARK_TICKER,
    EMA_WARMUP_DAYS,
    PO_FRESH_DAYS,
    liquidity_passed_tickers,
    perfect_order_wide,
    relative_strength_wide,
)

SIGNAL_FROM = "2025-01-01"  # この日以降の初めての入りを対象にする（2024年10〜12月はEMAの助走期間）
ENTRY_DAYS = [0, 5, 10, 20, 40]  # 初めて入った日から何営業日目に入るか
FRESH_DAYS = PO_FRESH_DAYS  # 直前のこの営業日数に上位10%に入っていなかった銘柄の入りを「初めて」とする（PO新規と同じ考え方）
HORIZON = 60  # 入る日の翌日から超過リターンを測る営業日数
BIG_WIN, BIG_LOSS = 0.30, -0.30  # 大きく上がった・下がったとみなす超過リターン
N_CONTROL = 5  # 比べる相手: 1件につき選ぶ銘柄の数
SEED = 0  # 比べる相手を選ぶ乱数の種（毎回同じ結果にする）
MIN_MEAN_GAP = 0.02  # 合格の基準: 平均の差
MIN_UP_RATIO = 1.5  # 合格の基準: +30%以上の割合の倍率
SHARE_DAY = 20  # 参考: この入る日で、上位10%にいた日の割合（定着度）で3等分する


def stats(r):
    r = r.dropna()
    return {"件数": len(r), "平均": r.mean(), "中央値": r.median(), "上": (r >= BIG_WIN).mean(), "下": (r <= BIG_LOSS).mean()}


def fmt(s):
    return (f"{s['件数']:5d}件 平均{s['平均'] * 100:+5.1f}% 中央値{s['中央値'] * 100:+5.1f}%"
            f" +30%以上{s['上'] * 100:4.1f}% -30%以下{s['下'] * 100:4.1f}%")


def collect(a, first, top, po, pools, rng):
    # 入る日ごとの1件と比べる相手 → (1件ごとのDataFrame, 比べる相手のDataFrame)。比べる相手は1件の番号で対応づける
    n = len(a["close"])
    rows, controls = [], []
    for i, j in zip(*np.nonzero(first)):
        for k in ENTRY_DAYS:
            d = i + k
            if d + 1 + HORIZON > n - 1:
                break
            if np.isnan(a["raw_close"][d, j]):
                continue  # 入る日に終値がない（売買停止など）
            row_id = len(rows)
            rows.append({"入る日": k, "日付": a["dates"][d], "ticker": a["tickers"][j], "PO": po[d, j],
                         "上位の割合": top[i:d + 1, j].mean(), "超過リターン": forward_excess(d, j, a)})
            for pool_name, pool in pools.items():
                cand = np.flatnonzero(pool[d])
                for m in rng.choice(cand, size=min(N_CONTROL, len(cand)), replace=False):
                    controls.append({"番号": row_id, "相手": pool_name, "超過リターン": forward_excess(d, m, a)})
    ev = pd.DataFrame(rows)
    ev["年"] = ev["日付"].dt.year
    return ev, pd.DataFrame(controls)


def verdict(sig, ctl):
    # 合格の判定（sig・ctlはstatsの結果）→ (合格か, 理由)
    up_ratio = sig["上"] / ctl["上"] if ctl["上"] else np.inf
    checks = [("平均の差が小さい", sig["平均"] - ctl["平均"] >= MIN_MEAN_GAP),
              ("+30%以上が1.5倍未満", up_ratio >= MIN_UP_RATIO),
              ("下側の増え方が上側以上", sig["下"] - ctl["下"] < sig["上"] - ctl["上"])]
    return [reason for reason, ok in checks if not ok]


def report(ev, ctrl, pool_name, title):
    print(f"\n{'=' * 10} {title}（比べる相手: {pool_name}） {'=' * 10}")
    rows = {}
    for k, g in ev.groupby("入る日"):
        c = ctrl[ctrl["番号"].isin(g.index) & (ctrl["相手"] == pool_name)]["超過リターン"]
        s, cs = stats(g["超過リターン"]), stats(c)
        print(f"\n■ {k}日目に入る\n  RS上位入り: {fmt(s)}\n  比べる相手: {fmt(cs)}")
        reasons = verdict(s, cs)
        by_year = {}
        for y, gy in g.groupby("年"):
            cy = ctrl[ctrl["番号"].isin(gy.index) & (ctrl["相手"] == pool_name)]["超過リターン"]
            sy, csy = stats(gy["超過リターン"]), stats(cy)
            print(f"  {y}: {fmt(sy)}\n        相手 {fmt(csy)}")
            by_year[y] = sy["平均"] > csy["平均"] and sy["上"] > csy["上"]
        if not all(by_year.values()):
            reasons.append("年で向きが違う")
        rows[f"{k}日目"] = {
            "平均の差": f"{(s['平均'] - cs['平均']) * 100:+.1f}pt",
            "+30%以上の倍率": f"{s['上'] / cs['上']:.2f}倍" if cs["上"] else "-",
            "-30%以下の差": f"{(s['下'] - cs['下']) * 100:+.1f}pt",
            "+30%以上の差": f"{(s['上'] - cs['上']) * 100:+.1f}pt",
            "中央値の差（参考）": f"{(s['中央値'] - cs['中央値']) * 100:+.1f}pt",
            "判定": "合格" if not reasons else "不合格（" + "・".join(reasons) + "）",
        }
    print(f"\n■ 合格の判定（{title}）")
    print(pd.DataFrame(rows).T.to_string())


def main():
    close_wide, _ = load_wide(with_history=True)
    tickers = [t for t in liquidity_passed_tickers(ROOT / "screening_result.csv") if t in close_wide.columns]
    close = close_wide[tickers]
    bench = close_wide[BENCHMARK_TICKER]
    n_obs = close.notna().cumsum()
    usable = close.notna() & (n_obs > EMA_WARMUP_DAYS)
    rank = relative_strength_wide(close_wide)[tickers].rank(axis=1, pct=True)
    top = (rank > 1 - RS_TOP_PCT) & usable
    recent = top.shift(1, fill_value=False).astype(float).rolling(FRESH_DAYS, min_periods=1).max() > 0
    first = top & ~recent & (close.index >= pd.Timestamp(SIGNAL_FROM))[:, None]
    po = perfect_order_wide(close) & usable
    a = {
        "dates": close.index,
        "tickers": tickers,
        "close": close.ffill().to_numpy(),  # 途中で終値が欠けた日は、直前の終値で見る
        "raw_close": close.to_numpy(),
        "bench": bench.ffill().to_numpy(),
    }
    pools = {"全銘柄": usable.to_numpy(), "PO成立中": po.to_numpy()}
    ev, ctrl = collect(a, first.to_numpy(), top.to_numpy(), po.to_numpy(), pools, np.random.default_rng(SEED))

    print(f"対象: 流動性フィルタ通過{len(tickers)}銘柄、{SIGNAL_FROM}以降にRS上位{RS_TOP_PCT:.0%}に初めて入った"
          f"{int(first.to_numpy().sum())}件（直前{FRESH_DAYS}営業日に上位に入っていなかったもの）、データの最終日 {close.index[-1]:%Y-%m-%d}")
    print(f"入る日の翌日の終値で買い、{HORIZON}営業日後までのTOPIX（1306.T）に対する超過リターン")

    report(ev, ctrl, "全銘柄", "全銘柄")
    report(ev[ev["PO"]], ctrl, "PO成立中", "入る日にPOが成立している銘柄")

    # 参考: 上位10%にいた日の割合（定着度）で3等分
    g = ev[ev["入る日"] == SHARE_DAY]
    groups = pd.qcut(g["上位の割合"], 3, labels=False, duplicates="drop")
    print(f"\n■ 参考: {SHARE_DAY}日目に入る場合を、初めて入った日からの間に上位{RS_TOP_PCT:.0%}にいた日の割合で3等分（比べる相手: 全銘柄）")
    for q, part in g.groupby(groups):
        c = ctrl[ctrl["番号"].isin(part.index) & (ctrl["相手"] == "全銘柄")]["超過リターン"]
        print(f"  {part['上位の割合'].min():.2f}〜{part['上位の割合'].max():.2f}: {fmt(stats(part['超過リターン']))}"
              f"\n  {'':10s}相手 {fmt(stats(c))}")


if __name__ == "__main__":
    main()
