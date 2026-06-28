#!/bin/zsh
set -euo pipefail

PORT="${SESSIONHUB_CHROME_PORT:-9222}"
PROFILE_DIR="${SESSIONHUB_CHROME_PROFILE:-$HOME/.sessionhub/chrome-9222}"
CHROME_APP="${SESSIONHUB_CHROME_APP:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
CHROME_BUNDLE="${SESSIONHUB_CHROME_BUNDLE:-Google Chrome}"

mkdir -p "$PROFILE_DIR"

if /usr/bin/curl -fsS "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
  echo "Chrome 9222 已运行：$PROFILE_DIR"
  exit 0
fi

# 关闭 macOS App Nap，避免后台被系统冻结（看起来像"挂起"）。
/usr/bin/defaults write com.google.Chrome NSAppSleepDisabled -bool YES >/dev/null 2>&1 || true

# -g 后台启动，不抢占前台焦点（静默运行，避免弹窗打扰）。
# 反节流参数：后台/被遮挡时也不冻结定时器与渲染进程，保持随用随响应。
/usr/bin/open -g -na "$CHROME_BUNDLE" --args \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check \
  --new-window \
  --disable-background-timer-throttling \
  --disable-backgrounding-occluded-windows \
  --disable-renderer-backgrounding \
  --disable-features=CalculateNativeWinOcclusion \
  about:blank >/dev/null 2>&1

for _ in {1..20}; do
  if /usr/bin/curl -fsS "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
    echo "Chrome 9222 已启动：$PROFILE_DIR"
    exit 0
  fi
  sleep 0.5
done

echo "Chrome 9222 启动失败，请检查 Google Chrome 是否已安装。" >&2
exit 1
