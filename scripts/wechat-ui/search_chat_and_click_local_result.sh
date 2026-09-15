#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
chat_name="${1:-}"
WECHAT_SKILL_DIR="$script_dir"
# shellcheck source=lib_wechat_ui.sh
. "$script_dir/lib_wechat_ui.sh"

if [ -z "$chat_name" ]; then
  echo "Usage: $0 <chat_name>" >&2
  exit 1
fi

"$script_dir/prepare_wechat_viewport.sh"
open -a WeChat

# 先获取窗口位置（用于计算左侧导航栏图标坐标）
rect="$(wechat_get_window_rect)"
if ! wechat_parse_rect "$rect" >/dev/null; then
  echo "Could not parse WeChat window bounds: $rect" >&2
  exit 1
fi
read -r _win_x _win_y _win_w _win_h <<<"$(wechat_parse_rect "$rect")"

# 健壮性步骤1：按ESC关闭任何弹窗/搜索结果
osascript -e 'tell application "System Events" to tell process "WeChat" to key code 53' 2>/dev/null
sleep 0.3

# 健壮性步骤2：点击左侧导航栏"微信"图标，切回聊天页面
# 左侧导航栏图标位置（相对于窗口左上角，需包含标题栏高度约38px）：
#   头像中心: y ≈ 76（标题栏38 + 头像顶部间距28 + 头像半径10）
#   微信(聊天)图标中心: y ≈ 110（标题栏38 + 头像下方间距 + 图标半径）
#   通讯录图标中心: y ≈ 150
#   收藏图标中心: y ≈ 190
# 导航栏宽度约 52px，图标中心 x ≈ 26
wechat_icon_x=$((_win_x + 26))
wechat_icon_y=$((_win_y + 110))
wechat_click_at "$wechat_icon_x" "$wechat_icon_y"
sleep 0.6

# 健壮性步骤3：Cmd+F 打开搜索 → Cmd+A 全选清空 → 粘贴新内容
osascript - "$chat_name" <<'OSA'
on run argv
  set chatName to item 1 of argv
  tell application "WeChat" to activate
  delay 0.2
  tell application "System Events"
    tell process "WeChat"
      -- 打开搜索
      keystroke "f" using {command down}
      delay 0.5
      -- 全选搜索框现有内容（处理残留/重复输入）
      keystroke "a" using {command down}
      delay 0.2
      -- 删除清空
      key code 51  -- Delete
      delay 0.2
      -- 粘贴新的搜索内容
      set the clipboard to chatName
      keystroke "v" using {command down}
      delay 1.0
    end tell
  end tell
end run
OSA

# 用同一个 rect 截图（确保 OCR 区域和坐标转换基准一致）
read -r _win_x _win_y _win_w _win_h <<<"$(wechat_parse_rect "$rect")"
shot_file="$(mktemp -t wechat-search).png"
screencapture -x -R"${_win_x},${_win_y},${_win_w},${_win_h}" "$shot_file"

# OCR 裁剪区域参数（left, bottom, width, height，均为窗口归一化坐标，y从底部起算）
# 注意：bottom+height 不要超过 0.90，避免包含搜索框（搜索框在窗口顶部约 10% 区域）
# 否则 OCR 会把搜索框里的输入文字识别为搜索结果，导致点击点到搜索框
REGION_LEFT=0.00
REGION_BOTTOM=0.30
REGION_WIDTH=0.30
REGION_HEIGHT=0.58

coords="$(
  "$script_dir/ocr_wechat_screenshot.sh" --json --region $REGION_LEFT $REGION_BOTTOM $REGION_WIDTH $REGION_HEIGHT "$shot_file" | \
    CHAT_NAME="$chat_name" REGION_LEFT="$REGION_LEFT" REGION_BOTTOM="$REGION_BOTTOM" REGION_WIDTH="$REGION_WIDTH" REGION_HEIGHT="$REGION_HEIGHT" python3 -c '
import json
import os
import sys

target = os.environ["CHAT_NAME"].replace(" ", "")
items = json.load(sys.stdin)

# 裁剪区域在窗口中的位置（用于把OCR坐标转回窗口坐标）
r_left = float(os.environ["REGION_LEFT"])
r_bottom = float(os.environ["REGION_BOTTOM"])
r_width = float(os.environ["REGION_WIDTH"])
r_height = float(os.environ["REGION_HEIGHT"])

def to_window_coords(ox, oy, ow, oh):
    """把相对于裁剪区域的坐标转换为相对于整个窗口的坐标（y均从底部起算）"""
    wx = r_left + ox * r_width
    wy = r_bottom + oy * r_height
    ww = ow * r_width
    wh = oh * r_height
    return wx, wy, ww, wh

local_headers = {"群聊", "联系人", "聊天记录", "公众号", "服务号", "小程序", "功能"}
local_header_y = None

for item in items:
    compact = item["text"].replace(" ", "")
    if compact in local_headers:
        local_header_y = item["y"] if local_header_y is None else max(local_header_y, item["y"])

local_candidates = []
fallback_candidates = []

for item in items:
    text = item["text"].replace(" ", "")
    if not text:
        continue

    score = None
    if text == target:
        score = 0
    elif text.startswith(target):
        score = 1
    elif target in text:
        score = 2

    if score is None:
        continue

    candidate = (score, -item["y"], item["x"], item)
    fallback_candidates.append(candidate)

    if local_header_y is not None and item["y"] < (local_header_y - 0.003):
        local_candidates.append(candidate)

if local_candidates:
    _, _, _, item = sorted(local_candidates)[0]
    # 行点击区域（相对于裁剪区域）
    row_x = 0.03
    row_w = 0.24
    row_h = max(0.032, item["h"] * 2.6)
    row_y = max(0.0, item["y"] - (row_h - item["h"]) / 2.0)
    # 转换为窗口坐标
    wx, wy, ww, wh = to_window_coords(row_x, row_y, row_w, row_h)
    print("{x},{y},{w},{h}".format(x=wx, y=wy, w=ww, h=wh))
    sys.exit(0)

if fallback_candidates:
    _, _, _, item = sorted(fallback_candidates)[0]
    wx, wy, ww, wh = to_window_coords(item["x"], item["y"], item["w"], item["h"])
    print("{x},{y},{w},{h}".format(x=wx, y=wy, w=ww, h=wh))
    sys.exit(0)

sys.exit(0)
'
)"

if [ -z "$coords" ]; then
  echo "Could not find local search result for: $chat_name" >&2
  exit 1
fi

set -- $(printf '%s\n' "$coords" | tr ',' ' ')
norm_x="$1"
norm_y="$2"
norm_w="$3"
norm_h="$4"

read -r click_x click_y <<<"$(wechat_click_norm_coords "$rect" "$norm_x" "$norm_y" "$norm_w" "$norm_h")"
printf 'FOUND %s %s %s\n' "$chat_name" "$click_x" "$click_y"
