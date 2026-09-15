#!/usr/bin/env python3
"""
wx-monitor.py - 微信群主动监控与推送工具
基于 byte886/wx-cli（jackwener/wx-cli 的多账号 fork），实现重要消息实时推送、每日总结、飞书同步。

用法:
  python3 wx-monitor.py recommend          # 推荐监控群
  python3 wx-monitor.py monitor            # 启动监控（前台运行，Ctrl+C 停止）
  python3 wx-monitor.py daily              # 生成当日总结
  python3 wx-monitor.py daily --sync       # 生成当日总结并同步到飞书
  python3 wx-monitor.py config list        # 查看当前配置
  python3 wx-monitor.py config add-group <群名或ID>
  python3 wx-monitor.py config remove-group <群名或ID>
  python3 wx-monitor.py config add-keyword <关键词>
  python3 wx-monitor.py config remove-keyword <关键词>
  python3 wx-monitor.py config add-person <人名>
  python3 wx-monitor.py config remove-person <人名>
  python3 wx-monitor.py status             # 查看监控状态
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# ============ 配置 ============
WX_CLI = shutil.which("wx") or "wx"
CONFIG_DIR = Path.home() / ".wx-cli"
CONFIG_FILE = CONFIG_DIR / "monitor_config.json"
STATE_FILE = CONFIG_DIR / "monitor_state.json"

# 默认配置
DEFAULT_CONFIG = {
    "monitor_groups": [],           # 监控的群列表 [{"name": "...", "id": "..."}]
    "keywords": [],                 # 自定义关键词
    "important_persons": [],        # 特定人（发任何消息都推送）
    "push_at_mention": True,        # @我 时推送
    "push_at_all": True,            # @all 时推送
    "poll_interval_minutes": 5,     # 轮询间隔（分钟）
    "daily_summary_time": "21:00",  # 每日总结时间
    "feishu_sync": False,           # 是否同步到飞书
    "feishu_wiki_node": "",         # 飞书知识库节点 token
    "current_user_nickname": "",    # 当前用户昵称（用于 @我 判断）
    "current_user_wxid": "",        # 当前用户 wxid
}

DEFAULT_STATE = {
    "last_poll_time": "",           # 上次轮询时间
    "last_pushed_msg_ids": [],      # 已推送的消息 ID（去重）
    "monitor_start_time": "",        # 监控启动时间
    "total_pushed": 0,              # 累计推送次数
}


# ============ 工具函数 ============
def run_wx(args, timeout=30):
    """执行 wx-cli 命令，返回解析后的 JSON 或文本"""
    cmd = [WX_CLI] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip() or result.stdout.strip()}
        # 尝试解析 JSON
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"text": result.stdout.strip()}
    except subprocess.TimeoutExpired:
        return {"error": "命令超时"}
    except Exception as e:
        return {"error": str(e)}


def load_config():
    """加载配置，不存在则创建默认配置"""
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG.copy())
        return DEFAULT_CONFIG.copy()
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)
    # 补全缺失的键
    for key, value in DEFAULT_CONFIG.items():
        if key not in config:
            config[key] = value
    return config


def save_config(config):
    """保存配置"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def load_state():
    """加载状态，不存在则创建默认状态"""
    if not STATE_FILE.exists():
        save_state(DEFAULT_STATE.copy())
        return DEFAULT_STATE.copy()
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
    for key, value in DEFAULT_STATE.items():
        if key not in state:
            state[key] = value
    return state


def save_state(state):
    """保存状态"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def is_important_message(msg, config):
    """判断消息是否重要（需要实时推送）
    返回 (is_important, reason)
    """
    content = msg.get("content", "")
    sender = msg.get("sender", "")
    msg_type = msg.get("type", "")

    # 系统消息不推送（入群/退群/撤回等）
    if msg_type == "系统":
        return False, ""

    # 1. @我 判断
    if config.get("push_at_mention", True):
        nickname = config.get("current_user_nickname", "")
        if nickname and f"@{nickname}" in content:
            return True, f"@{nickname}"
        # @all
        if "@all" in content or "@所有人" in content:
            return True, "@all"

    # 2. 关键词匹配
    keywords = config.get("keywords", [])
    for kw in keywords:
        if kw and kw in content:
            return True, f"关键词: {kw}"

    # 3. 特定人
    persons = config.get("important_persons", [])
    if sender in persons:
        return True, f"特定人: {sender}"

    return False, ""


def format_push_message(msg, reason, chat_name):
    """格式化推送消息"""
    sender = msg.get("sender", "未知")
    time_str = msg.get("time", "")
    content = msg.get("content", "")
    msg_type = msg.get("type", "")

    # 长内容截断
    if len(content) > 200:
        content = content[:200] + "..."

    # 特殊类型提示
    type_hint = ""
    if msg_type == "图片":
        type_hint = "[图片] "
    elif msg_type == "视频":
        type_hint = "[视频，需在微信查看] "
    elif msg_type == "语音":
        type_hint = "[语音，需在微信收听] "
    elif msg_type == "位置":
        type_hint = "[位置] "
    elif msg_type == "名片":
        type_hint = "[名片] "
    elif msg_type == "链接/文件":
        type_hint = "[链接] "

    return f"""🔔 重要消息推送
