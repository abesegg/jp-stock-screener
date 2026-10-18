"""エントリーシグナルの検証

ゴールデンクロスとパーフェクトオーダー（5EMA > 25EMA > 75EMA）への転換について、
シグナル発生後にRS上位に入ったか・TOPIXを上回ったかを過去データで集計する。

実行（プロジェクトフォルダで）: uv run python analysis/signal_study.py
--exportを付けると、PO新規のシグナル1件ごとの一覧をanalysis/po_signals.csv（git管理外）に書き出す。
データは読むだけで書き換えない。
過去データ（ohlcv_history.csv、analysis/fetch_history.pyで取得）をdaily_ohlcv.csvの前につなげて使う。
--daily-onlyを付けるとdaily_ohlcv.csvだけで集計する。

注意: 流動性フィルタは現時点の通過銘柄を過去にも当てはめている（過去は流動性が低かった銘柄も含む）。
また上場廃止で一覧から消えた銘柄はデータにないため、結果はやや良く出る可能性がある。
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from filters import BENCHMARK_TICKER, liquidity_passed_tickers, relative_strength_wide  # noqa: E402

WARMUP_DAYS = 63  # 銘柄ごとに、データ開始からこの営業日数はEMAの助走期間として判定に使わない（約3か月）
FRESH_DAYS = 20  # 直前のこの営業日数にパーフェクトオーダーでなかった銘柄の成立を「新規」とする
SLOPE_DAYS = 5  # 75EMAの向きを判定する営業日数
RS_TOP_PCT = 0.10  # RS上位の基準（rs_ranking.csvと同じ上位10%）
RS_TOP_WITHIN = 20  # シグナル後、この営業日数以内にRS上位に入ったかを見る
HORIZONS = [20, 60]  # 超過リターンを測る営業日数
EXPORT_FILE = ROOT / "analysis" / "po_signals.csv"  # --exportで書き出すPO新規の一覧
HISTORY_FILE = ROOT / "ohlcv_history.csv"  # analysis/fetch_history.pyで取得した、daily_ohlcv.csvより前の過去データ（検証専用）


def load_close(with_history):
    # daily_ohlcv.csvと過去データは日付が重ならないため、つなげて横長にする
    paths = [ROOT / "daily_ohlcv.csv"] + ([HISTORY_FILE] if with_history else [])
    df = pd.concat([pd.read_csv(p, parse_dates=["date"]) for p in paths])
    return df.pivot(index="date", columns="ticker", values="close").sort_index()


def forward_excess_return(close, bench, horizon):
    # horizon営業日後までの騰落率の、ベンチマークに対する超過分（データの終わりを越える場合はNaN）
    stock_ret = close.shift(-horizon) / close - 1
    bench_ret = bench.shift(-horizon) / bench - 1
    return stock_ret.sub(bench_ret, axis=0)


def entered_rs_top_within(rs, days, top_pct):
    # 翌日からdays営業日以内に、RSが全銘柄中の上位top_pctに入ったか（期間がデータの終わりを越える日はNaN）
    top = rs.rank(axis=1, pct=True) > 1 - top_pct
    entered = sum(top.shift(-k, fill_value=False).astype(int) for k in range(1, days + 1)) > 0
    entered = entered.astype(float)
    entered.iloc[-days:] = float("nan")
    return entered


def summarize(name, signal, outcomes, n_days):
    mask = signal.to_numpy(dtype=bool)
    row = {"シグナル": name, "件数": int(mask.sum()), "1日あたり": round(mask.sum() / n_days, 1)}
    for label, values in outcomes.items():
        v = pd.Series(values.to_numpy()[mask]).dropna()
        if label.startswith(f"{RS_TOP_WITHIN}日以内"):
            row[f"{label}(%)"] = round(v.mean() * 100, 1)
        else:
            row[f"{label} 平均(%)"] = round(v.mean() * 100, 1)
            row[f"{label} 中央値(%)"] = round(v.median() * 100, 1)
            row[f"{label} 勝率(%)"] = round((v > 0).mean() * 100, 1)
        row[f"{label} 件数"] = len(v)
    return row


MONTHLY_SIGNALS = ["ゴールデンクロス(5/25)", "PO新規", "PO新規+RSプラス"]  # 月別に比べるシグナル


def monthly_summary(signals, outcome, metric):
    # シグナルが出た月ごとに、結果の集計値と件数を「値 (件数)」の形で並べる
    months = outcome.index.to_period("M")
    values = outcome.to_numpy()
    table = {}
    for name, signal in signals.items():
        mask = signal.to_numpy(dtype=bool)
        col = {}
        for month in months.unique():
            in_month = months == month
            v = pd.Series(values[in_month][mask[in_month]]).dropna()
            col[str(month)] = f"{metric(v) * 100:+.1f} ({len(v)})" if len(v) else "-"
        table[name] = col
    return pd.DataFrame(table)


def export_signals(signal, close, rs, outcomes, path):
    # シグナル1件を1行とする一覧をCSVに書き出す（成立日の古い順、同日はRSの高い順）
    hits = signal.stack()
    hits = hits[hits].index  # (日付, ticker)のMultiIndex
    dates = hits.get_level_values(0)
    universe = pd.read_csv(ROOT / "universe.csv").set_index("ticker")

    df = pd.DataFrame({
        "成立日": dates.strftime("%Y-%m-%d"),
        "月": dates.strftime("%Y-%m"),
        "ticker": hits.get_level_values(1),
    })
    df["銘柄名"] = df["ticker"].map(universe["銘柄名"])
    df["業種"] = df["ticker"].map(universe["33業種区分"])
    df["成立日の終値"] = close.stack().reindex(hits).to_numpy().round(1)
    rs_at = rs.stack().reindex(hits).to_numpy()
    df["成立日のRS(%)"] = (rs_at * 100).round(2)
    df["RSプラス"] = rs_at > 0
    for label, values in outcomes.items():
        v = values.stack().reindex(hits).to_numpy()
        if label.startswith(f"{RS_TOP_WITHIN}日以内"):
            df[label] = pd.array(v, dtype="Int64")  # 1/0、期間が未到来なら空欄
        else:
            df[f"{label}(%)"] = (v * 100).round(2)  # 期間が未到来なら空欄

    df = df.sort_values(["成立日", "成立日のRS(%)"], ascending=[True, False])
    df.to_csv(path, index=False, encoding="utf-8-sig")  # Excelで文字化けしないようBOM付き
    print(f"\nPO新規の一覧を{path.relative_to(ROOT)}に書き出しました（{len(df)}件）")


EMA200_MIN_DAYS = 200  # 200EMAを使うのは、銘柄ごとにデータ開始からこの営業日数以降（初日の終値の重みが約13%以下になる）
CROSS_WITHIN = 20  # 200EMAより下で成立した銘柄が、この営業日数以内に上抜けたかを見る


def ema200_study(fresh, close, bench):
    # PO新規と200EMAの位置関係（--ema200）。200EMAが安定した日のシグナルだけを使う
    # B・手じまい判断は、判断できる日（上抜けた日・20日目）から成績を測る
    ema200 = close.ewm(span=200, adjust=False).mean()
    stable = close.notna() & (close.notna().cumsum() >= EMA200_MIN_DAYS)
    above = (close > ema200).to_numpy()
    fwd = {h: forward_excess_return(close, bench, h).to_numpy() for h in HORIZONS}

    records = []  # (分け方, シグナルの年, 成績を測る起点の行, 列)
    for i, c in zip(*np.nonzero((fresh & stable).to_numpy())):
        year = close.index[i].year
        if above[i, c]:
            records.append(("A: 成立日に200EMAより上", year, i, c))
            continue
        records.append(("A: 成立日に200EMAより下", year, i, c))
        if i + CROSS_WITHIN >= len(close):
            continue  # 上抜けたかを判定する期間が未到来
        window = above[i + 1:i + 1 + CROSS_WITHIN, c]
        label = "上抜け" if window.any() else "上抜けず"
        if window.any():
            records.append(("B: 下で成立→上抜けた日に入る", year, i + 1 + window.argmax(), c))
        records.append((f"C（参考）: 下で成立→{CROSS_WITHIN}日以内に{label}", year, i, c))
        records.append((f"手じまい判断: {CROSS_WITHIN}日目に{label}（{CROSS_WITHIN}日目から測る）", year, i + CROSS_WITHIN, c))

    df = pd.DataFrame(records, columns=["分け方", "年", "row", "col"])
    for h in HORIZONS:
        df[h] = fwd[h][df["row"], df["col"]]
    stable_np, years = stable.to_numpy(), close.index.year.to_numpy()

    def fmt(v):
        v = pd.Series(v).dropna()
        return f"{v.median() * 100:+.1f}%／{(v > 0).mean() * 100:.0f}%（{len(v)}）" if len(v) else "-"

    print(f"\n■ PO新規と200EMAの位置関係（データ開始から{EMA200_MIN_DAYS}営業日以降のシグナル）　※超過リターンの「中央値／勝率（件数）」")
    for period in ["全期間"] + sorted(df["年"].unique().tolist()):
        part = df if period == "全期間" else df[df["年"] == period]
        base_mask = stable_np if period == "全期間" else stable_np & (years == period)[:, None]
        rows = {"（基準）200EMA安定後の全銘柄・全日": {f"{h}日超過": fmt(fwd[h][base_mask]) for h in HORIZONS}}
        for name, g in part.groupby("分け方", sort=False):
            rows[name] = {f"{h}日超過": fmt(g[h]) for h in HORIZONS}
        print(f"\n{period}:")
        print(pd.DataFrame(rows).T.to_string())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", action="store_true", help=f"PO新規の一覧を{EXPORT_FILE.name}に書き出す")
    parser.add_argument("--daily-only", action="store_true", help="過去データを使わずdaily_ohlcv.csvだけで集計する（以前の結果との比較用）")
    parser.add_argument("--ema200", action="store_true", help="PO新規と200EMAの位置関係を集計する")
    args = parser.parse_args()

    close_wide = load_close(not args.daily_only)
    tickers = [t for t in liquidity_passed_tickers(ROOT / "screening_result.csv") if t in close_wide.columns]
    close = close_wide[tickers]
    bench = close_wide[BENCHMARK_TICKER]

    ema5, ema25, ema75 = (close.ewm(span=s, adjust=False).mean() for s in (5, 25, 75))
    # 助走期間を過ぎ、当日の終値がある銘柄・日だけを判定対象にする
    usable = close.notna() & (close.notna().cumsum() > WARMUP_DAYS)

    po = (ema5 > ema25) & (ema25 > ema75)
    po_prev = po.shift(1, fill_value=False)
    po_start = po & ~po_prev & usable
    po_recent = po_prev.astype(float).rolling(FRESH_DAYS, min_periods=1).max() > 0
    fresh = po_start & ~po_recent
    gc = (ema5 > ema25) & ~(ema5.shift(1) > ema25.shift(1)) & usable
    ema75_up = ema75 > ema75.shift(SLOPE_DAYS)
    rs = relative_strength_wide(close_wide)[tickers]

    signals = {
        "ゴールデンクロス(5/25)": gc,
        "PO新規": fresh,
        "PO新規+75EMA上向き": fresh & ema75_up,
        "PO新規+RSプラス": fresh & (rs > 0),
        "PO再開（参考）": po_start & po_recent,
    }
    outcomes = {f"{RS_TOP_WITHIN}日以内にRS上位{RS_TOP_PCT:.0%}入り": entered_rs_top_within(rs, RS_TOP_WITHIN, RS_TOP_PCT)}
    for h in HORIZONS:
        outcomes[f"{h}日超過リターン"] = forward_excess_return(close, bench, h)

    n_days = int(usable.any(axis=1).sum())
    period = usable.index[usable.any(axis=1)]
    print(f"対象: 流動性フィルタ通過{len(tickers)}銘柄、判定期間 {period[0]:%Y-%m-%d}〜{period[-1]:%Y-%m-%d}（{n_days}営業日）\n")

    rows = [summarize("（基準）全銘柄・全日", usable, outcomes, n_days)]
    rows += [summarize(name, signal, outcomes, n_days) for name, signal in signals.items()]
    print(pd.DataFrame(rows).set_index("シグナル").T.to_string())

    monthly_signals = {"（基準）全銘柄・全日": usable} | {name: signals[name] for name in MONTHLY_SIGNALS}
    rs_label = f"{RS_TOP_WITHIN}日以内にRS上位{RS_TOP_PCT:.0%}入り"
    for title, outcome, metric in [
        (f"{rs_label}の割合(%)", outcomes[rs_label], lambda v: v.mean()),
        ("60日超過リターンの平均(%)", outcomes["60日超過リターン"], lambda v: v.mean()),
        ("60日超過リターンの中央値(%)", outcomes["60日超過リターン"], lambda v: v.median()),
    ]:
        print(f"\n■ 月別: {title}　※「値 (件数)」、月はシグナルが出た月")
        print(monthly_summary(monthly_signals, outcome, metric).to_string())

    if args.export:
        export_signals(fresh, close, rs, outcomes, EXPORT_FILE)
    if args.ema200:
        ema200_study(fresh, close, bench)


if __name__ == "__main__":
    main()
