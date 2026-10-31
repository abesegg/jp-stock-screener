"""勝ち銘柄の研究

実際に大きく上がった銘柄（勝ち銘柄）を起点に、スクリーニング（PO新規+RSプラス）がそれを拾えていたか、
いつ拾えたか、拾えなかった銘柄はどんな状態だったかを集計する（signal_study.pyとは逆向きの検証）。

勝ち銘柄: 各月の最初の営業日を起点に、60営業日後のTOPIXに対する超過リターンが+30%以上（+50%以上は大化け）。
同じ銘柄が続く起点で勝ち銘柄になった場合は、一連の上昇（エピソード）として1件にまとめる。

実行（プロジェクトフォルダで）: uv run python analysis/winner_study.py
--exportを付けると、エピソード1件ごとの一覧をanalysis/winner_episodes.csv（git管理外）に書き出す。
データは読むだけで書き換えない。

注意: 勝ち銘柄は結果を見てから選ぶため、この集計自体はシグナルにならない。見逃しや死角を見つける診断として使い、
見つかった条件はsignal_study.py（シグナルから見る向き）で改めて確かめる。
流動性フィルタは現時点の通過銘柄を過去にも当てはめている。
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis.signal_study import forward_excess_return, load_wide  # noqa: E402
from filters import (  # noqa: E402
    BENCHMARK_TICKER,
    EMA_WARMUP_DAYS,
    fresh_perfect_order_wide,
    liquidity_passed_tickers,
    perfect_order_wide,
    relative_strength_wide,
)

HORIZON = 60  # 勝ち銘柄を判定する営業日数
WIN_THRESHOLD = 0.30  # 勝ち銘柄: 60営業日後の超過リターンがこれ以上
BIG_THRESHOLD = 0.50  # 大化け: 60営業日後の超過リターンがこれ以上
JUDGE_FROM = "2025-01-01"  # 2024年10〜12月はEMAの助走期間のため、起点はこれ以降
LOW_LOOKBACK = 20  # 安値日を探す範囲: 最初の起点のこの営業日数前から
CAPTURE_BEFORE = 20  # 安値日のこの営業日数前から
CAPTURE_AFTER = 40  # 安値日のこの営業日数後までにシグナルが出ていれば「捕捉」とみなす
PO_RECENT_DAYS = 20  # 安値日までのこの営業日数にPOが成立していれば「POが続いていた」とみなす
GAP_UP = 0.10  # 安値日から高値日までに、1日でこれ以上上がった日があれば「急騰型」とみなす
RS_TOP_PCT = 0.10  # RS上位の基準（rs_ranking.csvと同じ上位10%）
EXPORT_FILE = ROOT / "analysis" / "winner_episodes.csv"  # --exportで書き出すエピソードの一覧
MISSED_TYPES = ["データ不足", "POが続いていた（押し目からの再加速）", "200EMAより下から反発", "急騰型", "その他"]
MISSED_REASONS = ["PO新規は出たがRSがマイナス", "判定の幅より後にシグナル", "PO成立中で新規にならず", "POにならなかった"]


def find_episodes(excess, starts):
    # 起点ごとの勝ち銘柄を、同じ銘柄の続く起点（前の起点から60営業日以内）でまとめて1件のエピソードにする
    positions = {d: excess.index.get_loc(d) for d in starts}
    episodes, latest = [], {}
    for d in starts:
        row = excess.loc[d]
        for ticker, r in row[row >= WIN_THRESHOLD].items():
            ep = latest.get(ticker)
            if ep and positions[d] - ep["last_pos"] <= HORIZON:
                ep["last_pos"] = positions[d]
                ep["max_excess"] = max(ep["max_excess"], r)
                ep["n_starts"] += 1
            else:
                ep = {"ticker": ticker, "first_pos": positions[d], "last_pos": positions[d], "max_excess": r, "n_starts": 1}
                latest[ticker] = ep
                episodes.append(ep)
    return episodes


def first_true(flags, lo, hi):
    # flags[lo:hi+1]で最初にTrueになる位置（なければNone）
    hits = np.flatnonzero(flags[lo:hi + 1])
    return lo + hits[0] if len(hits) else None


def analyze_episode(ep, data):
    t = ep["ticker"]
    close, signal, rs_top, po = (data[k][t].to_numpy() for k in ["close", "signal", "rs_top", "po"])
    ema200, high52, rs_rank = (data[k][t].to_numpy() for k in ["ema200", "high52", "rs_rank"])
    fresh = data["fresh"][t].to_numpy()
    n = len(close)

    # 安値日: 最初の起点のLOW_LOOKBACK営業日前から高値日までで終値が最も安い日／高値日: エピソードの期間内で最も高い日
    end = min(ep["last_pos"] + HORIZON, n - 1)
    high_pos = ep["first_pos"] + int(np.nanargmax(close[ep["first_pos"]:end + 1]))
    lo_start = max(ep["first_pos"] - LOW_LOOKBACK, 0)
    low_pos = lo_start + int(np.nanargmin(close[lo_start:high_pos + 1]))
    low, high = close[low_pos], close[high_pos]

    cap_lo, cap_hi = max(low_pos - CAPTURE_BEFORE, 0), min(low_pos + CAPTURE_AFTER, n - 1)
    sig_pos = first_true(signal, cap_lo, cap_hi)
    rs_pos = first_true(rs_top, cap_lo, cap_hi)
    daily = close[low_pos + 1:high_pos + 1] / close[low_pos:high_pos] - 1

    row = {
        "ticker": t,
        "大化け": ep["max_excess"] >= BIG_THRESHOLD,
        "最初の起点": data["dates"][ep["first_pos"]],
        "起点の数": ep["n_starts"],
        "最大の60日超過リターン(%)": round(ep["max_excess"] * 100, 1),
        "安値日": data["dates"][low_pos],
        "高値日": data["dates"][high_pos],
        "安値から高値の上昇率(%)": round((high / low - 1) * 100, 1),
        "捕捉": sig_pos is not None,
        "シグナルの日": data["dates"][sig_pos] if sig_pos is not None else None,
        "シグナルの時期（安値日から営業日）": sig_pos - low_pos if sig_pos is not None else None,
        "乗り遅れ（安値からシグナルまでの上昇, %）": round((close[sig_pos] / low - 1) * 100, 1) if sig_pos is not None else None,
        "取れた割合（シグナルから高値まで／安値から高値まで, %）": (
            round((high - close[sig_pos]) / (high - low) * 100, 1) if sig_pos is not None and high > low else None
        ),
        "RS上位10%入り": rs_pos is not None,
        "RS上位入りの時期（安値日から営業日）": rs_pos - low_pos if rs_pos is not None else None,
        # 安値日の特徴
        "安値日のRSの順位(上位%)": round((1 - rs_rank[low_pos]) * 100, 1) if not np.isnan(rs_rank[low_pos]) else None,
        "安値日の200EMAからの距離(%)": round((low / ema200[low_pos] - 1) * 100, 1),
        "安値日の52週高値からの距離(%)": round((low / high52[low_pos] - 1) * 100, 1),
        "上昇中の最大の1日上昇率(%)": round(np.nanmax(daily) * 100, 1) if len(daily) else None,
    }

    # 見逃した型（上から順に、最初に当てはまったもの）
    if sig_pos is not None:
        row["見逃しの型"] = None
    elif data["n_obs"][t].iloc[low_pos] <= EMA_WARMUP_DAYS:
        row["見逃しの型"] = MISSED_TYPES[0]
    elif po[max(low_pos - PO_RECENT_DAYS, 0):low_pos + 1].any():
        row["見逃しの型"] = MISSED_TYPES[1]
    elif low < ema200[low_pos]:
        row["見逃しの型"] = MISSED_TYPES[2]
    elif len(daily) and np.nanmax(daily) >= GAP_UP:
        row["見逃しの型"] = MISSED_TYPES[3]
    else:
        row["見逃しの型"] = MISSED_TYPES[4]

    # 見逃した直接の理由（上から順に、最初に当てはまったもの）
    if sig_pos is not None:
        row["見逃しの理由"] = None
    elif fresh[cap_lo:cap_hi + 1].any():
        row["見逃しの理由"] = MISSED_REASONS[0]
    elif high_pos > cap_hi and signal[cap_hi + 1:high_pos + 1].any():
        row["見逃しの理由"] = MISSED_REASONS[1]
    elif po[cap_lo:cap_hi + 1].any():
        row["見逃しの理由"] = MISSED_REASONS[2]
    else:
        row["見逃しの理由"] = MISSED_REASONS[3]
    return row


def baseline_capture(excess, starts, data):
    # 勝ち銘柄でない銘柄・起点を1件ずつのエピソードとみなし、同じ手順で捕捉を判定する（偶然の捕捉率）
    rows = []
    for d in starts:
        row = excess.loc[d]
        pos = excess.index.get_loc(d)
        for ticker, r in row[row < WIN_THRESHOLD].items():
            res = analyze_episode({"ticker": ticker, "first_pos": pos, "last_pos": pos, "max_excess": r, "n_starts": 1}, data)
            rows.append({"捕捉": res["捕捉"], "RS上位10%入り": res["RS上位10%入り"]})
    return pd.DataFrame(rows)


def summarize(df, label):
    caught = df[df["捕捉"]]
    print(f"\n■ {label}: {len(df)}エピソード")
    print(f"  PO新規+RSプラスで捕捉: {len(caught)}件（{len(caught) / len(df):.1%}）"
          f"／参考: RS上位10%入り {df['RS上位10%入り'].mean():.1%}")
    if len(caught):
        print(f"  捕捉した場合の中央値: シグナルの時期 安値日から{caught['シグナルの時期（安値日から営業日）'].median():+.0f}営業日、"
              f"乗り遅れ {caught['乗り遅れ（安値からシグナルまでの上昇, %）'].median():+.1f}%、"
              f"取れた割合 {caught['取れた割合（シグナルから高値まで／安値から高値まで, %）'].median():.0f}%"
              f"（安値から高値の上昇率の中央値 {df['安値から高値の上昇率(%)'].median():+.1f}%）")
    missed = df[~df["捕捉"]]
    counts = missed["見逃しの型"].value_counts().reindex(MISSED_TYPES, fill_value=0)
    print("  見逃しの型: " + "、".join(f"{k} {v}件（{v / len(df):.0%}）" for k, v in counts.items()))
    reasons = missed["見逃しの理由"].value_counts().reindex(MISSED_REASONS, fill_value=0)
    print("  見逃しの理由: " + "、".join(f"{k} {v}件（{v / len(df):.0%}）" for k, v in reasons.items()))
    print(pd.crosstab(missed["見逃しの型"], missed["見逃しの理由"]).to_string())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", action="store_true", help=f"エピソードの一覧を{EXPORT_FILE.name}に書き出す")
    args = parser.parse_args()

    close_wide, _ = load_wide(with_history=True)
    tickers = [t for t in liquidity_passed_tickers(ROOT / "screening_result.csv") if t in close_wide.columns]
    close = close_wide[tickers]
    bench = close_wide[BENCHMARK_TICKER]

    rs = relative_strength_wide(close_wide)[tickers]
    rs_rank = rs.rank(axis=1, pct=True)
    fresh = fresh_perfect_order_wide(close)
    signal = fresh & (rs > 0)
    data = {
        "dates": close.index.strftime("%Y-%m-%d"),
        "close": close,
        "fresh": fresh,
        "signal": signal,
        "rs_top": rs_rank > 1 - RS_TOP_PCT,
        "rs_rank": rs_rank,
        "po": perfect_order_wide(close),
        "ema200": close.ewm(span=200, adjust=False).mean(),
        "high52": close.rolling(250, min_periods=1).max(),
        "n_obs": close.notna().cumsum(),
    }

    excess = forward_excess_return(close, bench, HORIZON)
    first_days = close.index.to_series().groupby(close.index.to_period("M")).first()
    starts = [d for d in first_days if d >= pd.Timestamp(JUDGE_FROM) and excess.loc[d].notna().any()]
    episodes = find_episodes(excess, starts)
    df = pd.DataFrame([analyze_episode(ep, data) for ep in episodes])
    sectors = pd.read_csv(ROOT / "universe.csv").set_index("ticker")
    df.insert(1, "銘柄名", df["ticker"].map(sectors["銘柄名"]))
    df.insert(2, "業種", df["ticker"].map(sectors["33業種区分"]))

    n_winners = sum(int((excess.loc[d] >= WIN_THRESHOLD).sum()) for d in starts)
    print(f"対象: 流動性フィルタ通過{len(tickers)}銘柄、起点 {starts[0]:%Y-%m}〜{starts[-1]:%Y-%m}（{len(starts)}か月）")
    print(f"勝ち銘柄（60日超過+{WIN_THRESHOLD:.0%}以上）: 起点ごとに数えて{n_winners}件 → 同じ上昇をまとめて{len(df)}エピソード")
    summarize(df, f"勝ち銘柄（+{WIN_THRESHOLD:.0%}以上）")
    summarize(df[df["大化け"]], f"大化け（+{BIG_THRESHOLD:.0%}以上）")

    base_df = baseline_capture(excess, starts, data)
    print(f"\n■ 偶然の捕捉率（勝ち銘柄でない銘柄・起点 {len(base_df)}件に同じ判定）")
    print(f"  PO新規+RSプラス {base_df['捕捉'].mean():.1%}／RS上位10%入り {base_df['RS上位10%入り'].mean():.1%}")

    # だましの側（参考）: シグナルが出た銘柄・日のうち、その後60営業日で勝ち銘柄の水準に届いた割合
    judged = excess.index >= pd.Timestamp(JUDGE_FROM)
    base = excess[judged].stack().dropna()
    sig_ex = excess[judged].where(signal[judged]).stack().dropna()
    print(f"\n■ 参考: 60営業日後の超過リターンが+{WIN_THRESHOLD:.0%}以上になる割合")
    print(f"  全銘柄・全日 {(base >= WIN_THRESHOLD).mean():.1%}（{len(base)}件）／"
          f"PO新規+RSプラスのシグナル {(sig_ex >= WIN_THRESHOLD).mean():.1%}（{len(sig_ex)}件）")

    if args.export:
        df.sort_values(["最初の起点", "最大の60日超過リターン(%)"], ascending=[True, False]).to_csv(
            EXPORT_FILE, index=False, encoding="utf-8-sig"  # Excelで文字化けしないようBOM付き
        )
        print(f"\nエピソードの一覧を{EXPORT_FILE.relative_to(ROOT)}に書き出しました（{len(df)}件）")


if __name__ == "__main__":
    main()
