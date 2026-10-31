"""PO新規の後の値動きで、その後も上がる銘柄を見分けられるかの検証

PO新規の成立からk営業日目（判定日）までの値動きの特徴で銘柄を分け、判定日の翌日から60営業日の超過リターンを比べる。
成立日から測ると判定日までの上昇が結果に混ざるため、判定日の翌日の終値で買ったとして測る。
しきい値を決めずに済むよう、数値の特徴は値の大きさで5等分し、グループの並び方を見る。
判定日は20日を主とし、10日・40日でも向きが変わらないかを確かめる。

比べる相手: シグナル1件ごとに、判定日にPOが成立している別の銘柄（新規でないもの）を無作為に選び、同じ日から測る

合格の基準（特徴ごと。結果を見る前に決めたもの）:
1. 判定日20日で、両端のグループの60日超過の中央値の差が2ポイント以上・並びがおおむね単調（順位相関の絶対値0.9以上）・
   良い側の端のグループが、同じ日の比べる相手を上回る
2. 2025年と2026年（判定日の年）で、両端の差の向きが同じ
3. 判定日10日・40日でも、両端の差の向きが同じ
「はい／いいえ」の特徴（A・C）は2群で、単調さを除いて同じ基準で見る

実行（プロジェクトフォルダで）: uv run python analysis/confirm_study.py
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

from analysis.signal_study import EMA200_MIN_DAYS, load_wide  # noqa: E402
from filters import (  # noqa: E402
    BENCHMARK_TICKER,
    EMA_WARMUP_DAYS,
    fresh_perfect_order_wide,
    liquidity_passed_tickers,
    perfect_order_wide,
    relative_strength_wide,
)

SIGNAL_FROM = "2025-01-01"  # この日以降のPO新規を対象にする（2024年10〜12月はEMAの助走期間）
JUDGE_DAYS = [20, 10, 40]  # 判定日（成立から何営業日目に判断するか）。先頭が主な値
HORIZON = 60  # 判定日の翌日から超過リターンを測る営業日数（signal_study.pyと同じ物差し）
BIG_WIN = 0.30  # 大きく上がったとみなす超過リターン（winner_study.pyと同じ基準）
N_CONTROL = 5  # 比べる相手: シグナル1件につき選ぶ銘柄の数
SEED = 0  # 比べる相手を選ぶ乱数の種（毎回同じ結果にする）
HIGH_WINDOW, HIGH_MIN_DAYS = 250, 200  # 52週高値（250営業日。データが200日以上あれば計算する）
MIN_GAP = 0.02  # 合格の基準: 両端のグループの中央値の差
MIN_MONOTONIC = 0.9  # 合格の基準: グループの番号と中央値の順位相関の絶対値
N_GROUPS = 5

FEATURES = {  # 特徴名: 「はい／いいえ」の特徴か
    "A: POが一度も崩れていない": True,
    "B: RSの順位の最高値（1が最強）": False,
    "C: 52週高値を更新した": True,
    "D: 最大下落（成立日以降の高値から）": False,
    "E: 上げた日の出来高÷下げた日の出来高": False,
    "F: TOPIX比の線の75EMAからの距離": False,
}


def event_features(i, d, j, a):
    # 成立日i〜判定日dの値動きから、判定日の時点で分かる特徴を計算する
    c = a["close"][i:d + 1, j]
    ret = c[1:] / c[:-1] - 1
    v = a["volume"][i + 1:d + 1, j]
    down_volume = np.nansum(v[ret < 0])
    high52 = a["high52"][d, j]
    return {
        "A: POが一度も崩れていない": float(a["po"][i:d + 1, j].all()),
        "B: RSの順位の最高値（1が最強）": np.nanmax(a["rs_rank"][i + 1:d + 1, j]),
        "C: 52週高値を更新した": float(c.max() >= high52) if not np.isnan(high52) else np.nan,
        "D: 最大下落（成立日以降の高値から）": (c / np.maximum.accumulate(c) - 1).min(),
        "E: 上げた日の出来高÷下げた日の出来高": np.nansum(v[ret > 0]) / down_volume if down_volume > 0 else np.nan,
        "F: TOPIX比の線の75EMAからの距離": a["rel_gap"][d, j],
    }


def forward_excess(d, j, a):
    # 判定日dの翌日の終値で買い、HORIZON営業日後の終値までの超過リターン（データの終わりを越える場合はNaN）
    buy, sell = d + 1, d + 1 + HORIZON
    if sell > len(a["close"]) - 1:
        return np.nan
    c, b = a["close"][:, j], a["bench"]
    return (c[sell] / c[buy] - 1) - (b[sell] / b[buy] - 1)


def collect(k, a, fresh, control_pool, rng):
    # 判定日kのシグナルと比べる相手 → (シグナルのDataFrame, 比べる相手のDataFrame)。比べる相手はシグナルの番号で対応づける
    events, controls = [], []
    for i, j in zip(*np.nonzero(fresh)):
        d = i + k
        if a["dates"][i] < pd.Timestamp(SIGNAL_FROM) or d + 1 + HORIZON > len(a["close"]) - 1:
            continue
        n = len(events)
        events.append({"判定日": a["dates"][d], "ticker": a["tickers"][j], "200EMA安定": a["stable"][d, j],
                       "超過リターン": forward_excess(d, j, a)} | event_features(i, d, j, a))
        pool = np.flatnonzero(control_pool[d])
        for m in rng.choice(pool, size=min(N_CONTROL, len(pool)), replace=False):
            controls.append({"シグナルの番号": n, "超過リターン": forward_excess(d, m, a)})
    ev = pd.DataFrame(events)
    ev["年"] = ev["判定日"].dt.year
    return ev, pd.DataFrame(controls)


def groups_of(ev, name):
    # 特徴の値で分けたグループの番号（0から。「はい／いいえ」の特徴は0=いいえ、1=はい）
    if FEATURES[name]:
        return ev[name]
    return pd.qcut(ev[name], N_GROUPS, labels=False, duplicates="drop")


def fmt(r):
    r = r.dropna()
    return f"{r.median() * 100:+.1f}%／{(r > 0).mean() * 100:.0f}%／{(r >= BIG_WIN).mean() * 100:.1f}%" if len(r) else "-"


def group_table(ev, ctrl, name):
    # グループごとの成績と、同じ日の比べる相手の成績
    groups = groups_of(ev, name)
    years = sorted(ev["年"].unique().tolist())
    rows = {}
    for q, g in ev.groupby(groups):
        c = ctrl.loc[ctrl["シグナルの番号"].isin(g.index), "超過リターン"]
        label = ("はい" if q else "いいえ") if FEATURES[name] else f"{int(q) + 1}{'（小）' if q == 0 else '（大）' if q == groups.max() else ''}"
        row = {} if FEATURES[name] else {"範囲": f"{g[name].min():.3g}〜{g[name].max():.3g}"}
        row |= {"件数": len(g), "シグナル": fmt(g["超過リターン"]), "比べる相手": fmt(c),
                "平均": f"{g['超過リターン'].mean() * 100:+.1f}%"}
        row |= {f"中央値 {y}": f"{g.loc[g['年'] == y, '超過リターン'].median() * 100:+.1f}%" for y in years}
        rows[label] = row
    return pd.DataFrame(rows).T


def judge(ev, ctrl, name):
    # 判定日1つ分の判定の材料 → {両端の差, 単調さ, 良い側の端が比べる相手を上回ったか, 年ごとの両端の差}
    groups = groups_of(ev, name)
    med = ev.groupby(groups)["超過リターン"].median()
    if len(med) < 2:
        return None
    gap = med.iloc[-1] - med.iloc[0]
    best = med.index[-1] if gap > 0 else med.index[0]
    best_ids = ev.index[groups == best]
    ctrl_med = ctrl.loc[ctrl["シグナルの番号"].isin(best_ids), "超過リターン"].median()
    by_year = {}
    for y, part in ev.groupby("年"):
        m = part.groupby(groups[part.index])["超過リターン"].median()
        by_year[y] = m.get(med.index[-1], np.nan) - m.get(med.index[0], np.nan)
    # 順位相関（順位に直してから相関をとる。scipyを入れずに済ませる）
    rho = pd.Series(range(len(med))).corr(pd.Series(med.to_numpy()).rank()) if len(med) > 2 else np.nan
    return {"gap": gap, "rho": rho, "beat": med[best] > ctrl_med, "best_vs_ctrl": med[best] - ctrl_med, "by_year": by_year}


def verdict_table(results):
    # results: {判定日k: {特徴名: judgeの結果}} → 特徴ごとの合格判定の表
    main_k = JUDGE_DAYS[0]
    rows = {}
    for name in FEATURES:
        r = results[main_k][name]
        if r is None:
            continue
        sign = np.sign(r["gap"])
        ok_gap = abs(r["gap"]) >= MIN_GAP
        ok_mono = FEATURES[name] or abs(r["rho"]) >= MIN_MONOTONIC - 1e-9  # 0.9ちょうどが計算誤差で下回らないように
        ok_years = all(np.sign(v) == sign for v in r["by_year"].values())
        ok_days = all(results[k][name] is not None and np.sign(results[k][name]["gap"]) == sign for k in JUDGE_DAYS[1:])
        row = {
            f"両端の差（{main_k}日）": f"{r['gap'] * 100:+.1f}pt",
            "順位相関": "-" if FEATURES[name] else f"{r['rho']:+.2f}",
            "良い端−比べる相手": f"{r['best_vs_ctrl'] * 100:+.1f}pt",
        }
        row |= {f"両端の差 {y}": f"{v * 100:+.1f}pt" for y, v in r["by_year"].items()}
        row |= {f"両端の差（{k}日）": f"{results[k][name]['gap'] * 100:+.1f}pt" for k in JUDGE_DAYS[1:] if results[k][name] is not None}
        row["判定"] = "合格" if ok_gap and ok_mono and r["beat"] and ok_years and ok_days else "不合格（" + "・".join(
            reason for reason, ok in [("差が小さい", ok_gap), ("単調でない", ok_mono), ("比べる相手に負ける", r["beat"]),
                                      ("年で向きが違う", ok_years), ("判定日で向きが違う", ok_days)] if not ok) + "）"
        rows[name] = row
    return pd.DataFrame(rows).T


def main():
    close_wide, volume_wide = load_wide(with_history=True)
    tickers = [t for t in liquidity_passed_tickers(ROOT / "screening_result.csv") if t in close_wide.columns]
    close = close_wide[tickers]
    bench = close_wide[BENCHMARK_TICKER]
    fresh = fresh_perfect_order_wide(close)
    po = perfect_order_wide(close)
    n_obs = close.notna().cumsum()
    filled = close.ffill()  # 途中で終値が欠けた日は、直前の終値で見る
    rel = close.div(bench, axis=0)  # TOPIX比の線
    a = {
        "dates": close.index,
        "tickers": tickers,
        "close": filled.to_numpy(),
        "bench": bench.ffill().to_numpy(),
        "volume": volume_wide[tickers].to_numpy(dtype=float),
        "po": po.to_numpy(),
        "rs_rank": relative_strength_wide(close_wide)[tickers].rank(axis=1, pct=True).to_numpy(),
        "high52": filled.rolling(HIGH_WINDOW, min_periods=HIGH_MIN_DAYS).max().to_numpy(),
        "rel_gap": (rel / rel.ewm(span=75, adjust=False).mean() - 1).to_numpy(),
        "stable": (n_obs >= EMA200_MIN_DAYS).to_numpy(),
    }
    # 比べる相手の候補: POが成立中（新規でない）・助走期間を過ぎ・当日の終値がある銘柄
    control_pool = (po & ~fresh & close.notna() & (n_obs > EMA_WARMUP_DAYS)).to_numpy()

    data = {k: collect(k, a, fresh.to_numpy(), control_pool, np.random.default_rng(SEED)) for k in JUDGE_DAYS}

    print(f"対象: 流動性フィルタ通過{len(tickers)}銘柄、{SIGNAL_FROM}以降のPO新規、データの最終日 {close.index[-1]:%Y-%m-%d}")
    print(f"判定日の翌日の終値で買い、{HORIZON}営業日後までのTOPIX（1306.T）に対する超過リターン")
    print(f"表の値は「中央値／勝率／+{BIG_WIN:.0%}以上の割合」（比べる相手は同じ日にPOが成立中の銘柄）")

    for title, select in [("全期間", None), (f"200EMAが安定してから（判定日がデータ開始から{EMA200_MIN_DAYS}営業日以降）", "200EMA安定")]:
        print(f"\n{'=' * 10} {title} {'=' * 10}")
        results = {}
        for k in JUDGE_DAYS:
            ev, ctrl = data[k]
            if select:
                ev = ev[ev[select]]
                ctrl = ctrl[ctrl["シグナルの番号"].isin(ev.index)]
            results[k] = {name: judge(ev, ctrl, name) for name in FEATURES}
            if k != JUDGE_DAYS[0]:
                continue
            print(f"\n■ 判定日{k}日: 全体 {len(ev)}件 {fmt(ev['超過リターン'])}／比べる相手 {fmt(ctrl['超過リターン'])}"
                  f"（判定日 {ev['判定日'].min():%Y-%m-%d}〜{ev['判定日'].max():%Y-%m-%d}）")
            for name in FEATURES:
                print(f"\n□ {name}")
                print(group_table(ev, ctrl, name).to_string())
        print(f"\n■ 合格の判定（両端の差＝「はい」または最大のグループ − 「いいえ」または最小のグループ、{HORIZON}日超過の中央値）")
        print(verdict_table(results).to_string())


if __name__ == "__main__":
    main()
