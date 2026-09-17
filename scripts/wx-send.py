#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WeChat message sender (write operations)"""

import argparse
import json
import platform
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

CONFIG_DIR = Path.home() / ".wx-cli"
AUTO_REPLY_CONFIG = CONFIG_DIR / "auto_reply_config.json"
AUTO_REPLY_LOG = CONFIG_DIR / "auto_reply_log.jsonl"
AUTO_REPLY_STATE = CONFIG_DIR / "auto_reply_state.json"

BLACKLIST = [
    (r"(\u8f6c\u8d26|\u4ed8\u6b3e|\u7ea2\u5305|\u6253\u6b3e|\u6c47\u6b3e|\u652f\u4ed8\u5b9d|\u5fae\u4fe1\u652f\u4ed8|\u94f6\u884c\u5361|\u91d1\u989d|\u591a\u5c11\u94b1|\u4ef7\u683c|\u62a5\u4ef7)", "\u91d1\u94b1\u76f8\u5173"),
    (r"(\u5bc6\u7801|\u9a8c\u8bc1\u7801|\u8eab\u4efd\u8bc1|\u624b\u673a\u53f7|\u7535\u8bdd\u53f7\u7801|\u5bb6\u5ead\u4f4f\u5740|\u4e2a\u4eba\u4fe1\u606f|\u94f6\u884c\u5361\u53f7)", "\u9690\u79c1\u654f\u611f"),
    (r"(\u6211\u4fdd\u8bc1|\u4e00\u5b9a|\u80af\u5b9a|\u7edd\u5bf9|\u6ca1\u95ee\u9898|\u5305\u5728\u6211\u8eab\u4e0a|\u8d1f\u8d23\u5230\u5e95|\u7edd\u4e0d\u53cd\u6094)", "\u627f\u8bfa\u4fdd\u8bc1"),
    (r"(\u653f\u6cbb|\u9886\u5bfc\u4eba|\u516d\u56db|\u5929\u5b89\u95e8|\u53f0\u72ec|\u6e2f\u72ec|\u7586\u72ec|\u85cf\u72ec|\u8272\u60c5|\u9ec4\u8272|\u66b4\u529b|\u8d4c\u535a|\u6bd2\u54c1|\u67aa|\u70b8\u836f)", "\u654f\u611f\u5185\u5bb9"),
    (r"(\u50bb\u903c|\u64cd\u4f60|\u5988\u7684|\u8349\u6ce5\u9a6c|\u8d31\u4eba|\u5e9f\u7269|\u5783\u573e|\u6eda\u86cb|\u53bb\u6b7b|\u767d\u75f4|\u5f31\u667a|\u6b67\u89c6|\u79cd\u65cf)", "\u8fb1\u9a82\u653b\u51fb"),
    (r"(\u673a\u5bc6|\u7edd\u5bc6|\u5185\u90e8\u8d44\u6599|\u672a\u516c\u5f00|\u5546\u4e1a\u79d8\u5bc6|\u6838\u5fc3\u6570\u636e|\u6e90\u4ee3\u7801|\u7b97\u6cd5|\u8d22\u52a1\u62a5\u8868)", "\u516c\u53f8\u673a\u5bc6"),
    (r"(\u8bca\u65ad|\u6cbb\u7597|\u5403\u836f|\u624b\u672f|\u764c\u75c7|\u827e\u6ecb|\u6cd5\u5f8b\u610f\u89c1|\u8d77\u8bc9|\u5f8b\u5e08|\u6295\u8d44\u5efa\u8bae|\u7092\u80a1|\u7406\u8d22|\u4fdd\u672c)", "\u4e13\u4e1a\u5efa\u8bae"),
    (r"(\u6211\u662f\u672c\u4eba|\u662f\u6211\u554a|\u4f60\u8fde\u6211\u90fd\u4e0d\u8ba4\u8bc6|\u542c\u4e0d\u51fa\u6211\u58f0\u97f3|\u5192\u5145|\u5047\u626e)", "\u8eab\u4efd\u5192\u5145"),
    (r"(\u6211\u662fAI|\u4eba\u5de5\u667a\u80fd|\u673a\u5668\u4eba|\u81ea\u52a8\u56de\u590d|\u7a0b\u5e8f|\u811a\u672c|\u81ea\u52a8\u5316)", "\u66b4\u9732AI"),
    (r"(\u4f60\u662f\u8c01|\u4f60\u600e\u4e48\u77e5\u9053|\u4f60\u5728\u54ea|\u4f60\u53eb\u4ec0\u4e48|\u4f60\u662f\u4eba\u5417)", "\u53cd\u95ee\u8d28\u7591"),
]


