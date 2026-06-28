#!/bin/zsh
set -euo pipefail

PROFILE_DIR="${SESSIONHUB_CHROME_PROFILE:-$HOME/.sessionhub/chrome-9222}"

pids="$(pgrep -f "user-data-dir=${PROFILE_DIR}" || true)"
if [[ -z "$pids" ]]; then
  pids="$(pgrep -f "user-data-dir $PROFILE_DIR" || true)"
fi

if [[ -z "$pids" ]]; then
  echo "Chrome 9222 未运行：$PROFILE_DIR"
  exit 0
fi

echo "$pids" | xargs kill
echo "Chrome 9222 已关闭：$PROFILE_DIR"
