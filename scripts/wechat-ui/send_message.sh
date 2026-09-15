#!/usr/bin/env bash
set -euo pipefail

# 完整发送消息流程（Cmd+F方案，优化速度版）：
#   激活微信 → 切回聊天页 → Cmd+F搜索 → 回车选中 → 验证标题 → 点击输入框 → 粘贴 → 回车 → 验证
# 用法: ./send_message.sh <聊天名称> <消息内容> [--no-verify]
#   --no-verify: 跳过发送后的OCR验证（更快，但不确认发送结果）

script_dir="$(cd "$(dirname "$0")" && pwd)"
chat_name="${1:-}"
message="${2:-}"
skip_verify="${3:-}"
WECHAT_SKILL_DIR="$script_dir"
. "$script_dir/lib_wechat_ui.sh"

if [ -z "$chat_name" ] || [ -z "$message" ]; then
  echo "Usage: $0 <chat_name> <message> [--no-verify]" >&2
  exit 1
fi

echo "=== 步骤1: 激活微信、恢复窗口、切回聊天页面 ==="
# 用 reopen 恢复最小化窗口（比 activate 更可靠）
osascript -e 'tell application "WeChat" to reopen'
sleep 0.3
osascript -e 'tell application "WeChat" to activate'
sleep 0.2

# 确保窗口未最小化（最多2次尝试）
osascript <<'OSA' 2>/dev/null
tell application "System Events"
  tell process "WeChat"
    repeat 2 times
      if (count of windows) > 0 then
        try
          set miniaturized of window 1 to false
          exit repeat
        end try
      end if
      delay 0.2
    end repeat
  end tell
end tell
OSA
sleep 0.3

# 获取窗口位置和大小
rect="$(wechat_get_window_rect)"
read -r _win_x _win_y _win_w _win_h <<<"$(wechat_parse_rect "$rect")"

# 检查窗口大小，如果太小（看不到搜索框），调整到合适大小
min_width=700
min_height=500
if [ "$_win_w" -lt "$min_width" ] || [ "$_win_h" -lt "$min_height" ]; then
  echo "窗口过小 (${_win_w}x${_win_h})，调整到 ${min_width}x${min_height}"
  osascript <<OSA
tell application "System Events"
  tell process "WeChat"
    set size of window 1 to {$min_width, $min_height}
  end tell
end tell
OSA
  sleep 0.3
  rect="$(wechat_get_window_rect)"
  read -r _win_x _win_y _win_w _win_h <<<"$(wechat_parse_rect "$rect")"
fi

# 按ESC关闭任何弹窗
osascript -e 'tell application "System Events" to tell process "WeChat" to key code 53' 2>/dev/null
sleep 0.1

# 点击左侧导航栏"微信"图标，切回聊天页面
wechat_icon_x=$((_win_x + 26))
wechat_icon_y=$((_win_y + 110))
wechat_click_at "$wechat_icon_x" "$wechat_icon_y"
sleep 0.4

echo "=== 步骤2: Cmd+F 搜索并选中: $chat_name ==="
osascript - "$chat_name" <<'OSA'
on run argv
  set chatName to item 1 of argv
  tell application "WeChat" to activate
  delay 0.1
  tell application "System Events"
    tell process "WeChat"
      keystroke "f" using {command down}
      delay 0.3
      keystroke "a" using {command down}
      delay 0.1
      key code 51  -- Delete
      delay 0.1
      set the clipboard to chatName
      keystroke "v" using {command down}
      delay 0.6
      key code 36  -- 回车选中第一个结果
      delay 0.6
    end tell
  end tell
end run
OSA

echo "=== 步骤3: OCR验证当前选中会话 ==="
verify_shot="$(mktemp -t wechat-verify).png"
screencapture -x -R"${_win_x},${_win_y},${_win_w},${_win_h}" "$verify_shot"

title_match="$(
  # 校验锚点 = 左侧会话列表“当前选中并置顶的那一行”（Cmd+F 回车后目标会话会被置顶选中）。
  # 实测 Vision OCR 读不到顶部浅色会话标题，只稳定读到列表行；区域只框第一行，避开搜索框与第二行。
  "$script_dir/ocr_wechat_screenshot.sh" --json --region 0.02 0.90 0.15 0.08 "$verify_shot" | \
    CHAT_NAME="$chat_name" python3 -c '
import json, os, sys
target = os.environ["CHAT_NAME"].replace(" ", "")
items = json.load(sys.stdin)
for item in items:
    text = item["text"].replace(" ", "")
    if text == target or target in text:
        print(item["text"])
        break
'
)"
if [ -z "$title_match" ]; then
  echo "❌ 验证失败：OCR 未在左侧选中会话行识别到 '$chat_name'（可能没进对会话，已中止发送）" >&2
  echo "截图已保存: $verify_shot" >&2
  exit 1
fi
echo "✅ 验证通过：当前选中会话 = $title_match"
rm -f "$verify_shot"

echo "=== 步骤4: 点击输入框 ==="
chat_area_x=$((_win_x + 232))
chat_area_w=$((_win_w - 232))
composer_x=$((chat_area_x + chat_area_w / 3))
composer_y=$((_win_y + _win_h - 55))
wechat_click_at "$composer_x" "$composer_y"
sleep 0.3

echo "=== 步骤5: 粘贴消息内容 ==="
echo -n "$message" | pbcopy
osascript <<'OSA'
tell application "WeChat" to activate
delay 0.1
tell application "System Events"
  tell process "WeChat"
    -- 焦点已在输入框：先全选清空旧草稿/残留，再粘贴，避免新旧内容混在一起
    keystroke "a" using {command down}
    delay 0.1
    key code 51
    delay 0.1
    keystroke "v" using {command down}
  end tell
end tell
delay 0.3
OSA

echo "=== 步骤6: 按回车发送 ==="
osascript -e 'tell application "System Events" to tell process "WeChat" to key code 36'
sleep 0.6

if [ "$skip_verify" = "--no-verify" ]; then
  echo "=== 步骤7: 跳过发送后验证（--no-verify）==="
else
  echo "=== 步骤7: 验证发送结果 ==="
  result_shot="$(mktemp -t wechat-result).png"
  screencapture -x -R"${_win_x},${_win_y},${_win_w},${_win_h}" "$result_shot"

  message_preview="$(echo -n "$message" | head -c 15)"
  send_verify="$(
    "$script_dir/ocr_wechat_screenshot.sh" --json --region 0.20 0.15 0.75 0.70 "$result_shot" | \
      MESSAGE_PREVIEW="$message_preview" python3 -c '
import json, os, sys
target = os.environ["MESSAGE_PREVIEW"].replace(" ", "")
items = json.load(sys.stdin)
for item in items:
    text = item["text"].replace(" ", "")
    if target in text:
        print(item["text"])
        break
'
  )"
  if [ -n "$send_verify" ]; then
    echo "✅ 发送成功！聊天区域出现消息: $send_verify"
  else
    echo "⚠️  未在聊天区域识别到发送的消息（可能OCR未识别到，不代表发送失败）"
    echo "截图已保存: $result_shot"
  fi
fi

echo ""
echo "=== 发送完成 ==="
echo "对象: $chat_name"
echo "消息: $message"
