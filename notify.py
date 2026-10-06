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

BASE_DIR = Path(__file__).parent
FAILED_FILE = "daily_failed_tickers.txt"
MAX_LEN = 2000  # Discordの1メッセージの上限文字数
MAX_ITEMS = 10  # 新規銘柄の表示件数の上限


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


def format_items(df, fmt):
    lines = [fmt(row) for _, row in df.head(MAX_ITEMS).iterrows()]
    if len(df) > MAX_ITEMS:
        lines.append(f"　…ほか{len(df) - MAX_ITEMS}銘柄")
    return lines


def build_success_message(base, dashboard_url):
    rs, rs_new = new_entries("rs_ranking.csv", base)
    gc, gc_new = new_entries("golden_cross.csv", base)

    lines = [f"✅ 日次スクリーニング完了（{date.today():%Y-%m-%d}）", ""]
    lines.append(f"**RS上位10%**: {len(rs)}銘柄（新規 {len(rs_new)}）")
    lines += format_items(
        rs_new,
        lambda r: f"・{r['ticker']} {r['銘柄名']}（{r['33業種区分']}）RS {r['relative_strength']:+.1%}",
    )
    lines.append("")
    lines.append(f"**ゴールデンクロス**: {len(gc)}銘柄（新規 {len(gc_new)}）")
    lines += format_items(
        gc_new,
        lambda r: f"・{r['ticker']} {r['銘柄名']}（{r['33業種区分']}）{r['cross_date']}",
    )
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
