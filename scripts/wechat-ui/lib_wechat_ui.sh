#!/usr/bin/env bash

wechat_get_window_rect() {
  osascript <<'OSA'
tell application "System Events"
  tell process "WeChat"
    set targetWindow to missing value
    repeat with w in windows
      try
        if name of w is "微信" then
          set targetWindow to w
          exit repeat
        end if
      end try
    end repeat
    if targetWindow is missing value then
      set targetWindow to window 1
    end if
    set {x, y} to position of targetWindow
    set {w0, h0} to size of targetWindow
    return (x as integer) & "," & (y as integer) & "," & (w0 as integer) & "," & (h0 as integer)
  end tell
end tell
OSA
}

wechat_parse_rect() {
  local rect="$1"
  set -- $(printf '%s\n' "$rect" | grep -Eo '[0-9]+')
  if [ "$#" -lt 4 ]; then
    return 1
  fi
  printf '%s %s %s %s\n' "$1" "$2" "$3" "$4"
}

wechat_click_at() {
  local click_x="$1"
  local click_y="$2"

  osascript -l JavaScript <<JXA >/dev/null
ObjC.import('CoreGraphics')
ObjC.import('Foundation')

function mouseEvent(type, x, y) {
  return $.CGEventCreateMouseEvent(null, type, {x:x, y:y}, $.kCGMouseButtonLeft)
}

function post(event) {
  $.CGEventPost($.kCGHIDEventTap, event)
}

const clickX = Number($click_x)
const clickY = Number($click_y)

post(mouseEvent($.kCGEventMouseMoved, clickX, clickY))
$.NSThread.sleepForTimeInterval(0.05)
post(mouseEvent($.kCGEventLeftMouseDown, clickX, clickY))
post(mouseEvent($.kCGEventLeftMouseUp, clickX, clickY))
JXA
}

wechat_click_norm_coords() {
  local rect="$1"
  local norm_x="$2"
  local norm_y="$3"
  local norm_w="$4"
  local norm_h="$5"

  local parsed
  if ! parsed="$(wechat_parse_rect "$rect")"; then
    return 1
  fi

  local win_x win_y win_w win_h
  read -r win_x win_y win_w win_h <<<"$parsed"

  local click_x click_y
  click_x="$(python3 -c 'import sys; win_x, win_w, x, w = map(float, sys.argv[1:5]); print(int(round(win_x + (x + w / 2.0) * win_w)))' "$win_x" "$win_w" "$norm_x" "$norm_w")"
  click_y="$(python3 -c 'import sys; win_y, win_h, y, h = map(float, sys.argv[1:5]); print(int(round(win_y + (1.0 - (y + h / 2.0)) * win_h)))' "$win_y" "$win_h" "$norm_y" "$norm_h")"

  wechat_click_at "$click_x" "$click_y"
  printf '%s %s\n' "$click_x" "$click_y"
}

wechat_ocr_find_coords() {
  local image_path="$1"
  local region_left="$2"
  local region_bottom="$3"
  local region_width="$4"
  local region_height="$5"
  local target="$6"

  if [ -z "${WECHAT_SKILL_DIR:-}" ]; then
    echo "WECHAT_SKILL_DIR must be set before calling wechat_ocr_find_coords" >&2
    return 1
  fi

  "$WECHAT_SKILL_DIR/ocr_wechat_screenshot.sh" --json --region "$region_left" "$region_bottom" "$region_width" "$region_height" "$image_path" | \
    TARGET_TEXT="$target" python3 -c '
import json
import os
import sys

target = os.environ["TARGET_TEXT"].replace(" ", "")
items = json.load(sys.stdin)
candidates = []

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
    candidates.append((score, -item["y"], item["x"], item))

if not candidates:
    sys.exit(0)

_, _, _, item = sorted(candidates)[0]
print("{x},{y},{w},{h}".format(x=item["x"], y=item["y"], w=item["w"], h=item["h"]))
'
}

wechat_ocr_contains_text() {
  local image_path="$1"
  local region_left="$2"
  local region_bottom="$3"
  local region_width="$4"
  local region_height="$5"
  local target="$6"

  if [ -z "${WECHAT_SKILL_DIR:-}" ]; then
    echo "WECHAT_SKILL_DIR must be set before calling wechat_ocr_contains_text" >&2
    return 1
  fi

  "$WECHAT_SKILL_DIR/ocr_wechat_screenshot.sh" --json --region "$region_left" "$region_bottom" "$region_width" "$region_height" "$image_path" | \
    TARGET_TEXT="$target" python3 -c '
import json
import os
import sys

target = os.environ["TARGET_TEXT"].replace(" ", "")
items = json.load(sys.stdin)

for item in items:
    text = item["text"].replace(" ", "")
    if not text:
        continue
    if text == target or text.startswith(target) or target in text:
        sys.exit(0)

sys.exit(1)
'
}
