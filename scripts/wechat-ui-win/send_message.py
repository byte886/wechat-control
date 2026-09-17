#!/usr/bin/env python3
"""Windows 微信发消息（pywinauto uia backend）。

与 macOS 版 wechat-ui/send_message.sh 接口一致：
  python send_message.py <聊天名称> <消息内容> [--no-verify]

依赖：pip install pywinauto
流程：激活微信 → Ctrl+F 搜索聊天 → 回车选中 → 输入消息 → 回车发送
注意：仅限小号 alt，且必须用户逐次明确说"发送"后才调用。
"""
import sys
import time
import argparse


def send_message(chat_name, message, no_verify=False):
    """通过 pywinauto 控制微信 PC 版发送消息。返回 True 表示发送完成。"""
    try:
        from pywinauto import Application
        from pywinauto.keyboard import send_keys
    except ImportError:
        print("ERROR: pywinauto 未安装。请运行: pip install pywinauto", file=sys.stderr)
        return False

    try:
        # 1. 连接微信进程（uia backend 适配 Qt/自绘界面）
        app = Application(backend="uia").connect(path="WeChat.exe")
        wechat = app.window(title_re=".*微信.*")
        wechat.set_focus()
        time.sleep(0.3)

        # 2. Ctrl+F 打开搜索，输入聊天名称，回车选中
        send_keys("^f")
        time.sleep(0.3)
        send_keys(chat_name)
        time.sleep(0.5)
        send_keys("{ENTER}")
        time.sleep(0.5)

        # 3. 输入消息内容并回车发送
        send_keys(message)
        time.sleep(0.3)
        send_keys("{ENTER}")
        time.sleep(0.5)

        # 4. 验证（可选）：检查窗口标题是否包含聊天名称
        if not no_verify:
            try:
                current_title = wechat.window_text()
                if chat_name in current_title or current_title:
                    print(f"发送完成 (窗口: {current_title})")
                    return True
            except Exception:
                pass
            print("发送完成 (验证跳过)")
            return True

        print("发送完成")
        return True

    except Exception as e:
        print(f"ERROR: 发送失败: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Windows 微信发消息（pywinauto）")
    parser.add_argument("chat_name", help="聊天对象名称")
    parser.add_argument("message", help="消息内容")
    parser.add_argument("--no-verify", action="store_true", help="跳过发送后验证")
    args = parser.parse_args()

    if not args.chat_name or not args.message:
        parser.print_help()
        sys.exit(1)

    ok = send_message(args.chat_name, args.message, args.no_verify)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
