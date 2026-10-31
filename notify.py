"""日次ジョブの結果をDiscordのWebhookへ通知する

使い方:
    uv run python notify.py success --base <比較元のgit ref> [--dry-run]
    uv run python notify.py failure --message "<内容>" [--dry-run]

Webhook URLは.env（git管理外）のDISCORD_WEBHOOK_URLから読み込む。
"""
import argparse
import io
import json
import subprocess
import sys
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

from filters import WATCH_DAYS
from jpx import download_jpx_list, list_date

BASE_DIR = Path(__file__).parent
FAILED_FILE = "daily_failed_tickers.txt"
WATCH_FILE = "watch_list.csv"
MAX_LEN = 2000  # Discordの1メッセージの上限文字数
MAX_ITEMS = 10  # 新規銘柄の表示件数の上限
WATCH_MIN_SHARE = 0.6  # RS上位にいた日の割合がこれ以上なら「居続けている」（analysis/trade_study.pyの監視型と同じ線）
WATCH_MIN_ELAPSED = 5  # 「居続けている」の欄に出すのはこの営業日数目以降（日数が少なく割合だけ高い銘柄を除くため）
WATCH_JUDGE_DAYS = (10, 20)  # 判定日（監視の開始から何営業日目か）
WATCH_MAX_ITEMS = 5  # 「居続けている」の欄の表示件数の上限（新規が多い日も2000文字に収めるため）


def load_env():
    env = {}
    path = BASE_DIR / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
    return env


def git_show(ref, filename):
    """指定したgitコミット時点のファイル内容を返す（存在しなければNone）"""
    result = subprocess.run(
        ["git", "show", f"{ref}:./{filename}"], cwd=BASE_DIR, capture_output=True
    )
    return result.stdout if result.returncode == 0 else None


def read_csv_at(ref, filename):
    """指定したgitコミット時点のCSVを読み込む（存在しなければ空のDataFrame）"""
    content = git_show(ref, filename)
    if content is None:
        return pd.DataFrame(columns=["ticker"])
    return pd.read_csv(io.BytesIO(content))


def read_missing_all(text):
    """daily_failed_tickers.txtから「5日間すべて取得できず」の銘柄を取り出す"""
    tickers, in_section = [], False
    for line in text.splitlines():
        if line.startswith("#"):
            in_section = "5日間すべて" in line
        elif in_section and line.strip():
            tickers.append(line.strip())
    return tickers


def new_entries(filename, base):
    """現在のCSVと比較元のCSVを比べ、新規に入った銘柄を返す"""
    current = pd.read_csv(BASE_DIR / filename)
    previous = read_csv_at(base, filename)
    return current, current[~current["ticker"].isin(previous["ticker"])]


def format_items(df, fmt, max_items=MAX_ITEMS):
    lines = [fmt(row) for _, row in df.head(max_items).iterrows()]
    if len(df) > max_items:
        lines.append(f"　…ほか{len(df) - max_items}銘柄")
    return lines


def format_rs(value):
    # 最新日の終値が未反映の銘柄はRSを計算できず空欄になるため「-」と表示する
    return f"RS {value:+.1%}" if pd.notna(value) else "RS -"


def format_watch(r):
    return (
        f"・{r['ticker']} {r['銘柄名']}（{r['33業種区分']}）"
        f"PO {pd.Timestamp(r['po_date']):%m/%d}・{r['elapsed']}日目 RS上位 {r['rs_top_days']}/{r['elapsed']}日 {format_rs(r['relative_strength'])}"
    )


def watch_lines():
    """監視中の欄（判定日の銘柄と、RS上位に居続けている銘柄）"""
    watch = pd.read_csv(BASE_DIR / WATCH_FILE)
    staying = watch[(watch["rs_top_share"] >= WATCH_MIN_SHARE) & (watch["elapsed"] >= WATCH_MIN_ELAPSED)]
    judged = staying[staying["elapsed"].isin(WATCH_JUDGE_DAYS)]
    ended = watch[(watch["elapsed"] == WATCH_DAYS) & (watch["rs_top_share"] < WATCH_MIN_SHARE)]
    lines = ["", f"**監視中**: {len(watch)}銘柄（RS上位に居続け {len(staying)}）"]
    if len(judged) or len(ended):
        lines.append("🔔 判定日")
        lines += format_items(judged, format_watch)
        if len(ended):
            lines.append(f"・{WATCH_DAYS}日目で{WATCH_MIN_SHARE:.0%}未満のため監視終了: {len(ended)}銘柄")
    others = staying.drop(judged.index)
    if len(others):
        lines.append(f"居続けている銘柄（{WATCH_MIN_ELAPSED}日目以降、RS上位にいた日が{WATCH_MIN_SHARE:.0%}以上）")
        lines += format_items(others, format_watch, WATCH_MAX_ITEMS)
    lines.append("※参考情報（買いの合図ではない）")
    return lines


