"""保有ルールを入れた売買の検証

PO新規+RSプラスのシグナルで買い、手じまいのルールを変えたときの成績（TOPIXに対する超過リターン）を比べる。
signal_study.pyは60営業日後の結果だけを見るため、長く持ち続けて伸びる銘柄（福山通運など）の良さが見えない。

買い: シグナルの翌日の終値／売り: 手じまいの条件を満たした翌日の終値（同じ終値で売買すると実際より有利になるため）
比べる相手: シグナル1件ごとに、同じ日にPOが成立している別の銘柄（新規でないもの）を無作為に選び、同じルールで売買する
買い直しありのルール（②′③′）: シグナルから見張る期間（250営業日）の間、条件が崩れたら売り、戻ったら買い直す。
成績は持っていた日だけの騰落率から、同じ日だけTOPIXを持った場合の騰落率を引いたもの。1往復0.2%のコスト込みの成績も出す

実行（プロジェクトフォルダで）: uv run python analysis/trade_study.py
--exportを付けると、取引1件ごとの一覧をanalysis/trades.csv（git管理外）に書き出す。
データは読むだけで書き換えない。

注意: 手数料・スリッページは含めない。データの終わりまで売っていない取引は最後の日の終値で評価する（未決済）。
取引の期間は重なり、同じ相場の影響を受けるため、件数ほどの確からしさはない。
流動性フィルタは現時点の通過銘柄を過去にも当てはめている。
"""
import argparse
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

ENTRY_FROM = "2025-01-01"  # この日以降のシグナルで買う（2024年10〜12月はEMAの助走期間）
ENTRY_TO = "2026-04-30"  # この日までのシグナルで買う（手じまいまでの期間を残すため）
FIXED_DAYS = 60  # ①: この営業日数で売る（signal_study.pyと同じ物差し）
EMA200_CHECK_DAY = 20  # ④: シグナルからこの営業日数の日に200EMAより下なら売る
TRAIL_STOP = 0.20  # ⑤: 買ってからの最高値からこの割合下げたら売る
N_CONTROL = 5  # 比べる相手: シグナル1件につき選ぶ銘柄の数
SEED = 0  # 比べる相手を選ぶ乱数の種（毎回同じ結果にする）
TOP_SHARE = 0.10  # 利益のうち、上位のこの割合の取引が占める割合を見る
EXPORT_FILE = ROOT / "analysis" / "trades.csv"  # --exportで書き出す取引の一覧
SLOPE200_DAYS = 20  # ⑥: 200EMAがこの営業日数前より高ければ「上向き」とみなす
RULES = [
    "①60日で売る", "②POが崩れたら売る", "③75EMAを割ったら売る", "④20日目に200EMAの下なら売り、以後は200EMAを割るまで",
    "⑤高値から20%下げたら売る", "⑥200EMAが上向きの間は200EMA割れ、それ以外は75EMA割れで売る", "⑦TOPIX比の線が75EMAを割ったら売る",
]
WATCH_DAYS = 250  # ②′③′: シグナルからこの営業日数の間は、条件が戻ったら買い直す（約1年）
COST = 0.002  # 1往復（買って売る）あたりのコスト（手数料・スリッページの目安）
RULES_RE = {"②′POが成立している間は持つ（買い直しあり）": "po", "③′75EMAより上の間は持つ（買い直しあり）": "above75"}  # ルール名: 持つ条件
CHECK_TICKER, CHECK_DATE = "9075.T", "2025-07-28"  # 確認用: 福山通運のシグナル


def first_after(flags, start):
    # flags[start:]で最初にTrueになる位置（なければNone）
    hits = np.flatnonzero(flags[start:])
    return start + hits[0] if len(hits) else None


def simulate(sig_pos, j, a):
    # シグナルの日sig_pos・銘柄の列jで買ったときの、ルールごとの(売った日の位置, 未決済か)
    n = len(a["close"])
    entry = sig_pos + 1
    c = a["close"][:, j]
    exits = {}

    def sell_after(pos):  # 条件を満たした日の翌日に売る（データの終わりを越えたら未決済）
        if pos is None or pos + 1 > n - 1:
            return n - 1, True
        return pos + 1, False

    exits[RULES[0]] = (entry + FIXED_DAYS, False) if entry + FIXED_DAYS <= n - 1 else (n - 1, True)
    exits[RULES[1]] = sell_after(first_after(~a["po"][:, j], entry))
    exits[RULES[2]] = sell_after(first_after(c < a["ema75"][:, j], entry))
    check = sig_pos + EMA200_CHECK_DAY
    below200 = c < a["ema200"][:, j]
    if check > n - 1:
        exits[RULES[3]] = (n - 1, True)
    elif below200[check]:
        exits[RULES[3]] = sell_after(check)
    else:
        exits[RULES[3]] = sell_after(first_after(below200, check + 1))
    peak = np.maximum.accumulate(c[entry:])
    hit = first_after(c[entry:] <= peak * (1 - TRAIL_STOP), 0)
    exits[RULES[4]] = sell_after(entry + hit if hit is not None else None)
    up200 = a["up200"][:, j]
    exits[RULES[5]] = sell_after(first_after((up200 & below200) | (~up200 & (c < a["ema75"][:, j])), entry))
    exits[RULES[6]] = sell_after(first_after(a["rel_below75"][:, j], entry))
    return entry, exits


