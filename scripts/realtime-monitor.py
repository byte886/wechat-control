#!/usr/bin/env python3
"""
微信新消息实时监听器（优化版）

特点：
1. 启动时自动检查 daemon 配置（db_dir 是否指向当前登录账号）
2. 配置不对时自动重启 daemon（kill + 清缓存 + 重启）
3. 初始化状态基线，避免把历史消息当成新消息
4. 每 N 秒轮询 wx new-messages（daemon 自动检测数据库更新，无需重启）
5. 检测到新消息时打印详细通知（发送者、会话、内容、类型、时间）
6. 支持语音消息标记（可选择自动转写）
7. 支持重要消息判定（关键词、特定发送者/群）
8. 日志记录到文件

用法：
  python3 realtime-monitor.py monitor --interval 10
  python3 realtime-monitor.py monitor --interval 5 --transcribe-voice
  python3 realtime-monitor.py once                    # 单次检查
  python3 realtime-monitor.py status                  # 查看状态
  python3 realtime-monitor.py config                  # 查看/修改配置
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ============ 配置 ============
WX_CLI_DIR = Path.home() / ".wx-cli"
CONFIG_FILE = WX_CLI_DIR / "config.json"
DAEMON_PID_FILE = WX_CLI_DIR / "daemon.pid"
DAEMON_SOCK = WX_CLI_DIR / "daemon.sock"
CACHE_DIR = WX_CLI_DIR / "cache"
STATE_FILE = WX_CLI_DIR / "realtime_monitor_state.json"
LOG_FILE = WX_CLI_DIR / "realtime_monitor.log"

# 重要消息配置（可通过 config 命令修改）
DEFAULT_CONFIG = {
    "interval_seconds": 10,
    "transcribe_voice": False,
    "important_keywords": [],
    "important_senders": [],
    "important_groups": [],
    "log_to_file": True,
}
MONITOR_CONFIG_FILE = WX_CLI_DIR / "realtime_monitor_config.json"


# ============ 工具函数 ============
def log(msg, level="INFO"):
    """打印日志并可选写入文件"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level}] {msg}"
    print(line, flush=True)
    config = load_monitor_config()
    if config.get("log_to_file", True):
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def load_monitor_config():
    """加载监控配置"""
    if MONITOR_CONFIG_FILE.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(MONITOR_CONFIG_FILE.read_text())}
        except:
            pass
    return DEFAULT_CONFIG.copy()


def save_monitor_config(config):
    """保存监控配置"""
    MONITOR_CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2))


def run_wx(args, timeout=30):
    """运行 wx 命令并返回解析后的 JSON"""
    try:
        result = subprocess.run(
            ["wx"] + args,
            capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip() or result.stdout.strip()}
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {"error": "命令超时"}
    except json.JSONDecodeError as e:
        return {"error": f"JSON解析失败: {e}", "raw": result.stdout[:500]}
    except Exception as e:
        return {"error": str(e)}


# ============ Daemon 管理 ============
def get_daemon_pid():
    """获取 daemon PID"""
    if DAEMON_PID_FILE.exists():
        try:
            data = json.loads(DAEMON_PID_FILE.read_text())
            return data.get("pid")
        except:
            pass
    return None


def is_daemon_running(pid):
    """检查 daemon 进程是否在运行"""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except:
        return False


def get_daemon_db_dir():
    """从 daemon.log 获取当前 daemon 读取的 DB_DIR"""
    log_file = WX_CLI_DIR / "daemon.log"
    if log_file.exists():
        lines = log_file.read_text().split("\n")
        for line in reversed(lines):
            if "DB_DIR:" in line:
                return line.split("DB_DIR:")[1].strip()
    return None


def get_config_db_dir():
    """从 config.json 获取配置的 db_dir"""
    if CONFIG_FILE.exists():
        config = json.loads(CONFIG_FILE.read_text())
        return config.get("db_dir", "")
    return ""