def jpx_update_notice():
    """JPXの一覧が手元より新しければ、月次更新を促す1行を返す（確認できなければNone）"""
    try:
        latest = list_date(download_jpx_list())
        local = list_date(BASE_DIR / "data_j.xlsx")
    except Exception as e:
        print(f"JPX一覧の更新確認に失敗しました: {e}")
        return None
    if latest > local:
        return (
            f"📅 JPXの上場銘柄一覧が更新されています（{latest:%Y-%m-%d}時点、手元は{local:%Y-%m-%d}時点）。"
            "月次更新を実行してください"
        )
    return None


def build_success_message(base, dashboard_url):
    rs, rs_new = new_entries("rs_ranking.csv", base)
    po, po_new = new_entries("perfect_order.csv", base)

    lines = [f"✅ 日次スクリーニング完了（{date.today():%Y-%m-%d}）", ""]
    lines.append(f"**RS上位10%**: {len(rs)}銘柄（新規 {len(rs_new)}）")
    lines += format_items(
        rs_new,
        lambda r: f"・{r['ticker']} {r['銘柄名']}（{r['33業種区分']}）RS {r['relative_strength']:+.1%}",
    )
    lines.append("")
    lines.append(f"**監視開始（PO新規）**: {len(po)}銘柄（新規 {len(po_new)}）")
    lines += format_items(
        po_new,
        lambda r: f"・{r['ticker']} {r['銘柄名']}（{r['33業種区分']}）{r['po_date']} {format_rs(r['relative_strength'])}",
    )
    lines += watch_lines()
    missing = read_missing_all((BASE_DIR / FAILED_FILE).read_text(encoding="utf-8"))
    if missing:
        previous = git_show(base, FAILED_FILE)
        previous = read_missing_all(previous.decode("utf-8")) if previous else []
        missing_new = [t for t in missing if t not in previous]
        names = pd.read_csv(BASE_DIR / "universe.csv").set_index("ticker")["銘柄名"]
        lines.append("")
        lines.append(
            f"⚠️ **5日間取得できない銘柄**: {len(missing)}銘柄（新規 {len(missing_new)}）"
            "　上場廃止・売買停止の可能性"
        )
        lines += [f"・{t} {names.get(t, '')}" for t in missing_new[:MAX_ITEMS]]
    notice = jpx_update_notice()
    if notice:
        lines += ["", notice]
    if dashboard_url:
        # <>で囲むとDiscordのリンクプレビューが表示されない
        lines += ["", f"<{dashboard_url}>"]
    return "\n".join(lines)


def build_failure_message(message):
    return (
        f"❌ 日次ジョブが失敗しました（{date.today():%Y-%m-%d}）\n"
        f"{message}\n詳細: logs/daily_job.log"
    )


def send(url, content, mention_user_id=None):
    # 自分宛てのメンションを付けるとアプリアイコンのバッジに数えられ、見落としにくくなる
    if mention_user_id:
        content = f"<@{mention_user_id}>\n{content}"
    if len(content) > MAX_LEN:
        content = content[: MAX_LEN - 1] + "…"
    payload = {"content": content}
    if mention_user_id:
        # メンションの対象を自分だけに限定する
        payload["allowed_mentions"] = {"users": [mention_user_id]}
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        # User-Agent未指定だとDiscord側で拒否される場合があるため明示する
        headers={"Content-Type": "application/json", "User-Agent": "jp-stock-screener"},
    )
    urllib.request.urlopen(request, timeout=10)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("status", choices=["success", "failure"])
    parser.add_argument("--base", default="HEAD", help="新規判定の比較元となるgit ref")
    parser.add_argument("--message", default="", help="失敗時に通知する内容")
    parser.add_argument("--dry-run", action="store_true", help="送信せずに表示のみ")
    args = parser.parse_args()

    env = load_env()
    if args.status == "success":
        content = build_success_message(args.base, env.get("DASHBOARD_URL"))
    else:
        content = build_failure_message(args.message)

    if args.dry_run:
        print(content)
        return

    url = env.get("DISCORD_WEBHOOK_URL")
    if not url:
        sys.exit(".envにDISCORD_WEBHOOK_URLが設定されていません")
    send(url, content, env.get("DISCORD_MENTION_USER_ID"))
    print("Discordに通知しました")


if __name__ == "__main__":
    main()