def campaign(sig_pos, j, a, cond):
    # シグナルの翌日に買い、見張る期間の間は前日に条件を満たしていれば持ち、崩れたら売り、戻ったら買い直す（どれも翌日の終値で売買）
    # → (持っていた日の騰落率, 同じ日だけTOPIXを持った場合の騰落率, 保有日数, 売買の回数, 見張る期間が終わっていないか)
    n = len(a["close"])
    entry = sig_pos + 1
    end = min(sig_pos + WATCH_DAYS, n - 1)
    held = np.zeros(end - entry + 1, dtype=bool)  # その日の終値の後に持っているか
    held[0] = True
    held[1:-1] = cond[entry:end - 1, j]  # 前日の条件で決める。最後の日は売る
    c, b = a["close"][entry:end + 1, j], a["bench"][entry:end + 1]
    on = held[:-1]
    growth = np.prod(np.where(on, c[1:] / c[:-1], 1.0))
    bench_growth = np.prod(np.where(on, b[1:] / b[:-1], 1.0))
    n_trades = 1 + int((held[1:-1] & ~held[:-2]).sum())
    return growth, bench_growth, int(on.sum()), n_trades, sig_pos + WATCH_DAYS > n - 1


def trade_rows(sig_pos, j, a, group):
    entry, exits = simulate(sig_pos, j, a)
    c, b = a["close"][:, j], a["bench"]
    base = {"区分": group, "シグナルの日": a["dates"][sig_pos], "ticker": a["tickers"][j], "買った日": a["dates"][entry],
            "200EMA安定": a["stable"][entry, j]}
    rows = []
    for rule, (x, still_open) in exits.items():
        growth, bench_growth = c[x] / c[entry], b[x] / b[entry]
        rows.append(base | {
            "ルール": rule,
            "売った日": a["dates"][x],
            "保有日数": x - entry,
            "売買の回数": 1,
            "未決済": still_open,
            "超過リターン": growth - bench_growth,
            "コスト込み超過リターン": growth * (1 - COST) - bench_growth,
        })
    if entry + 1 > len(c) - 1:
        return rows
    for rule, key in RULES_RE.items():
        growth, bench_growth, days, n_trades, still_open = campaign(sig_pos, j, a, a[key])
        rows.append(base | {
            "ルール": rule,
            "売った日": a["dates"][min(sig_pos + WATCH_DAYS, len(c) - 1)],
            "保有日数": days,
            "売買の回数": n_trades,
            "未決済": still_open,
            "超過リターン": growth - bench_growth,
            "コスト込み超過リターン": growth * (1 - COST) ** n_trades - bench_growth,
        })
    return rows


def profit_factor(r):
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    return gains / losses if losses else np.nan


def summary(trades):
    # ルールごとの成績（超過リターンは%）
    def stats(g):
        r = g["超過リターン"]
        gains = r[r > 0].sum()
        top = r.nlargest(max(int(len(r) * TOP_SHARE), 1))
        return pd.Series({
            "件数": len(r),
            "平均(%)": r.mean() * 100,
            "中央値(%)": r.median() * 100,
            "勝率(%)": (r > 0).mean() * 100,
            "平均保有日数": g["保有日数"].mean(),
            "売買の回数": g["売買の回数"].mean(),
            "PF": profit_factor(r),
            "コスト込み平均(%)": g["コスト込み超過リターン"].mean() * 100,
            "コスト込みPF": profit_factor(g["コスト込み超過リターン"]),
            "最大の損失(%)": r.min() * 100,
            f"上位{TOP_SHARE:.0%}の利益の割合(%)": top[top > 0].sum() / gains * 100 if gains else np.nan,
            "未決済(%)": g["未決済"].mean() * 100,
        })
    table = trades.groupby("ルール", sort=False).apply(stats, include_groups=False).round(1)
    # 1日あたりは値が小さいため小数第3位まで
    daily = trades.groupby("ルール", sort=False).apply(
        lambda g: pd.Series({"1日あたり(%)": per_day(g, "超過リターン"), "コスト込み1日あたり(%)": per_day(g, "コスト込み超過リターン")}),
        include_groups=False,
    ).round(3)
    return pd.concat([daily, table], axis=1)


def per_day(g, col):
    # 持っていた日1日あたりの超過リターン（%）＝超過リターンの合計÷保有日数の合計
    days = g["保有日数"].sum()
    return g[col].sum() / days * 100 if days else np.nan