━━━━━━━━━━━━━━━━
群聊: {chat_name}
发送人: {sender}
时间: {time_str}
触发原因: {reason}
━━━━━━━━━━━━━━━━
{type_hint}{content}
━━━━━━━━━━━━━━━━
(在微信中查看完整内容)"""


# ============ 命令实现 ============
def cmd_recommend():
    """推荐监控群"""
    print("📊 正在分析所有会话的活跃度...")
    print()

    # 获取所有会话
    sessions_data = run_wx(["sessions", "--limit", "100", "--json"])
    if "error" in sessions_data:
        print(f"❌ 获取会话列表失败: {sessions_data['error']}")
        return

    sessions = sessions_data.get("sessions", [])
    groups = [s for s in sessions if s.get("is_group")]

    if not groups:
        print("⚠️  当前没有群聊会话")
        return

    print(f"找到 {len(groups)} 个群聊，正在分析活跃度...")
    print()

    # 对每个群获取统计
    group_stats = []
    for i, group in enumerate(groups):
        chat_id = group.get("chat", "")
        chat_name = group.get("chat", "")
        # 尝试用群名获取统计
        stats_data = run_wx(["stats", chat_id, "--json"])
        msg_count = 0
        if isinstance(stats_data, dict) and "by_hour" in stats_data:
            msg_count = sum(h.get("count", 0) for h in stats_data["by_hour"])

        # 获取群成员数（容错处理）
        member_count = "未知"
        try:
            members_data = run_wx(["members", chat_id])
            if isinstance(members_data, dict) and "members" in members_data:
                member_count = len(members_data["members"])
            elif isinstance(members_data, list):
                member_count = len(members_data)
            elif "text" in members_data:
                # 文本格式，数一下以 "- " 开头的行数
                lines = members_data["text"].split("\n")
                member_count = sum(1 for l in lines if l.strip().startswith("- "))
        except Exception:
            pass

        last_time = group.get("time", "")
        last_sender = group.get("last_sender", "")
        summary = group.get("summary", "")

        group_stats.append({
            "id": chat_id,
            "name": chat_name,
            "msg_count": msg_count,
            "member_count": member_count,
            "last_time": last_time,
            "last_sender": last_sender,
            "summary": summary,
        })

        # 进度提示
        if (i + 1) % 5 == 0:
            print(f"  已分析 {i+1}/{len(groups)}...")

    # 按消息量排序
    group_stats.sort(key=lambda x: x["msg_count"], reverse=True)

    # 输出推荐列表
    print()
    print("=" * 70)
    print("📋 监控群推荐列表（按活跃度排序）")
    print("=" * 70)
    print(f"{'序号':<4} {'群名':<25} {'消息量':<8} {'人数':<6} {'最后消息'}")
    print("-" * 70)

    for i, g in enumerate(group_stats[:15], 1):
        name = g["name"][:22] + "..." if len(g["name"]) > 25 else g["name"]
        print(f"{i:<4} {name:<25} {g['msg_count']:<8} {g['member_count']:<6} {g['last_time']}")

    print()
    print("💡 推荐理由:")
    print("  - 消息量多 = 活跃，可能有重要信息")
    print("  - 人数多 = 群规模大，值得关注")
    print("  - 最后消息时间近 = 正在活跃")
    print()
    print("请输入要监控的群序号（多个用逗号分隔，如 1,3,5）:")
    print("或输入 'all' 监控所有推荐群，输入 'q' 取消")

    # 等待用户输入
    try:
        choice = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消")
        return

    if choice.lower() == "q":
        print("已取消")
        return

    selected_indices = []
    if choice.lower() == "all":
        selected_indices = list(range(len(group_stats[:15])))
    else:
        try:
            selected_indices = [int(x.strip()) - 1 for x in choice.split(",") if x.strip()]
        except ValueError:
            print("❌ 输入格式错误，请输入数字序号")
            return

    # 写入配置
    config = load_config()
    added = 0
    for idx in selected_indices:
        if 0 <= idx < len(group_stats):
            g = group_stats[idx]
            # 检查是否已存在
            exists = any(m["id"] == g["id"] for m in config["monitor_groups"])
            if not exists:
                config["monitor_groups"].append({"name": g["name"], "id": g["id"]})
                added += 1

    save_config(config)
    print()
    print(f"✅ 已添加 {added} 个监控群")
    print(f"当前监控群总数: {len(config['monitor_groups'])}")
    print()
    print("运行 'python3 wx-monitor.py config list' 查看当前配置")
    print("运行 'python3 wx-monitor.py monitor' 启动监控")


def cmd_monitor(once=False):
    """启动监控（前台运行）"""
    config = load_config()

    if not config["monitor_groups"]:
        print("⚠️  当前没有监控群，请先运行 'python3 wx-monitor.py recommend' 或 'config add-group'")
        return

    # 检测当前用户
    if not config.get("current_user_nickname"):
        print("ℹ️  正在检测当前微信用户...")
        # 从 contacts 或 sessions 推断
        contacts_data = run_wx(["contacts", "--json"])
        if isinstance(contacts_data, dict):
            # 尝试找到自己（通常在 contacts 里有自己）
            pass

    interval = config.get("poll_interval_minutes", 5) * 60
    state = load_state()
    state["monitor_start_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)

    print(f"🔍 监控已启动")
    print(f"   监控群数: {len(config['monitor_groups'])}")
    print(f"   轮询间隔: {config.get('poll_interval_minutes', 5)} 分钟")
    print(f"   关键词: {config.get('keywords', []) or '无'}")
    print(f"   特定人: {config.get('important_persons', []) or '无'}")
    print(f"   @我推送: {'开启' if config.get('push_at_mention') else '关闭'}")
    print()
    print("按 Ctrl+C 停止监控")
    print("=" * 50)

    try:
        while True:
            poll_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{poll_time}] 正在轮询...")

            total_new = 0
            total_pushed = 0

            # new-messages 是全局接口（与具体群无关），整轮只拉一次，
            # 再按 chat 字段过滤分配给各群，避免 N 个群重复拉取 N 次
            new_msgs_data = run_wx(["new-messages", "--limit", "100", "--json"])
            if "error" in new_msgs_data:
                print(f"  ⚠️  获取新消息失败: {new_msgs_data['error']}")
                all_msgs = []
            else:
                all_msgs = new_msgs_data.get("messages", [])

            for group in config["monitor_groups"]:
                group_id = group["id"]
                group_name = group.get("name", group_id)

                # 筛选当前群的消息
                group_msgs = [m for m in all_msgs if m.get("chat") == group_id]

                if not group_msgs:
                    continue

                total_new += len(group_msgs)

                # 判断重要消息
                for msg in group_msgs:
                    msg_id = f"{group_id}_{msg.get('local_id', '')}_{msg.get('timestamp', '')}"
                    # 去重
                    if msg_id in state["last_pushed_msg_ids"]:
                        continue

                    is_important, reason = is_important_message(msg, config)
                    if is_important:
                        push_msg = format_push_message(msg, reason, group_name)
                        print()
                        print(push_msg)
                        print()

                        state["last_pushed_msg_ids"].append(msg_id)
                        state["total_pushed"] = state.get("total_pushed", 0) + 1
                        total_pushed += 1

                # 限制已推送消息 ID 数量（最多 1000 条）
                if len(state["last_pushed_msg_ids"]) > 1000:
                    state["last_pushed_msg_ids"] = state["last_pushed_msg_ids"][-1000:]

            state["last_poll_time"] = poll_time
            save_state(state)

            if total_new > 0:
                print(f"  本次轮询: 新消息 {total_new} 条，推送 {total_pushed} 条")
            else:
                print(f"  本次轮询: 无新消息")

            # 性能保护提醒
            if total_new > 500:
                print(f"  ⚠️  性能提醒: 单次轮询新消息 {total_new} 条，建议减少监控群或增加轮询间隔")

            if once:
                print("\n✅ 单次轮询完成（--once 模式）")
                break

            time.sleep(interval)

    except KeyboardInterrupt:
        print()
        print("=" * 50)
        print("监控已停止")
        print(f"累计推送: {state.get('total_pushed', 0)} 条")
        print()


def cmd_daily(sync=False):
    """生成当日总结"""
    config = load_config()

    if not config["monitor_groups"]:
        print("⚠️  当前没有监控群")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    print(f"📝 正在生成 {today} 每日总结...")
    print()

    summary_parts = [f"# 微信群监控每日总结 ({today})\n"]

    for group in config["monitor_groups"]:
        group_id = group["id"]
        group_name = group.get("name", group_id)

        print(f"  正在分析: {group_name}")

        # 获取当日历史
        history_data = run_wx([
            "history", group_id,
            "--since", today,
            "--limit", "500",
            "--json"
        ])

        if "error" in history_data:
            print(f"    ⚠️  获取历史失败: {history_data['error']}")
            continue

        messages = history_data.get("messages", [])
        if not messages:
            summary_parts.append(f"## {group_name}\n\n今日无消息\n")
            continue

        # 统计
        senders = {}
        text_msgs = []
        important_msgs = []

        for msg in messages:
            sender = msg.get("sender", "") or "系统"
            senders[sender] = senders.get(sender, 0) + 1

            msg_type = msg.get("type", "")
            content = msg.get("content", "")

            if msg_type == "文本" and content:
                text_msgs.append((sender, content, msg.get("time", "")))

            # 判断重要消息
            is_imp, reason = is_important_message(msg, config)
            if is_imp:
                important_msgs.append((sender, content, msg.get("time", ""), reason))

        # 活跃发送者 Top 5
        top_senders = sorted(senders.items(), key=lambda x: x[1], reverse=True)[:5]

        # 生成总结
        summary_parts.append(f"## {group_name}\n")
        summary_parts.append(f"- 消息总数: {len(messages)} 条")
        summary_parts.append(f"- 参与人数: {len(senders)} 人")
        summary_parts.append(f"- 活跃成员: {', '.join(f'{s}({c})' for s, c in top_senders)}")
        summary_parts.append("")

        # 重要消息
        if important_msgs:
            summary_parts.append("### ⚠️ 重要消息")
            for sender, content, time_str, reason in important_msgs[:10]:
                if len(content) > 100:
                    content = content[:100] + "..."
                summary_parts.append(f"- **{sender}** ({time_str}) [{reason}]: {content}")
            summary_parts.append("")

        # 消息类型统计
        type_counts = {}
        for msg in messages:
            t = msg.get("type", "未知")
            type_counts[t] = type_counts.get(t, 0) + 1
        summary_parts.append("### 消息类型")
        summary_parts.append(", ".join(f"{t}: {c}" for t, c in type_counts.items()))
        summary_parts.append("")

        # 文本消息摘要（最近 10 条）
        if text_msgs:
            summary_parts.append("### 最近文本消息")
            for sender, content, time_str in text_msgs[-10:]:
                if len(content) > 80:
                    content = content[:80] + "..."
                summary_parts.append(f"- {sender} ({time_str}): {content}")
            summary_parts.append("")

    # 输出总结
    full_summary = "\n".join(summary_parts)
    print()
    print("=" * 70)
    print(full_summary)
    print("=" * 70)

    # 保存到本地
    output_file = CONFIG_DIR / f"daily_summary_{today}.md"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(full_summary)
    print(f"\n✅ 总结已保存到: {output_file}")

    # 飞书同步
    if sync:
        print("\n🔄 正在同步到飞书...")
        # TODO: 飞书同步实现
        print("⚠️  飞书同步功能开发中...")


def cmd_config(args):
    """配置管理"""
    if not args:
        print("用法:")
        print("  python3 wx-monitor.py config list")
        print("  python3 wx-monitor.py config add-group <群名或ID>")
        print("  python3 wx-monitor.py config remove-group <群名或ID>")
        print("  python3 wx-monitor.py config add-keyword <关键词>")
        print("  python3 wx-monitor.py config remove-keyword <关键词>")
        print("  python3 wx-monitor.py config add-person <人名>")
        print("  python3 wx-monitor.py config remove-person <人名>")
        return

    action = args[0]
    config = load_config()

    if action == "list":
        print("📋 当前监控配置")
        print("=" * 50)
        print(f"监控群 ({len(config['monitor_groups'])}):")
        for g in config["monitor_groups"]:
            print(f"  - {g.get('name', g['id'])} ({g['id']})")
        print()
        print(f"关键词 ({len(config['keywords'])}):")
        for kw in config["keywords"]:
            print(f"  - {kw}")
        print()
        print(f"特定人 ({len(config['important_persons'])}):")
        for p in config["important_persons"]:
            print(f"  - {p}")
        print()
        print(f"@我推送: {'开启' if config.get('push_at_mention') else '关闭'}")
        print(f"@all推送: {'开启' if config.get('push_at_all') else '关闭'}")
        print(f"轮询间隔: {config.get('poll_interval_minutes', 5)} 分钟")
        print(f"每日总结时间: {config.get('daily_summary_time', '21:00')}")
        print(f"飞书同步: {'开启' if config.get('feishu_sync') else '关闭'}")

    elif action == "add-group":
        if len(args) < 2:
            print("❌ 请指定群名或ID")
            return
        group_name = args[1]
        # 尝试解析群 ID
        sessions_data = run_wx(["sessions", "--limit", "100", "--json"])
        group_id = group_name
        matched_name = group_name
        if isinstance(sessions_data, dict):
            for s in sessions_data.get("sessions", []):
                if s.get("chat") == group_name or group_name in s.get("chat", ""):
                    group_id = s["chat"]
                    matched_name = s["chat"]
                    break

        exists = any(g["id"] == group_id for g in config["monitor_groups"])
        if exists:
            print(f"⚠️  群 '{matched_name}' 已在监控列表中")
        else:
            config["monitor_groups"].append({"name": matched_name, "id": group_id})
            save_config(config)
            print(f"✅ 已添加监控群: {matched_name}")

    elif action == "remove-group":
        if len(args) < 2:
            print("❌ 请指定群名或ID")
            return
        group_name = args[1]
        before = len(config["monitor_groups"])
        config["monitor_groups"] = [
            g for g in config["monitor_groups"]
            if g["id"] != group_name and g.get("name") != group_name
        ]
        if len(config["monitor_groups"]) < before:
            save_config(config)
            print(f"✅ 已移除监控群: {group_name}")
        else:
            print(f"⚠️  未找到群: {group_name}")

    elif action == "add-keyword":
        if len(args) < 2:
            print("❌ 请指定关键词")
            return
        kw = args[1]
        if kw in config["keywords"]:
            print(f"⚠️  关键词 '{kw}' 已存在")
        else:
            config["keywords"].append(kw)
            save_config(config)
            print(f"✅ 已添加关键词: {kw}")

    elif action == "remove-keyword":
        if len(args) < 2:
            print("❌ 请指定关键词")
            return
        kw = args[1]
        if kw in config["keywords"]:
            config["keywords"].remove(kw)
            save_config(config)
            print(f"✅ 已移除关键词: {kw}")
        else:
            print(f"⚠️  未找到关键词: {kw}")

    elif action == "add-person":
        if len(args) < 2:
            print("❌ 请指定人名")
            return
        person = args[1]
        if person in config["important_persons"]:
            print(f"⚠️  特定人 '{person}' 已存在")
        else:
            config["important_persons"].append(person)
            save_config(config)
            print(f"✅ 已添加特定人: {person}")

    elif action == "remove-person":
        if len(args) < 2:
            print("❌ 请指定人名")
            return
        person = args[1]
        if person in config["important_persons"]:
            config["important_persons"].remove(person)
            save_config(config)
            print(f"✅ 已移除特定人: {person}")
        else:
            print(f"⚠️  未找到特定人: {person}")

    else:
        print(f"❌ 未知操作: {action}")


def cmd_status():
    """查看监控状态"""
    state = load_state()
    config = load_config()

    print("📊 监控状态")
    print("=" * 50)
    print(f"监控群数: {len(config['monitor_groups'])}")
    print(f"监控启动时间: {state.get('monitor_start_time', '未启动')}")
    print(f"上次轮询时间: {state.get('last_poll_time', '从未')}")
    print(f"累计推送次数: {state.get('total_pushed', 0)}")
    print(f"已推送消息 ID 数: {len(state.get('last_pushed_msg_ids', []))}")
    print()
    print("配置文件:", CONFIG_FILE)
    print("状态文件:", STATE_FILE)


# ============ 主入口 ============
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1]

    if command == "recommend":
        cmd_recommend()
    elif command == "monitor":
        once = "--once" in sys.argv
        cmd_monitor(once=once)
    elif command == "daily":
        sync = "--sync" in sys.argv
        cmd_daily(sync=sync)
    elif command == "config":
        cmd_config(sys.argv[2:])
    elif command == "status":
        cmd_status()
    elif command == "--help" or command == "-h":
        print(__doc__)
    else:
        print(f"❌ 未知命令: {command}")
        print(__doc__)


if __name__ == "__main__":
    main()