def restart_daemon():
    """重启 daemon：kill 进程 + 清缓存 + 重启"""
    pid = get_daemon_pid()
    if pid and is_daemon_running(pid):
        log(f"停止 daemon (PID={pid})...")
        try:
            os.kill(pid, 9)
            time.sleep(1)
        except:
            pass

    # 清除残留文件
    for f in [DAEMON_SOCK, DAEMON_PID_FILE]:
        if f.exists():
            f.unlink()

    # 清除解密缓存
    cache_files = list(CACHE_DIR.glob("*.db")) if CACHE_DIR.exists() else []
    if cache_files:
        log(f"清除 {len(cache_files)} 个解密缓存文件...")
        for f in cache_files:
            f.unlink()

    # 启动 daemon（运行任意 wx 命令会自动启动）
    log("启动 daemon...")
    result = run_wx(["sessions", "--limit", "1"], timeout=15)
    if "error" in result:
        log(f"daemon 启动可能有问题: {result['error']}", "WARN")
    else:
        log("✅ daemon 启动成功")

    # 验证 daemon 读取的 DB_DIR
    time.sleep(1)
    actual_db_dir = get_daemon_db_dir()
    config_db_dir = get_config_db_dir()
    if actual_db_dir and actual_db_dir != config_db_dir:
        log(f"⚠️  daemon DB_DIR 与配置不一致: daemon={actual_db_dir}, config={config_db_dir}", "WARN")
    elif actual_db_dir:
        log(f"✅ daemon DB_DIR 正确: {actual_db_dir}")


def ensure_daemon_config():
    """确保 daemon 配置正确，不对则重启"""
    config_db_dir = get_config_db_dir()
    actual_db_dir = get_daemon_db_dir()
    pid = get_daemon_pid()

    if not is_daemon_running(pid):
        log("daemon 未运行，启动...")
        restart_daemon()
        return True

    if not actual_db_dir:
        log("无法获取 daemon DB_DIR，重启 daemon...")
        restart_daemon()
        return True

    if actual_db_dir != config_db_dir:
        log(f"daemon DB_DIR 不匹配（daemon={actual_db_dir}, config={config_db_dir}），重启...")
        restart_daemon()
        return True

    log(f"✅ daemon 配置正确，无需重启 (DB_DIR={config_db_dir})")
    return False


# ============ 状态管理 ============
def load_state():
    """加载监控状态"""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except:
            pass
    return {
        "initialized": False,
        "last_check_time": None,
        "total_new_messages": 0,
        "last_new_state": {},
    }


def save_state(state):
    """保存监控状态"""
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def initialize_state():
    """初始化状态基线（记录当前已有消息，避免当成新消息）"""
    log("初始化状态基线...")
    result = run_wx(["new-messages", "--limit", "100", "--json"])
    if "error" in result:
        log(f"初始化失败: {result['error']}", "ERROR")
        return False

    state = load_state()
    state["initialized"] = True
    state["last_check_time"] = datetime.now().isoformat()
    state["last_new_state"] = result.get("new_state", {})
    state["baseline_count"] = result.get("count", 0)
    save_state(state)
    log(f"✅ 状态基线已初始化（基线消息数: {state['baseline_count']}）")
    return True


# ============ 消息处理 ============
def is_important_message(msg, config):
    """判断是否为重要消息"""
    content = msg.get("content", "")
    sender = msg.get("sender", "")
    chat = msg.get("chat", "")
    is_group = msg.get("is_group", False)

    # 关键词匹配
    for kw in config.get("important_keywords", []):
        if kw and kw in content:
            return True, f"关键词匹配: {kw}"

    # 特定发送者
    for s in config.get("important_senders", []):
        if s and s in sender:
            return True, f"重要发送者: {s}"

    # 特定群
    if is_group:
        for g in config.get("important_groups", []):
            if g and g in chat:
                return True, f"重要群: {g}"

    return False, ""


def format_message_notification(msg, is_important, reason=""):
    """格式化消息通知"""
    chat = msg.get("chat", "未知")
    sender = msg.get("sender", "未知")
    content = msg.get("content", "")
    msg_type = msg.get("type", "未知")
    time_str = msg.get("time", "")
    is_group = msg.get("is_group", False)
    username = msg.get("username", "")

    if len(content) > 100:
        content = content[:100] + "..."

    lines = []
    important_tag = " 🔴重要" if is_important else ""
    group_tag = " [群聊]" if is_group else " [私聊]"

    lines.append(f"🔔 新消息{important_tag}{group_tag}")
    lines.append(f"   时间: {time_str}")
    lines.append(f"   会话: {chat} (username: {username})")
    lines.append(f"   发送者: {sender}")
    lines.append(f"   类型: {msg_type}")
    lines.append(f"   内容: {content}")
    if is_important and reason:
        lines.append(f"   重要原因: {reason}")

    return "\n".join(lines)


