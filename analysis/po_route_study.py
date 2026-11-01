"""PO新規の成り方（最後にどのEMAが抜けて成立したか）で、その後の期待値が違うかの検証

PO新規+RSプラス（Discord通知と同じ条件）を、成立日の前日の状態で分ける。
- GC型: 前日に25EMA > 75EMAで、最後に5EMAが25EMAを抜けて成立した（押し目からの再上昇に近い形）
- 25/75型: 前日に5EMA > 25EMAで、最後に25EMAが75EMAを抜けて成立した（じわじわ上がって転換する形）
- 同時: 前日はどちらも満たしていなかった（参考。判定はしない）
判定日（成立からk営業日目）の翌日の終値で買ったとして、60営業日後までのTOPIX超過リターンを比べる。

比べる相手: 1件ごとに、判定日にPOが成立している別の銘柄（新規でないもの）を無作為に5銘柄

合格の基準（GC型と25/75型の比較。結果を見る前に決めたもの。rs_entry_study.pyと同じ形）:
一方の型がもう一方の型と比べて、
1. 判定日20日で、平均で2ポイント以上高く、+30%以上の割合が1.5倍以上
2. -30%以下の割合の増え方が、+30%以上の割合の増え方より小さい
3. 2025年と2026年（判定日の年）のどちらでも、平均の差が同じ向きで+30%以上の割合が多い
4. 判定日0日・10日・40日でも、平均の差が同じ向き
すべて満たせば「差あり」。
参考: 型ごとに、20日間でRS上位10%にいた日の割合が60%以上になった銘柄の比率と、その成績

実行（プロジェクトフォルダで）: uv run python analysis/po_route_study.py
データは読むだけで書き換えない。

注意: 取引の期間は重なり、同じ相場の影響を受けるため、件数ほどの確からしさはない。
流動性フィルタは現時点の通過銘柄を過去にも当てはめている。データは上昇相場の期間しか含まない。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis.confirm_study import forward_excess  # noqa: E402
from analysis.rs_entry_study import fmt, stats  # noqa: E402
from analysis.signal_study import RS_TOP_PCT, load_wide  # noqa: E402
from filters import (  # noqa: E402
    BENCHMARK_TICKER,
    EMA_WARMUP_DAYS,
    GOLDEN_CROSS_LONG,
    GOLDEN_CROSS_SHORT,
    fresh_perfect_order_wide,
    liquidity_passed_tickers,
    perfect_order_wide,
    relative_strength_wide,
)

SIGNAL_FROM = "2025-01-01"  # この日以降のPO新規を対象にする（2024年10〜12月はEMAの助走期間）
JUDGE_DAYS = [20, 0, 10, 40]  # 判定日（成立から何営業日目か）。先頭が主な値、0は成立日の翌日に買う
HORIZON = 60  # 判定日の翌日から超過リターンを測る営業日数
WATCH_DAYS = 20  # 参考: RS上位にいた日の割合を数える日数
WATCH_MIN_SHARE = 0.6  # 参考: 監視型の条件（trade_study.pyの--watchと同じ）
N_CONTROL = 5  # 比べる相手: 1件につき選ぶ銘柄の数
SEED = 0  # 比べる相手を選ぶ乱数の種（毎回同じ結果にする）
MIN_MEAN_GAP = 0.02  # 合格の基準: 平均の差
MIN_UP_RATIO = 1.5  # 合格の基準: +30%以上の割合の倍率
ROUTES = ["GC型", "25/75型", "同時"]


def route_wide(close):
    # PO成立日の前日の状態から成り方を返す（index=date, columns=ticker の文字列。どれでもない日は空）
    short, mid, long = (close.ewm(span=s, adjust=False).mean() for s in (GOLDEN_CROSS_SHORT, GOLDEN_CROSS_LONG, 75))
    sm, ml = (short > mid).shift(1, fill_value=False), (mid > long).shift(1, fill_value=False)
    route = pd.DataFrame("", index=close.index, columns=close.columns)
    route[ml & ~sm] = ROUTES[0]
    route[sm & ~ml] = ROUTES[1]
    route[~sm & ~ml] = ROUTES[2]
    return route


def collect(k, a, signal, route, top, control_pool, rng):
    # 判定日kの1件ごとの成り方・超過リターンと、比べる相手 → (DataFrame, DataFrame)
    n = len(a["close"])
    rows, controls = [], []
    for i, j in zip(*np.nonzero(signal)):
        d = i + k
        if a["dates"][i] < pd.Timestamp(SIGNAL_FROM) or d + 1 + HORIZON > n - 1 or i + WATCH_DAYS > n - 1:
            continue
        row_id = len(rows)
        rows.append({"判定日": a["dates"][d], "ticker": a["tickers"][j], "成り方": route[i, j],
                     "RS上位の割合": top[i + 1:i + WATCH_DAYS + 1, j].mean(), "超過リターン": forward_excess(d, j, a)})
        pool = np.flatnonzero(control_pool[d])
        for m in rng.choice(pool, size=min(N_CONTROL, len(pool)), replace=False):
            controls.append({"番号": row_id, "超過リターン": forward_excess(d, m, a)})
    ev = pd.DataFrame(rows)
    ev["年"] = ev["判定日"].dt.year
    return ev, pd.DataFrame(controls)


def control_of(ctrl, index):
    return ctrl.loc[ctrl["番号"].isin(index), "超過リターン"]


def route_table(ev, ctrl):
    rows = {}
    for r in ROUTES:
        g = ev[ev["成り方"] == r]
        rows[r] = {"件数": len(g), "成り方の銘柄": fmt(stats(g["超過リターン"])), "比べる相手": fmt(stats(control_of(ctrl, g.index)))}
    rows["全体"] = {"件数": len(ev), "成り方の銘柄": fmt(stats(ev["超過リターン"])), "比べる相手": fmt(stats(control_of(ctrl, ev.index)))}
    return pd.DataFrame(rows).T


def gap(ev):
    # GC型 − 25/75型 の平均の差・+30%以上の倍率など
    a, b = (stats(ev.loc[ev["成り方"] == r, "超過リターン"]) for r in ROUTES[:2])
    return {"平均の差": a["平均"] - b["平均"], "上の倍率": a["上"] / b["上"] if b["上"] else np.nan,
            "上の差": a["上"] - b["上"], "下の差": a["下"] - b["下"]}


def judge(data):
    # 合格の判定（GC型と25/75型のどちらかが上回るか）→ (表, 判定の文字列)
    main_k = JUDGE_DAYS[0]
    ev, _ = data[main_k]
    g = gap(ev)
    sign = 1 if g["平均の差"] >= 0 else -1  # 上回った側から見る
    better, worse = (ROUTES[0], ROUTES[1]) if sign > 0 else (ROUTES[1], ROUTES[0])
    ratio = g["上の倍率"] if sign > 0 else 1 / g["上の倍率"]
    reasons = []
    if abs(g["平均の差"]) < MIN_MEAN_GAP:
        reasons.append("平均の差が小さい")
    if not ratio >= MIN_UP_RATIO:
        reasons.append("+30%以上が1.5倍未満")
    if sign * g["下の差"] >= sign * g["上の差"]:
        reasons.append("下側の増え方が上側以上")
    rows = {f"判定日{main_k}日": g}
    for y in sorted(ev["年"].unique()):
        gy = gap(ev[ev["年"] == y])
        rows[f"判定日{main_k}日 {y}年"] = gy
        if not (sign * gy["平均の差"] > 0 and sign * gy["上の差"] > 0):
            reasons.append(f"{y}年は向きが違う")
    for k in JUDGE_DAYS[1:]:
        gk = gap(data[k][0])
        rows[f"判定日{k}日"] = gk
        if sign * gk["平均の差"] <= 0:
            reasons.append(f"{k}日は向きが違う")
    table = pd.DataFrame(rows).T
    table["平均の差"] = table["平均の差"].map(lambda v: f"{v * 100:+.1f}pt")
    table["上の倍率"] = table["上の倍率"].map(lambda v: f"{v:.2f}倍")
    for c in ["上の差", "下の差"]:
        table[c] = table[c].map(lambda v: f"{v * 100:+.1f}pt")
    table.columns = [f"平均の差（{ROUTES[0]}−{ROUTES[1]}）", "+30%以上の倍率", "+30%以上の差", "-30%以下の差"]
    verdict = f"差あり（{better}が{worse}を上回る）" if not reasons else "差なし（" + "・".join(reasons) + "）"
    return table, verdict


def watch_table(ev, ctrl):
    # 参考: 型ごとの、RS上位に60%以上いた銘柄の比率と成績（判定日20日）
    rows = {}
    for r in ROUTES:
        g = ev[ev["成り方"] == r]
        w = g[g["RS上位の割合"] >= WATCH_MIN_SHARE]
        rows[r] = {"件数": len(g), f"{WATCH_MIN_SHARE:.0%}以上の比率": f"{len(w) / len(g):.1%}" if len(g) else "-",
                   f"{WATCH_MIN_SHARE:.0%}以上の銘柄": fmt(stats(w["超過リターン"])),
                   "比べる相手": fmt(stats(control_of(ctrl, w.index)))}
    return pd.DataFrame(rows).T


def main():
    close_wide, _ = load_wide(with_history=True)
    tickers = [t for t in liquidity_passed_tickers(ROOT / "screening_result.csv") if t in close_wide.columns]
    close = close_wide[tickers]
    bench = close_wide[BENCHMARK_TICKER]
    filled = close.ffill()  # 途中で終値が欠けた日は、直前の終値で見る
    rs = relative_strength_wide(close_wide)[tickers]
    fresh = fresh_perfect_order_wide(close)
    po = perfect_order_wide(close)
    n_obs = close.notna().cumsum()
    route = route_wide(close).to_numpy()
    top = (rs.rank(axis=1, pct=True) > 1 - RS_TOP_PCT).to_numpy()
    a = {"dates": close.index, "tickers": tickers, "close": filled.to_numpy(), "bench": bench.ffill().to_numpy()}
    control_pool = (po & ~fresh & close.notna() & (n_obs > EMA_WARMUP_DAYS)).to_numpy()
    signal = (fresh & (rs > 0)).to_numpy()
    data = {k: collect(k, a, signal, route, top, control_pool, np.random.default_rng(SEED)) for k in JUDGE_DAYS}

    print(f"対象: 流動性フィルタ通過{len(tickers)}銘柄、{SIGNAL_FROM}以降のPO新規+RSプラス、データの最終日 {close.index[-1]:%Y-%m-%d}")
    print(f"判定日の翌日の終値で買い、{HORIZON}営業日後までのTOPIX（1306.T）に対する超過リターン。比べる相手は判定日にPOが成立している銘柄")
    for k in JUDGE_DAYS:
        ev, ctrl = data[k]
        print(f"\n■ 判定日{k}日（成り方ごと）")
        print(route_table(ev, ctrl).to_string())

    table, verdict = judge(data)
    print(f"\n■ 合格の判定（{ROUTES[0]}と{ROUTES[1]}の比較） → {verdict}")
    print(table.to_string())

    ev, ctrl = data[JUDGE_DAYS[0]]
    print(f"\n■ 参考: {WATCH_DAYS}日間でRS上位{RS_TOP_PCT:.0%}にいた日の割合が{WATCH_MIN_SHARE:.0%}以上の銘柄（判定日{JUDGE_DAYS[0]}日）")
    print(watch_table(ev, ctrl).to_string())


if __name__ == "__main__":
    main()
