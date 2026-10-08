"""月次更新（fetch_universe.py → screening.py）の結果を monthly_log.md に追記する

使い方:
    uv run python monthly_log.py [--base <更新前のgit ref>] [--dry-run]

更新前のcommit（既定はHEAD）と現在の universe.csv / screening_result.csv を比べ、
除外・追加・銘柄名や市場区分の変更・流動性フィルタ通過数の変化を記録する。
判断を伴う作業（持株会社化の手動追加など）は、生成後に「メモ」へ手で追記する。
"""
import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from notify import read_csv_at

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "monthly_log.md"
FAILED_FILE = BASE_DIR / "failed_tickers.txt"
LOG_HEADER = (
    "# 月次更新ログ\n\n"
    "月次更新（`fetch_universe.py` → `screening.py`）のたびに`monthly_log.py`で追記する。"
    "手順はREADMEの「日次・月次のジョブ分割」を参照。\n"
)


def short_market(market):
    return market.replace("（内国株式）", "")


def fmt(row, suffix=""):
    return f"- {row['ticker'].removesuffix('.T')} {row['銘柄名']}（{short_market(row['市場・商品区分'])}）{suffix}"


def passed(screening):
    return set(screening[screening["除外"] == False]["ticker"])


def build_entry(base):
    old_u = read_csv_at(base, "universe.csv")
    new_u = pd.read_csv(BASE_DIR / "universe.csv")
    old_s = read_csv_at(base, "screening_result.csv")
    new_s = pd.read_csv(BASE_DIR / "screening_result.csv")
    jpx = pd.read_excel(BASE_DIR / "data_j.xlsx", dtype={"コード": str})
    jpx_date = pd.to_datetime(str(jpx["日付"].iloc[0])).date()

    removed = old_u[~old_u["ticker"].isin(new_u["ticker"])]
    added = new_u[~new_u["ticker"].isin(old_u["ticker"])]
    # JPX一覧に載っていないのにuniverse.csvにある銘柄 = 手動追加分
    manual = set(new_u["ticker"]) - set(jpx["コード"] + ".T")
    merged = old_u.merge(new_u, on="ticker", suffixes=("_old", "_new"))
    changed = merged[
        (merged["銘柄名_old"] != merged["銘柄名_new"])
        | (merged["市場・商品区分_old"] != merged["市場・商品区分_new"])
    ]
    passed_old, passed_new = passed(old_s), passed(new_s)
    failed = FAILED_FILE.read_text().split() if FAILED_FILE.exists() else []

    lines = [
        f"## {date.today():%Y-%m-%d}（JPX一覧: {jpx_date:%Y-%m-%d}時点）",
        "",
        f"- 銘柄数: {len(old_u):,} → {len(new_u):,}（除外 {len(removed)} / 追加 {len(added)}、うち手動追加 {len(manual)}）",
        f"- 流動性フィルタ通過: {len(passed_old):,} → {len(passed_new):,}"
        f"（外れた {len(passed_old - passed_new)} / 加わった {len(passed_new - passed_old)}）",
        f"- screening.pyの取得失敗: {len(failed)}銘柄" + (f"（{', '.join(t.removesuffix('.T') for t in failed)}）" if failed else ""),
        "",
        "### 除外",
        *([fmt(r) for _, r in removed.iterrows()] or ["- なし"]),
        "",
        "### 追加",
        *([fmt(r, "※手動追加（JPX一覧に未掲載）" if r["ticker"] in manual else "") for _, r in added.iterrows()] or ["- なし"]),
        "",
        "### 銘柄名・市場区分の変更",
        *([
            f"- {r['ticker'].removesuffix('.T')} {r['銘柄名_old']}（{short_market(r['市場・商品区分_old'])}）"
            f"→ {r['銘柄名_new']}（{short_market(r['市場・商品区分_new'])}）"
            for _, r in changed.iterrows()
        ] or ["- なし"]),
        "",
        "### メモ",
        "- （手作業や気づいたこと、翌月への積み残しを追記）",
        "",
    ]
    return jpx_date, "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="HEAD", help="更新前の状態を指すgit ref")
    parser.add_argument("--dry-run", action="store_true", help="追記せずに表示のみ")
    args = parser.parse_args()

    jpx_date, entry = build_entry(args.base)
    if args.dry_run:
        print(entry)
        return

    log = LOG_FILE.read_text(encoding="utf-8") if LOG_FILE.exists() else LOG_HEADER
    if f"（JPX一覧: {jpx_date:%Y-%m-%d}時点）" in log:
        sys.exit(f"JPX一覧 {jpx_date} 時点の記録は既に monthly_log.md にあります")
    LOG_FILE.write_text(log.rstrip("\n") + "\n\n" + entry, encoding="utf-8")
    print(f"monthly_log.md に {jpx_date} 時点の記録を追記しました")


if __name__ == "__main__":
    main()