def transcribe_voice_message(msg):
    """转写语音消息（调用 voice-transcribe.py）"""
    # 语音消息需要从数据库提取，这里只做标记
    # 完整转写需要 local_id 和 chat 信息
    log(f"  🎤 语音消息待转写: chat={msg.get('chat')}, time={msg.get('time')}")
    # TODO: 调用 voice-transcribe.py 进行转写
    # 需要先修复 voice-transcribe.py 的路径硬编码问题


def process_new_messages(messages, config):
    """处理新消息列表"""
    if not messages:
        return

    log(f"检测到 {len(messages)} 条新消息:")
    print("-" * 60)

    for msg in messages:
        is_important, reason = is_important_message(msg, config)
        notification = format_message_notification(msg, is_important, reason)
        print(notification)
        print()

        # 语音消息处理
        if msg.get("type") == "语音" and config.get("transcribe_voice", False):
            transcribe_voice_message(msg)

    print("-" * 60)

    # 更新状态
    state = load_state()
    state["total_new_messages"] = state.get("total_new_messages", 0) + len(messages)
    state["last_check_time"] = datetime.now().isoformat()
    save_state(state)


# ============ 主循环 ============
def check_once():
    """单次检查新消息"""
    result = run_wx(["new-messages", "--limit", "50", "--json"])
    if "error" in result:
        log(f"检查失败: {result['error']}", "ERROR")
        return []

    messages = result.get("messages", [])

    # 更新 new_state
    state = load_state()
    state["last_new_state"] = result.get("new_state", {})
    state["last_check_time"] = datetime.now().isoformat()
    save_state(state)

    return messages


def monitor_loop(interval=None):
    """监控主循环"""
    config = load_monitor_config()
    if interval:
        config["interval_seconds"] = interval

    log("=" * 60)
    log("微信新消息实时监听器启动")
    log(f"轮询间隔: {config['interval_seconds']} 秒")
    log(f"语音自动转写: {'开启' if config.get('transcribe_voice') else '关闭'}")
    log(f"重要关键词: {config.get('important_keywords', []) or '无'}")
    log("=" * 60)

    # 1. 确保 daemon 配置正确
    ensure_daemon_config()

    # 2. 初始化状态基线
    state = load_state()
    if not state.get("initialized"):
        if not initialize_state():
            log("状态初始化失败，退出", "ERROR")
            return
    else:
        log(f"状态已初始化（基线消息数: {state.get('baseline_count', 0)}）")

    # 3. 主循环
    log("开始监控，按 Ctrl+C 停止...")
    print()

    try:
        while True:
            messages = check_once()
            if messages:
                process_new_messages(messages, config)
            time.sleep(config["interval_seconds"])
    except KeyboardInterrupt:
        log("\n监控已停止")
        state = load_state()
        log(f"累计检测到新消息: {state.get('total_new_messages', 0)} 条")


# ============ 命令处理 ============
def cmd_status():
    """查看监控状态"""
    state = load_state()
    config = load_monitor_config()

    print("=== 实时监听器状态 ===")
    print(f"已初始化: {'是' if state.get('initialized') else '否'}")
    print(f"最后检查: {state.get('last_check_time', '从未')}")
    print(f"累计新消息: {state.get('total_new_messages', 0)} 条")
    print(f"基线消息数: {state.get('baseline_count', 0)}")
    print()
    print("=== 配置 ===")
    print(f"轮询间隔: {config['interval_seconds']} 秒")
    print(f"语音自动转写: {'开启' if config.get('transcribe_voice') else '关闭'}")
    print(f"重要关键词: {config.get('important_keywords', []) or '无'}")
    print(f"重要发送者: {config.get('important_senders', []) or '无'}")
    print(f"重要群: {config.get('important_groups', []) or '无'}")
    print()
    print("=== Daemon 状态 ===")
    pid = get_daemon_pid()
    print(f"Daemon PID: {pid or '无'}")
    print(f"Daemon 运行中: {'是' if is_daemon_running(pid) else '否'}")
    print(f"配置 DB_DIR: {get_config_db_dir()}")
    print(f"Daemon DB_DIR: {get_daemon_db_dir() or '未知'}")
    print()
    print(f"日志文件: {LOG_FILE}")
    print(f"状态文件: {STATE_FILE}")