def run_cmd(cmd, timeout=30):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def send_message(to, message, confirm=True, no_verify=False):
    """发送消息到指定聊天对象。按操作系统选择后端：
    - macOS: wechat-ui/send_message.sh（AppleScript + 辅助功能）
    - Windows: wechat-ui-win/send_message.py（pywinauto uia backend）
    """
    result = {
        "success": False, "recipient": to, "message": message,
        "timestamp": datetime.now().isoformat(), "error": None
    }
    blocked, reason = check_blacklist(message)
    if blocked:
        result["error"] = f"blocked by blacklist: {reason}"
        return result
    if confirm:
        result["pending"] = True
        result["confirm_required"] = True
        return result
    try:
        system = platform.system()
        if system == "Darwin":
            script_path = Path(__file__).parent / "wechat-ui" / "send_message.sh"
            # 用 list 参数 + shell=False，避免 to/message 含引号或 $() 时的 shell 注入
            cmd_args = ["bash", str(script_path), to, message]
        elif system == "Windows":
            script_path = Path(__file__).parent / "wechat-ui-win" / "send_message.py"
            cmd_args = [sys.executable, str(script_path), to, message]
        else:
            result["error"] = f"unsupported platform: {system}"
            return result
        if no_verify:
            cmd_args.append("--no-verify")
        proc = subprocess.run(cmd_args, capture_output=True, text=True, timeout=60)
        code, stdout, stderr = proc.returncode, proc.stdout.strip(), proc.stderr.strip()
        if code == 0 and "发送完成" in stdout:
            result["success"] = True
            result["stdout"] = stdout[-500:]
            return result
        result["error"] = f"send failed (code={code}): {stderr[-200:] if stderr else stdout[-200:]}"
        return result
    except Exception as e:
        result["error"] = str(e)
        return result


def check_blacklist(text):
    for pattern, reason in BLACKLIST:
        if re.search(pattern, text, re.IGNORECASE):
            return True, reason
    return False, None


def load_config():
    if AUTO_REPLY_CONFIG.exists():
        return json.loads(AUTO_REPLY_CONFIG.read_text())
    return {
        "enabled": False, "keywords": [], "specific_contacts": [],
        "specific_groups": [], "blacklist_contacts": [],
        "rate_limit": {"per_minute": 3, "per_hour": 20, "per_contact_5min": 2},
        "night_mode": {"enabled": True, "start": "23:00", "end": "08:00"}
    }


def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    AUTO_REPLY_CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2))


def load_state():
    if AUTO_REPLY_STATE.exists():
        return json.loads(AUTO_REPLY_STATE.read_text())
    return {
        "running": False, "minute_count": 0, "hour_count": 0,
        "minute_start": None, "hour_start": None,
        "contact_counts": {}, "paused": False
    }