def by_year(trades):
    # 買った年ごとに「1日あたり(%)／PF／コスト込み1日あたり(%)／平均(%)」の形で並べる
    t = trades.assign(年=trades["買った日"].str[:4])
    table = t.groupby(["ルール", "年"], sort=False).apply(
        lambda g: f"{per_day(g, '超過リターン'):.3f}／{profit_factor(g['超過リターン']):.2f}"
                  f"／{per_day(g, 'コスト込み超過リターン'):.3f}／{g['超過リターン'].mean() * 100:.1f}",
        include_groups=False,
    )
    return table.unstack("年")


def report(trades, title):
    print(f"\n{'=' * 10} {title} {'=' * 10}")
    for group in ["シグナル", "比べる相手"]:
        t = trades[trades["区分"] == group]
        print(f"\n■ {group}（{t[t['ルール'] == RULES[0]].shape[0]}件）")
        print(summary(t).to_string())
    print("\n■ 買った年ごと（1日あたり(%)／PF／コスト込み1日あたり(%)／平均(%)）")
    both = pd.concat({g: by_year(trades[trades["区分"] == g]) for g in ["シグナル", "比べる相手"]}, axis=1)
    print(both.to_string())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", action="store_true", help=f"取引の一覧を{EXPORT_FILE.name}に書き出す")
    args = parser.parse_args()

    close_wide, _ = load_wide(with_history=True)
    tickers = [t for t in liquidity_passed_tickers(ROOT / "screening_result.csv") if t in close_wide.columns]
    close = close_wide[tickers]
    rs = relative_strength_wide(close_wide)[tickers]
    fresh = fresh_perfect_order_wide(close)
    signal = fresh & (rs > 0)
    po = perfect_order_wide(close)
    n_obs = close.notna().cumsum()
    filled = close.ffill()  # 売買の途中で終値が欠けた日は、直前の終値で見る
    ema200 = close.ewm(span=200, adjust=False).mean()
    rel = close.div(close_wide[BENCHMARK_TICKER], axis=0)  # TOPIX比の線
    a = {
        "dates": close.index.strftime("%Y-%m-%d"),
        "tickers": tickers,
        "close": filled.to_numpy(),
        "bench": close_wide[BENCHMARK_TICKER].ffill().to_numpy(),
        "po": po.to_numpy(),
        "ema75": close.ewm(span=75, adjust=False).mean().to_numpy(),
        "ema200": ema200.to_numpy(),
        "above75": (close > close.ewm(span=75, adjust=False).mean()).to_numpy(),
        "up200": (ema200 > ema200.shift(SLOPE200_DAYS)).to_numpy(),
        "rel_below75": (rel < rel.ewm(span=75, adjust=False).mean()).to_numpy(),
        "stable": (n_obs >= EMA200_MIN_DAYS).to_numpy(),
    }
    # 比べる相手の候補: POが成立中（新規でない）・助走期間を過ぎ・当日の終値がある銘柄
    control_pool = (po & ~fresh & close.notna() & (n_obs > EMA_WARMUP_DAYS)).to_numpy()
    sig = signal.to_numpy()
    in_period = (close.index >= pd.Timestamp(ENTRY_FROM)) & (close.index <= pd.Timestamp(ENTRY_TO))

    rng = np.random.default_rng(SEED)
    rows = []
    for i in np.flatnonzero(in_period):
        if i + 1 > len(close) - 1:
            continue
        pool = np.flatnonzero(control_pool[i])
        for j in np.flatnonzero(sig[i]):
            rows += trade_rows(i, j, a, "シグナル")
            for k in rng.choice(pool, size=min(N_CONTROL, len(pool)), replace=False):
                rows += trade_rows(i, k, a, "比べる相手")
    trades = pd.DataFrame(rows)

    print(f"対象: 流動性フィルタ通過{len(tickers)}銘柄、シグナルの日 {ENTRY_FROM}〜{ENTRY_TO}、データの最終日 {a['dates'][-1]}")
    print("買い: シグナルの翌日の終値／売り: 条件を満たした翌日の終値／超過リターンはTOPIX（1306.T）に対する分")
    report(trades, "全期間")
    report(trades[trades["200EMA安定"]], f"200EMAが安定してからの買い（データ開始から{EMA200_MIN_DAYS}営業日以降）")

    check = trades[(trades["区分"] == "シグナル") & (trades["ticker"] == CHECK_TICKER) & (trades["シグナルの日"] == CHECK_DATE)]
    if len(check):
        print(f"\n■ 確認: {CHECK_TICKER}（{CHECK_DATE}のシグナル）")
        print(check[["ルール", "買った日", "売った日", "保有日数", "売買の回数", "未決済", "超過リターン"]]
              .assign(超過リターン=lambda d: (d["超過リターン"] * 100).round(1)).to_string(index=False))

    if args.export:
        cols = ["超過リターン", "コスト込み超過リターン"]
        out = trades.assign(**{f"{c}(%)": (trades[c] * 100).round(2) for c in cols}).drop(columns=cols)
        out.to_csv(EXPORT_FILE, index=False, encoding="utf-8-sig")  # Excelで文字化けしないようBOM付き
        print(f"\n取引の一覧を{EXPORT_FILE.relative_to(ROOT)}に書き出しました（{len(out)}行）")


if __name__ == "__main__":
    main()
