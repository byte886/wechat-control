#!/usr/bin/env bash
set -euo pipefail

# 只激活微信到前台，不强制全屏、不缩小字体
# （原项目会 Ctrl+Cmd+F 全屏 + Cmd+- 缩小8次，体验差且会遮挡其他窗口）

if [ "${WECHAT_VIEWPORT_PREPARED:-0}" = "1" ]; then
  exit 0
fi

open -a WeChat

osascript <<OSA
tell application "WeChat" to activate
delay 0.3
tell application "System Events"
  tell process "WeChat"
    set frontmost to true
  end tell
end tell
delay 0.2
OSA