def save_state(state):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    AUTO_REPLY_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def append_log(entry):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(AUTO_REPLY_LOG, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def is_night_time(config):
    if not config.get("night_mode", {}).get("enabled", True):
        return False
    now = datetime.now().time()
    start = datetime.strptime(config["night_mode"]["start"], "%H:%M").time()
    end = datetime.strptime(config["night_mode"]["end"], "%H:%M").time()
    if start < end:
        return start <= now <= end
    return now >= start or now <= end


def check_rate_limit(state, config, contact_id):
    now = datetime.now()
    if state.get("minute_start"):
        ms = datetime.fromisoformat(state["minute_start"])
        if (now - ms).total_seconds() > 60:
            state["minute_count"] = 0
            state["minute_start"] = now.isoformat()
    else:
        state["minute_start"] = now.isoformat()
    if state.get("hour_start"):
        hs = datetime.fromisoformat(state["hour_start"])
        if (now - hs).total_seconds() > 3600:
            state["hour_count"] = 0
            state["hour_start"] = now.isoformat()
    else:
        state["hour_start"] = now.isoformat()
    if state["minute_count"] >= config["rate_limit"]["per_minute"]:
        return False, "per minute limit"
    if state["hour_count"] >= config["rate_limit"]["per_hour"]:
        return False, "per hour limit"
    cc = state.get("contact_counts", {})
    if contact_id in cc:
        ci = cc[contact_id]
        cs = datetime.fromisoformat(ci["start"])
        if (now - cs).total_seconds() <= 300:
            if ci["count"] >= config["rate_limit"]["per_contact_5min"]:
                return False, "per contact 5min limit"
        else:
            cc[contact_id] = {"count": 0, "start": now.isoformat()}
    return True, "ok"


def increment_rate(state, contact_id):
    now = datetime.now()
    state["minute_count"] = state.get("minute_count", 0) + 1
    state["hour_count"] = state.get("hour_count", 0) + 1
    cc = state.get("contact_counts", {})
    if contact_id not in cc:
        cc[contact_id] = {"count": 0, "start": now.isoformat()}
    cc[contact_id]["count"] += 1
    state["contact_counts"] = cc


def should_reply(message, sender, is_group, config):
    if sender in config.get("blacklist_contacts", []):
        return False, "contact in blacklist"
    if sender in config.get("specific_contacts", []):
        return True, "match specific contact"
    if is_group and sender in config.get("specific_groups", []):
        return True, "match specific group"
    for kw in config.get("keywords", []):
        if kw in message:
            return True, f"match keyword: {kw}"
    return False, "no match"


def generate_reply(message, sender, is_group):
    """生成回复内容。当前使用模板回复，后续可接入AI生成。"""
    templates = [
        "收到，我现在有点忙，稍后回复你。",
        "好的，我看到了，稍等一下。",
        "明白了，我来处理一下。",
        "OK，收到，稍后联系你。",
    ]
    msg_lower = message.lower()
    if "在吗" in message or "在么" in message or "在不" in message:
        return "在的，有什么事吗？"
    elif "谢谢" in message or "感谢" in message:
        return "不客气。"
    elif "好的" in message or "嗯" in message or "ok" in msg_lower:
        return "好的，有需要再联系。"
    else:
        return templates[hash(message) % len(templates)]


def auto_reply_loop(interval=60):
    """自动回复主循环：轮询新消息 → 判定 → 生成回复 → 发送 → 记录日志。"""
    config = load_config()
    state = load_state()
    replied_ids = set(state.get("replied_ids", []))
    print(f"Auto-reply loop started (interval={interval}s)")
    print(f"  Keywords: {config.get('keywords', [])}")
    print(f"  Specific contacts: {config.get('specific_contacts', [])}")
    print(f"  Specific groups: {config.get('specific_groups', [])}")
    print("Press Ctrl+C to stop")
    print("-" * 60)

    try:
        while True:
            config = load_config()
            state = load_state()
            if not config.get("enabled"):
                print("Auto-reply disabled, exiting")
                break
            if state.get("paused"):
                time.sleep(interval)
                continue
            if is_night_time(config):
                print("Night mode, sleeping...")
                time.sleep(interval)
                continue

            # 获取新消息
            code, stdout, stderr = run_cmd("wx new-messages --json 2>/dev/null || wx new-messages", timeout=30)
            if code != 0 or not stdout.strip():
                time.sleep(interval)
                continue

            try:
                messages = json.loads(stdout)
                if isinstance(messages, dict):
                    messages = messages.get("messages", [])
            except (json.JSONDecodeError, TypeError):
                # 非JSON输出，跳过
                time.sleep(interval)
                continue

            for msg in messages:
                msg_id = msg.get("id") or msg.get("msgId") or f"{msg.get('sender','')}_{msg.get('timestamp','')}"
                if msg_id in replied_ids:
                    continue
                sender = msg.get("sender") or msg.get("from") or "unknown"
                content = msg.get("content") or msg.get("message") or ""
                is_group = "@chatroom" in sender or msg.get("is_group", False)
                chat_name = msg.get("chat_name") or msg.get("talker") or sender

                # 判断是否需要回复
                should, reason = should_reply(content, sender, is_group, config)
                if not should:
                    continue

                # 频率限制
                ok, rate_reason = check_rate_limit(state, config, sender)
                if not ok:
                    print(f"Rate limited ({rate_reason}), skipping: {sender}")
                    continue

                # 生成回复
                reply = generate_reply(content, sender, is_group)

                # 检查回复内容黑名单
                blocked, block_reason = check_blacklist(reply)
                if blocked:
                    log_entry = {
                        "timestamp": datetime.now().isoformat(),
                        "sender": sender, "original_message": content,
                        "reply_message": reply, "success": False,
                        "blocked": True, "block_reason": block_reason,
                    }
                    append_log(log_entry)
                    replied_ids.add(msg_id)
                    continue

                # 发送回复
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Replying to {chat_name}: {reply[:30]}")
                result = send_message(chat_name, reply, confirm=False, no_verify=True)
                if result.get("success"):
                    increment_rate(state, sender)
                    log_entry = {
                        "timestamp": datetime.now().isoformat(),
                        "sender": sender, "original_message": content,
                        "reply_message": reply, "success": True,
                        "match_reason": reason,
                    }
                    append_log(log_entry)
                    print(f"  ✓ Sent successfully")
                else:
                    log_entry = {
                        "timestamp": datetime.now().isoformat(),
                        "sender": sender, "original_message": content,
                        "reply_message": reply, "success": False,
                        "error": result.get("error", "unknown"),
                    }
                    append_log(log_entry)
                    print(f"  ✗ Failed: {result.get('error', 'unknown')[:80]}")

                replied_ids.add(msg_id)
                # 保存已回复ID（最多保留1000条）
                state["replied_ids"] = list(replied_ids)[-1000:]
                save_state(state)
                time.sleep(3)  # 每条消息间隔3秒，避免频率过高

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nAuto-reply loop stopped by user")
    finally:
        state["replied_ids"] = list(replied_ids)[-1000:]
        save_state(state)


def cmd_send(args):
    to = args.to
    message = args.message
    print(f"Preparing to send message")
    print(f"  To: {to}")
    print(f"  Message: {message}")
    print()
    blocked, reason = check_blacklist(message)
    if blocked:
        print(f"Blocked by blacklist: {reason}, not sending")
        return
    if args.yes:
        print("Skipping confirmation, sending directly (test only)")
        result = send_message(to, message, confirm=False)
    else:
        print("Please confirm: reply 'send' to execute, 'cancel' to abort, 'change: new content' to modify")
        result = {"pending": True, "confirm_required": True}
    if result.get("success"):
        print(f"Sent successfully! Window: {result.get('window_title', 'unknown')}")
        print("Can recall within 2 minutes")
    elif result.get("pending"):
        print("Pending confirmation, waiting for user to say 'send'")
    else:
        print(f"Send failed: {result.get('error', 'unknown error')}")


def cmd_auto_reply(args):
    subcmd = args.subcmd
    if subcmd == "start":
        config = load_config()
        config["enabled"] = True
        save_config(config)
        state = load_state()
        state["running"] = True
        state["paused"] = False
        save_state(state)
        print("Auto-reply started")
        print(f"  Keywords: {config.get('keywords', [])}")
        print(f"  Specific contacts: {config.get('specific_contacts', [])}")
        print(f"  Specific groups: {config.get('specific_groups', [])}")
    elif subcmd == "stop":
        config = load_config()
        config["enabled"] = False
        save_config(config)
        state = load_state()
        state["running"] = False
        save_state(state)
        print("Auto-reply stopped")
    elif subcmd == "pause":
        state = load_state()
        state["paused"] = True
        save_state(state)
        print("Auto-reply paused (say 'resume' to continue)")
    elif subcmd == "resume":
        state = load_state()
        state["paused"] = False
        save_state(state)
        print("Auto-reply resumed")
    elif subcmd == "status":
        config = load_config()
        state = load_state()
        print("Auto-reply status")
        print(f"  Enabled: {'yes' if config.get('enabled') else 'no'}")
        print(f"  Running: {'yes' if state.get('running') else 'no'}")
        print(f"  Paused: {'yes' if state.get('paused') else 'no'}")
        print(f"  This minute: {state.get('minute_count', 0)}/{config['rate_limit']['per_minute']}")
        print(f"  This hour: {state.get('hour_count', 0)}/{config['rate_limit']['per_hour']}")
        print(f"  Keywords: {config.get('keywords', [])}")
        print(f"  Specific contacts: {config.get('specific_contacts', [])}")
        print(f"  Specific groups: {config.get('specific_groups', [])}")
    elif subcmd == "config":
        action = args.action
        config = load_config()
        if action == "add-keyword":
            kw = args.value
            if kw not in config["keywords"]:
                config["keywords"].append(kw)
                save_config(config)
                print(f"Added keyword: {kw}")
            else:
                print(f"Keyword already exists: {kw}")
        elif action == "remove-keyword":
            kw = args.value
            if kw in config["keywords"]:
                config["keywords"].remove(kw)
                save_config(config)
                print(f"Removed keyword: {kw}")
            else:
                print(f"Keyword not found: {kw}")
        elif action == "add-contact":
            c = args.value
            if c not in config["specific_contacts"]:
                config["specific_contacts"].append(c)
                save_config(config)
                print(f"Added specific contact: {c}")
            else:
                print(f"Contact already exists: {c}")
        elif action == "add-group":
            g = args.value
            if g not in config["specific_groups"]:
                config["specific_groups"].append(g)
                save_config(config)
                print(f"Added specific group: {g}")
            else:
                print(f"Group already exists: {g}")
        elif action == "list":
            print(f"Keywords: {config.get('keywords', [])}")
            print(f"Specific contacts: {config.get('specific_contacts', [])}")
            print(f"Specific groups: {config.get('specific_groups', [])}")
            print(f"Blacklist contacts: {config.get('blacklist_contacts', [])}")
    elif subcmd == "run":
        interval = args.interval if hasattr(args, 'interval') else 60
        config = load_config()
        if not config.get("enabled"):
            config["enabled"] = True
            save_config(config)
        state = load_state()
        state["running"] = True
        state["paused"] = False
        save_state(state)
        auto_reply_loop(interval=interval)
    elif subcmd == "log":
        limit = args.limit if hasattr(args, 'limit') else 20
        if AUTO_REPLY_LOG.exists():
            lines = AUTO_REPLY_LOG.read_text().strip().split("\n")
            recent = lines[-limit:] if len(lines) > limit else lines
            print(f"Auto-reply log (recent {len(recent)} entries)")
            print("-" * 60)
            for line in recent:
                try:
                    entry = json.loads(line)
                    status = "OK" if entry.get("success") else "BLOCKED"
                    print(f"[{status}] {entry.get('timestamp', '')[:19]}")
                    print(f"  Sender: {entry.get('sender', 'unknown')}")
                    print(f"  Original: {entry.get('original_message', '')[:50]}")
                    print(f"  Reply: {entry.get('reply_message', '')[:50]}")
                    if entry.get("blocked"):
                        print(f"  Block reason: {entry.get('block_reason', '')}")
                    print()
                except (json.JSONDecodeError, OSError):
                    print(line)
        else:
            print("No log entries")


def main():
    parser = argparse.ArgumentParser(description="WeChat message sender")
    subparsers = parser.add_subparsers(dest="command", help="command")
    send_parser = subparsers.add_parser("send", help="send message")
    send_parser.add_argument("--to", required=True, help="recipient name")
    send_parser.add_argument("--message", required=True, help="message content")
    send_parser.add_argument("--yes", action="store_true", help="skip confirmation (test only)")
    ar_parser = subparsers.add_parser("auto-reply", help="auto reply")
    ar_subparsers = ar_parser.add_subparsers(dest="subcmd", help="subcommand")
    ar_subparsers.add_parser("start", help="start auto-reply")
    ar_subparsers.add_parser("stop", help="stop auto-reply")
    ar_subparsers.add_parser("pause", help="pause auto-reply")
    ar_subparsers.add_parser("resume", help="resume auto-reply")
    ar_subparsers.add_parser("status", help="view status")
    run_parser = ar_subparsers.add_parser("run", help="run auto-reply loop (foreground)")
    run_parser.add_argument("--interval", type=int, default=60, help="polling interval in seconds")
    config_parser = ar_subparsers.add_parser("config", help="configuration")
    config_parser.add_argument("action", choices=["add-keyword", "remove-keyword", "add-contact", "add-group", "list"], help="action")
    config_parser.add_argument("value", nargs="?", help="value")
    log_parser = ar_subparsers.add_parser("log", help="view log")
    log_parser.add_argument("--limit", type=int, default=20, help="number of entries")
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return
    if args.command == "send":
        cmd_send(args)
    elif args.command == "auto-reply":
        if not args.subcmd:
            ar_parser.print_help()
            return
        cmd_auto_reply(args)


if __name__ == "__main__":
    main()