def cmd_config(args):
    """查看/修改配置"""
    config = load_monitor_config()

    if args.action == "show":
        print(json.dumps(config, ensure_ascii=False, indent=2))
    elif args.action == "set":
        key = args.key
        value = args.value
        if key in ["interval_seconds", "transcribe_voice", "log_to_file"]:
            if key == "interval_seconds":
                config[key] = int(value)
            elif key in ["transcribe_voice", "log_to_file"]:
                config[key] = value.lower() in ["true", "1", "yes"]
        elif key in ["important_keywords", "important_senders", "important_groups"]:
            config[key] = [v.strip() for v in value.split(",") if v.strip()]
        else:
            print(f"未知配置项: {key}")
            print(f"可用配置项: {list(config.keys())}")
            return
        save_monitor_config(config)
        print(f"✅ 已设置 {key} = {config[key]}")
    elif args.action == "add":
        key = args.key
        value = args.value
        if key in ["important_keywords", "important_senders", "important_groups"]:
            if value not in config[key]:
                config[key].append(value)
                save_monitor_config(config)
                print(f"✅ 已添加 {value} 到 {key}")
            else:
                print(f"⚠️  {value} 已存在于 {key}")
        else:
            print(f"该配置项不支持 add: {key}")
    elif args.action == "remove":
        key = args.key
        value = args.value
        if key in ["important_keywords", "important_senders", "important_groups"]:
            if value in config[key]:
                config[key].remove(value)
                save_monitor_config(config)
                print(f"✅ 已从 {key} 移除 {value}")
            else:
                print(f"⚠️  {value} 不存在于 {key}")


def cmd_reset():
    """重置状态（重新初始化基线）"""
    if STATE_FILE.exists():
        STATE_FILE.unlink()
        print("✅ 状态已重置，下次启动将重新初始化基线")
    else:
        print("状态文件不存在，无需重置")


def main():
    parser = argparse.ArgumentParser(description="微信新消息实时监听器")
    subparsers = parser.add_subparsers(dest="command", help="命令")

    # monitor
    mon_parser = subparsers.add_parser("monitor", help="启动实时监控")
    mon_parser.add_argument("--interval", type=int, help="轮询间隔（秒）")
    mon_parser.add_argument("--transcribe-voice", action="store_true", help="自动转写语音消息")

    # once
    subparsers.add_parser("once", help="单次检查新消息")

    # status
    subparsers.add_parser("status", help="查看监控状态")

    # config
    cfg_parser = subparsers.add_parser("config", help="查看/修改配置")
    cfg_sub = cfg_parser.add_subparsers(dest="action")
    cfg_sub.add_parser("show", help="显示配置")
    set_parser = cfg_sub.add_parser("set", help="设置配置项")
    set_parser.add_argument("key", help="配置项名称")
    set_parser.add_argument("value", help="配置值")
    add_parser = cfg_sub.add_parser("add", help="添加到列表配置项")
    add_parser.add_argument("key", help="配置项名称")
    add_parser.add_argument("value", help="要添加的值")
    rem_parser = cfg_sub.add_parser("remove", help="从列表配置项移除")
    rem_parser.add_argument("key", help="配置项名称")
    rem_parser.add_argument("value", help="要移除的值")

    # reset
    subparsers.add_parser("reset", help="重置状态（重新初始化基线）")

    # restart-daemon
    subparsers.add_parser("restart-daemon", help="重启 wx-cli daemon")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "monitor":
        monitor_loop(interval=args.interval)
    elif args.command == "once":
        ensure_daemon_config()
        messages = check_once()
        if messages:
            config = load_monitor_config()
            process_new_messages(messages, config)
        else:
            log("暂无新消息")
    elif args.command == "status":
        cmd_status()
    elif args.command == "config":
        if not args.action:
            args.action = "show"
        cmd_config(args)
    elif args.command == "reset":
        cmd_reset()
    elif args.command == "restart-daemon":
        restart_daemon()


if __name__ == "__main__":
    main()
