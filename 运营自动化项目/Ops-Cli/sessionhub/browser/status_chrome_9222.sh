#!/bin/zsh
set -euo pipefail

PORT="${SESSIONHUB_CHROME_PORT:-9222}"

if /usr/bin/curl -fsS "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
  echo "Chrome 9222 可用"
  /usr/bin/curl -fsS "http://127.0.0.1:${PORT}/json/version"
  echo
  exit 0
fi

echo "Chrome 9222 未运行"
exit 1
