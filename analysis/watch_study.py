"""PO新規を監視の開始としたときに、強さが続いているかで期待値を見分けられるかの検証

PO新規+RSプラス（Discord通知と同じ条件）を監視の開始とし、k営業日目（判定日）までに次の状態だった日の割合で銘柄を分け、
判定日の翌日の終値で買ったとして60営業日後までのTOPIX超過リターンを比べる（TradingViewのチャートで見ている指標の再現）。
1. RSが0より上で、RSの10日移動平均より上（RSは21営業日の騰落率のTOPIXとの差）
2. 日足のMACD（12・26・9）がシグナルより上
3. 週足のMACD（12・26・9）がシグナルより上（週の途中は当日の終値を週の終値とみなす。先の値は使わない）
4. （比べるため）RS上位10%にいた
割合は監視の開始の翌日から判定日までで数える。割合で5等分する（同じ値が多い場合はグループが減る）。

比べる相手: 1件ごとに、判定日にPOが成立している別の銘柄（新規でないもの）を無作為に5銘柄

合格の基準（指標ごと。結果を見る前に決めたもの。rs_entry_study.pyと同じ）:
1. 割合が最も高いグループが、比べる相手より平均で2ポイント以上高く、+30%以上の割合が1.5倍以上
2. -30%以下の割合の増え方が、+30%以上の割合の増え方より小さい
3. 2025年と2026年（判定日の年）のどちらでも、平均の差がプラスで+30%以上の割合が多い
4. 判定日10日・40日でも、平均の差がプラス
RS上位の定着度への上乗せ: RS上位にいた日の割合で3等分し、それぞれの中で指標の割合が高い半分と低い半分を比べる。
3つすべてで高い半分が平均・+30%以上の割合とも上回れば「上乗せあり」

実行（プロジェクトフォルダで）: uv run python analysis/watch_study.py
データは読むだけで書き換えない。

注意: どの指標も値動きの勢いから計算するもので、互いに強く重なる。取引の期間は重なり、同じ相場の影響を受けるため、件数ほどの確からしさはない。
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
    fresh_perfect_order_wide,
    liquidity_passed_tickers,
    perfect_order_wide,
    relative_strength_wide,
)

SIGNAL_FROM = "2025-01-01"  # この日以降の監視の開始を対象にする（2024年10〜12月はEMAの助走期間）
JUDGE_DAYS = [20, 10, 40]  # 判定日（監視の開始から何営業日目か）。先頭が主な値
HORIZON = 60  # 判定日の翌日から超過リターンを測る営業日数
RS_MA = 10  # RSの移動平均の日数（TradingViewの設定と同じ）
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9  # MACDの標準設定
N_GROUPS = 5
N_CONTROL = 5  # 比べる相手: 1件につき選ぶ銘柄の数
SEED = 0  # 比べる相手を選ぶ乱数の種（毎回同じ結果にする）
MIN_MEAN_GAP = 0.02  # 合格の基準: 平均の差
MIN_UP_RATIO = 1.5  # 合格の基準: +30%以上の割合の倍率
BASE = "RS上位10%にいた"  # 上乗せを確かめる相手の指標
INDICATORS = ["RSが0より上かつ10日平均より上", "日足MACDがシグナルより上", "週足MACDがシグナルより上", BASE]


def macd_above(close):
    # 日足のMACDがシグナルより上か（index=date, columns=ticker）
    macd = close.ewm(span=MACD_FAST, adjust=False).mean() - close.ewm(span=MACD_SLOW, adjust=False).mean()
    return macd > macd.ewm(span=MACD_SIGNAL, adjust=False).mean()


def weekly_macd_above(close):
    # その日の時点の週足のMACDがシグナルより上か。週の途中は当日の終値を週の終値とみなす（先の値を使わない）
    week = close.index.to_period("W-FRI")
    weekly = close.groupby(week).last()

    def prev_week(wide):  # 前の週（確定した週）の値を、各日に配る
        return wide.shift(1).reindex(week).set_axis(close.index)

    fast, slow = (weekly.ewm(span=s, adjust=False).mean() for s in (MACD_FAST, MACD_SLOW))
    signal = (fast - slow).ewm(span=MACD_SIGNAL, adjust=False).mean()
    a_fast, a_slow, a_sig = (2 / (s + 1) for s in (MACD_FAST, MACD_SLOW, MACD_SIGNAL))
    macd_now = (a_fast * close + (1 - a_fast) * prev_week(fast)) - (a_slow * close + (1 - a_slow) * prev_week(slow))
    signal_now = a_sig * macd_now + (1 - a_sig) * prev_week(signal)
    return macd_now > signal_now


def collect(k, a, signal, flags, control_pool, rng):
    # 判定日kの1件ごとの指標の割合・超過リターンと、比べる相手 → (DataFrame, DataFrame)
    n = len(a["close"])
    rows, controls = [], []
    for i, j in zip(*np.nonzero(signal)):
        d = i + k
        if a["dates"][i] < pd.Timestamp(SIGNAL_FROM) or d + 1 + HORIZON > n - 1:
            continue
        row_id = len(rows)
        rows.append({"判定日": a["dates"][d], "ticker": a["tickers"][j], "超過リターン": forward_excess(d, j, a)}
                    | {name: f[i + 1:d + 1, j].mean() for name, f in flags.items()})
        pool = np.flatnonzero(control_pool[d])
        for m in rng.choice(pool, size=min(N_CONTROL, len(pool)), replace=False):
            controls.append({"番号": row_id, "超過リターン": forward_excess(d, m, a)})
    ev = pd.DataFrame(rows)
    ev["年"] = ev["判定日"].dt.year
    return ev, pd.DataFrame(controls)


def control_of(ctrl, index):
    return ctrl.loc[ctrl["番号"].isin(index), "超過リターン"]


def best_group(ev, name):
    # 割合が最も高いグループの1件の番号
    groups = pd.qcut(ev[name], N_GROUPS, labels=False, duplicates="drop")
    return ev.index[groups == groups.max()]


def group_table(ev, ctrl, name):
    groups = pd.qcut(ev[name], N_GROUPS, labels=False, duplicates="drop")
    rows = {}
    for q, g in ev.groupby(groups):
        rows[f"{int(q) + 1}（{g[name].min():.2f}〜{g[name].max():.2f}）"] = {
            "監視中の銘柄": fmt(stats(g["超過リターン"])), "比べる相手": fmt(stats(control_of(ctrl, g.index)))}
    return pd.DataFrame(rows).T


def judge(data, name):
    # 合格の判定 → (表の1行, 理由のリスト)
    main_k = JUDGE_DAYS[0]
    ev, ctrl = data[main_k]
    best = best_group(ev, name)
    s, c = stats(ev.loc[best, "超過リターン"]), stats(control_of(ctrl, best))
    reasons = []
    if s["平均"] - c["平均"] < MIN_MEAN_GAP:
        reasons.append("平均の差が小さい")
    if not c["上"] or s["上"] / c["上"] < MIN_UP_RATIO:
        reasons.append("+30%以上が1.5倍未満")
    if s["下"] - c["下"] >= s["上"] - c["上"]:
        reasons.append("下側の増え方が上側以上")
    years = {}
    for y in sorted(ev["年"].unique()):
        idx = best[ev.loc[best, "年"] == y]
        sy, cy = stats(ev.loc[idx, "超過リターン"]), stats(control_of(ctrl, idx))
        years[y] = sy["平均"] - cy["平均"]
        if not (sy["平均"] > cy["平均"] and sy["上"] > cy["上"]):
            reasons.append(f"{y}年は向きが違う")
    other = {}
    for k in JUDGE_DAYS[1:]:
        ev_k, ctrl_k = data[k]
        b = best_group(ev_k, name)
        other[k] = stats(ev_k.loc[b, "超過リターン"])["平均"] - stats(control_of(ctrl_k, b))["平均"]
        if other[k] <= 0:
            reasons.append(f"{k}日は向きが違う")
    row = {"件数": s["件数"], f"平均の差（{main_k}日）": f"{(s['平均'] - c['平均']) * 100:+.1f}pt",
           "+30%以上の倍率": f"{s['上'] / c['上']:.2f}倍" if c["上"] else "-",
           "-30%以下の差": f"{(s['下'] - c['下']) * 100:+.1f}pt", "+30%以上の差": f"{(s['上'] - c['上']) * 100:+.1f}pt"}
    row |= {f"平均の差 {y}": f"{v * 100:+.1f}pt" for y, v in years.items()}
    row |= {f"平均の差（{k}日）": f"{v * 100:+.1f}pt" for k, v in other.items()}
    row["判定"] = "合格" if not reasons else "不合格（" + "・".join(reasons) + "）"
    return row


def added_value(ev, name):
    # RS上位にいた日の割合で3等分し、それぞれの中で指標の割合の高い半分と低い半分を比べる → (表, 上乗せありか)
    tiers = pd.qcut(ev[BASE], 3, labels=False, duplicates="drop")
    rows, ok = {}, True
    for t, g in ev.groupby(tiers):
        high = g[name] > g[name].median()
        if not high.any() or high.all():
            rows[f"RS上位の割合 {g[BASE].min():.2f}〜{g[BASE].max():.2f}"] = {"高い半分": "-", "低い半分": "-"}
            ok = False
            continue
        sh, sl = stats(g.loc[high, "超過リターン"]), stats(g.loc[~high, "超過リターン"])
        rows[f"RS上位の割合 {g[BASE].min():.2f}〜{g[BASE].max():.2f}"] = {"高い半分": fmt(sh), "低い半分": fmt(sl)}
        ok &= sh["平均"] > sl["平均"] and sh["上"] > sl["上"]
    return pd.DataFrame(rows).T, ok


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
    flags = {
        INDICATORS[0]: (rs > 0) & (rs > rs.rolling(RS_MA).mean()),
        INDICATORS[1]: macd_above(filled),
        INDICATORS[2]: weekly_macd_above(filled),
        BASE: rs.rank(axis=1, pct=True) > 1 - RS_TOP_PCT,
    }
    flags = {name: f.to_numpy() for name, f in flags.items()}
    a = {"dates": close.index, "tickers": tickers, "close": filled.to_numpy(), "bench": bench.ffill().to_numpy()}
    control_pool = (po & ~fresh & close.notna() & (n_obs > EMA_WARMUP_DAYS)).to_numpy()
    signal = (fresh & (rs > 0)).to_numpy()
    data = {k: collect(k, a, signal, flags, control_pool, np.random.default_rng(SEED)) for k in JUDGE_DAYS}

    ev, ctrl = data[JUDGE_DAYS[0]]
    print(f"対象: 流動性フィルタ通過{len(tickers)}銘柄、{SIGNAL_FROM}以降のPO新規+RSプラス（監視の開始）、データの最終日 {close.index[-1]:%Y-%m-%d}")
    print(f"判定日の翌日の終値で買い、{HORIZON}営業日後までのTOPIX（1306.T）に対する超過リターン。比べる相手は判定日にPOが成立している銘柄")
    print(f"\n■ 判定日{JUDGE_DAYS[0]}日: 全体 {fmt(stats(ev['超過リターン']))}\n  {'比べる相手':8s} {fmt(stats(ctrl['超過リターン']))}")
    for name in INDICATORS:
        print(f"\n□ {name}（監視の開始の翌日から判定日までの日の割合で5等分）")
        print(group_table(ev, ctrl, name).to_string())

    print(f"\n■ 合格の判定（割合が最も高いグループ − 比べる相手）")
    print(pd.DataFrame({name: judge(data, name) for name in INDICATORS}).T.to_string())

    for name in INDICATORS[:-1]:
        table, ok = added_value(ev, name)
        print(f"\n■ RS上位の定着度への上乗せ: {name}（判定日{JUDGE_DAYS[0]}日） → {'上乗せあり' if ok else '上乗せなし'}")
        print(table.to_string())


if __name__ == "__main__":
    main()
