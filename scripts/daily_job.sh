#!/bin/bash
# LaunchDaemonから呼び出す日次ジョブ: 終値蓄積 → スクリーニング → git commit & push
set -euo pipefail

# LaunchDaemonは最小限の環境変数で起動するため明示的に設定する
export HOME="/Users/achilles"
export PATH="$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export LANG="ja_JP.UTF-8"

cd "$(dirname "$0")/.."

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 開始 ====="

# MacBook Air側でpushされたコード変更を取り込む
git pull --rebase --autostash origin develop

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

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 終了 ====="
