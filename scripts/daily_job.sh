#!/bin/bash
# LaunchDaemonから呼び出す日次ジョブ: 終値蓄積 → スクリーニング → git commit & push
set -euo pipefail

# LaunchDaemonは最小限の環境変数で起動するため明示的に設定する
export HOME="/Users/achilles"
export PATH="$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export LANG="ja_JP.UTF-8"

cd "$(dirname "$0")/.."

# 失敗時にDiscordへ通知する（通知自体が失敗してもジョブの結果は変えない）
notify_failure() {
    uv run python notify.py failure --message "daily_job.sh の${1}行目で失敗（exit code ${2}）" || true
}
trap 'code=$?; notify_failure $LINENO $code' ERR

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 開始 ====="

# MacBook Air側でpushされたコード変更を取り込む
git pull --rebase --autostash origin develop
# 新規銘柄の判定に使う比較元（前回のdaily update時点）
BASE_REF=$(git rev-parse HEAD)

uv run python daily_update.py
uv run python run_screening.py
uv run python run_golden_cross.py

git add daily_ohlcv.csv daily_failed_tickers.txt rs_ranking.csv golden_cross.csv
if git diff --cached --quiet; then
    echo "変更なしのためcommitをスキップ"
else
    git commit -m "daily update $(date '+%Y-%m-%d')"
    git push origin develop
fi

uv run python notify.py success --base "$BASE_REF" || echo "Discord通知に失敗しました"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 終了 ====="
