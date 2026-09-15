#!/usr/bin/env python3
"""
微信统一消息采集模块
整合 wx-cli（文本）、media_0.db（语音）、message_0/1/2.db（所有消息元数据）
支持所有消息类型的识别和统一输出。

用法：
  python3 message-collector.py list-types              # 列出所有消息类型统计
  python3 message-collector.py list-messages --limit 20  # 列出最近消息
  python3 message-collector.py list-messages --since "2026-09-10" --type 1  # 按时间和类型过滤
  python3 message-collector.py monitor --interval 30   # 实时监控新消息
  python3 message-collector.py stats                    # 消息统计
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ============================================================
# 配置
# ============================================================

SKILL_DIR = Path(__file__).parent.parent
WX_CLI_CONFIG = Path.home() / ".wx-cli" / "config.json"
ALL_KEYS_FILE = Path.home() / ".wx-cli" / "all_keys.json"
STATE_FILE = Path.home() / ".wx-cli" / "collector_state.json"

# 消息类型映射（base_type = local_type 低32位 -> 名称）
# 参考 wx-cli src/daemon/query.rs fmt_type()
MESSAGE_TYPES = {
    1: "文本",
    3: "图片",
    34: "语音",
    42: "名片",
    43: "视频",
    47: "表情",
    48: "位置",
    49: "链接/文件",
    50: "通话",
    10000: "系统",
    10002: "撤回",
}

# 大 local_type（含高32位flag）的常见类型映射（用于统计显示）
EXTENDED_TYPE_HINTS = {
    244813135921: "位置",
    25769803825: "名片",
    81604378673: "合并聊天记录",
    21474836529: "红包",
    141733920817: "转账",
    8589934592049: "文件",
    219043332145: "引用消息",
    266287972401: "小程序",
    270582939697: "公众号",
    227633266737: "链接卡片",
}


def get_base_type(local_type):
    """获取消息真实类型（local_type 低32位）
    wx-cli: base = (local_type as u64 & 0xFFFFFFFF) as i64
    高32位是版本/会话flag，低32位才是真实类型
    """
    return local_type & 0xFFFFFFFF


def get_type_name(local_type):
    """获取消息类型名称（优先用base_type映射，fallback到扩展映射）"""
    base = get_base_type(local_type)
    if base in MESSAGE_TYPES:
        return MESSAGE_TYPES[base]
    if local_type in EXTENDED_TYPE_HINTS:
        return EXTENDED_TYPE_HINTS[local_type]
    return f"未知(type={base})"


def run_wx_cli(args):
    """调用 wx-cli 命令并返回解析后的 JSON"""
    try:
        result = subprocess.run(
            ["wx"] + args,
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None

# 全局变量
DB_PATHS = {}
MSG_DB_KEYS = {}
MEDIA_DB_KEY = None
CONTACT_DB_KEY = None


# ============================================================
# 工具函数
# ============================================================

def load_keys():
    """加载数据库密钥和路径"""
    global DB_PATHS, MSG_DB_KEYS, MEDIA_DB_KEY, CONTACT_DB_KEY

    if not WX_CLI_CONFIG.exists():
        print("❌ wx-cli 配置不存在，请先运行 wx init", file=sys.stderr)
        sys.exit(1)

    config = json.loads(WX_CLI_CONFIG.read_text())
    db_dir = Path(config.get("db_dir", ""))

    if not db_dir.exists():
        print(f"❌ 数据目录不存在: {db_dir}", file=sys.stderr)
        sys.exit(1)

    DB_PATHS = {
        "message_0": db_dir / "message" / "message_0.db",
        "message_1": db_dir / "message" / "message_1.db",
        "message_2": db_dir / "message" / "message_2.db",
        "media": db_dir / "message" / "media_0.db",
        "contact": db_dir / "contact" / "contact.db",
        "session": db_dir / "session" / "session.db",
    }

    # 加载密钥（all_keys.json 是 dict，key 是数据库路径）
    if ALL_KEYS_FILE.exists():
        all_keys = json.loads(ALL_KEYS_FILE.read_text())
        for db_path_key, key_info in all_keys.items():
            enc_key = key_info.get("enc_key", "") if isinstance(key_info, dict) else str(key_info)
            if "message_0" in db_path_key:
                MSG_DB_KEYS["message_0"] = enc_key
            elif "message_1" in db_path_key:
                MSG_DB_KEYS["message_1"] = enc_key
            elif "message_2" in db_path_key:
                MSG_DB_KEYS["message_2"] = enc_key
            elif "media" in db_path_key:
                MEDIA_DB_KEY = enc_key
            elif "contact" in db_path_key and "fts" not in db_path_key:
                CONTACT_DB_KEY = enc_key


def sqlcipher_query(db_path, db_key, query, params=None):
    """执行 SQLCipher 查询"""
    if not db_key:
        return []
    key_pragma = f"PRAGMA key = \"x'{db_key}'\";"
    full_sql = f"{key_pragma}\n{query}"
    result = subprocess.run(
        ["sqlcipher", str(db_path)],
        input=full_sql,
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        return []
    lines = result.stdout.strip().split("\n")
    return [l for l in lines if l.strip() and l.strip() != "ok"]


def get_chat_name_id_to_wxid(chat_name_id):
    """通过 media_0.db Name2Id 表获取会话 wxid"""
    if not MEDIA_DB_KEY:
        return f"unknown_{chat_name_id}"
    rows = sqlcipher_query(
        DB_PATHS["media"], MEDIA_DB_KEY,
        f"SELECT user_name FROM Name2Id WHERE rowid = {chat_name_id};"
    )
    return rows[0] if rows else f"unknown_{chat_name_id}"


def get_contact_info(wxid):
    """获取联系人详细信息"""
    if not CONTACT_DB_KEY or not wxid or wxid.startswith("unknown_"):
        return {"wxid": wxid, "display_name": wxid, "nick_name": "", "remark": ""}

    rows = sqlcipher_query(
        DB_PATHS["contact"], CONTACT_DB_KEY,
        f"SELECT remark, nick_name, alias FROM contact WHERE username = '{wxid}';"
    )
    if rows:
        parts = rows[0].split("|") if "|" in rows[0] else rows[0].split()
        if len(parts) >= 2:
            remark = parts[0].strip()
            nick_name = parts[1].strip()
            alias = parts[2].strip() if len(parts) > 2 else ""
            display_name = remark or nick_name or alias or wxid
            return {"wxid": wxid, "display_name": display_name, "nick_name": nick_name, "remark": remark, "alias": alias}
    return {"wxid": wxid, "display_name": wxid, "nick_name": "", "remark": ""}


def get_session_table_name(wxid):
    """获取会话对应的表名 Msg_<MD5(短wxid)>"""
    # 短 wxid：去掉 _xxxxx 后缀
    short_wxid = wxid.split("_")[0] + "_" + wxid.split("_")[1] if wxid.count("_") >= 2 else wxid
    table_md5 = hashlib.md5(short_wxid.encode()).hexdigest()
    return f"Msg_{table_md5}"


# ============================================================
# 消息采集
# ============================================================

def collect_all_messages(since_time=0, chat_filter=None, type_filter=None, limit=100):
    """从所有 message 数据库采集消息元数据"""
    all_messages = []

    for db_name in ["message_0", "message_1", "message_2"]:
        db_key = MSG_DB_KEYS.get(db_name)
        db_path = DB_PATHS.get(db_name)
        if not db_key or not db_path or not db_path.exists():
            continue

        # 获取所有会话表
        tables = sqlcipher_query(db_path, db_key,
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%';")

        for table_name in tables:
            # 构建查询条件
            where = ["1=1"]
            if since_time > 0:
                where.append(f"create_time > {since_time}")
            if type_filter:
                where.append(f"local_type = {type_filter}")

            query = f"""
                SELECT local_id, local_type, create_time, real_sender_id, status, download_status
                FROM {table_name}
                WHERE {' AND '.join(where)}
                ORDER BY create_time DESC
                LIMIT {limit};
            """
            rows = sqlcipher_query(db_path, db_key, query)

            for row in rows:
                parts = row.split("|") if "|" in row else row.split()
                if len(parts) >= 4:
                    try:
                        msg = {
                            "local_id": int(parts[0]),
                            "local_type": int(parts[1]),
                            "create_time": int(parts[2]),
                            "real_sender_id": int(parts[3]),
                            "status": int(parts[4]) if len(parts) > 4 else 0,
                            "download_status": int(parts[5]) if len(parts) > 5 else 0,
                            "local_type": int(parts[1]),
                            "base_type": get_base_type(int(parts[1])),
                            "type_name": get_type_name(int(parts[1])),
                            "db_source": db_name,
                            "table_name": table_name,
                            "time_str": datetime.fromtimestamp(int(parts[2])).strftime("%Y-%m-%d %H:%M:%S"),
                        }
                        all_messages.append(msg)
                    except (ValueError, IndexError):
                        continue

    # 按时间排序
    all_messages.sort(key=lambda x: x["create_time"], reverse=True)
    return all_messages[:limit]


def get_message_types_stats():
    """统计所有消息类型（按 base_type 低32位统计）"""
    stats = {}

    for db_name in ["message_0", "message_1", "message_2"]:
        db_key = MSG_DB_KEYS.get(db_name)
        db_path = DB_PATHS.get(db_name)
        if not db_key or not db_path or not db_path.exists():
            continue

        tables = sqlcipher_query(db_path, db_key,
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%';")

        for table_name in tables:
            rows = sqlcipher_query(db_path, db_key,
                f"SELECT local_type, count(*) FROM {table_name} GROUP BY local_type;")
            for row in rows:
                parts = row.split("|") if "|" in row else row.split()
                if len(parts) >= 2:
                    try:
                        raw_type = int(parts[0])
                        base_type = get_base_type(raw_type)
                        count = int(parts[1])
                        stats[base_type] = stats.get(base_type, 0) + count
                    except ValueError:
                        continue

    return stats


def get_voice_messages(since_time=0, limit=50):
    """从 media_0.db 获取语音消息（含BLOB，可直接转写）"""
    if not MEDIA_DB_KEY or not DB_PATHS["media"].exists():
        return []

    where = f"create_time > {since_time}" if since_time > 0 else "1=1"
    query = f"""
        SELECT chat_name_id, create_time, local_id, svr_id, length(voice_data), data_index
        FROM VoiceInfo
        WHERE {where}
        ORDER BY create_time DESC
        LIMIT {limit};
    """
    rows = sqlcipher_query(DB_PATHS["media"], MEDIA_DB_KEY, query)

    voices = []
    for row in rows:
        parts = row.split("|") if "|" in row else row.split()
        if len(parts) >= 5:
            try:
                chat_name_id = int(parts[0])
                wxid = get_chat_name_id_to_wxid(chat_name_id)
                contact = get_contact_info(wxid)
                voice = {
                    "chat_name_id": chat_name_id,
                    "chat_wxid": wxid,
                    "chat_display": contact["display_name"],
                    "create_time": int(parts[1]),
                    "local_id": int(parts[2]),
                    "svr_id": parts[3],
                    "size_bytes": int(parts[4]),
                    "data_index": parts[5] if len(parts) > 5 else "",
                    "time_str": datetime.fromtimestamp(int(parts[1])).strftime("%Y-%m-%d %H:%M:%S"),
                    "type_name": "语音",
                }
                voices.append(voice)
            except (ValueError, IndexError):
                continue

    return voices


# ============================================================
# 命令实现
# ============================================================

def cmd_list_types():
    """列出所有消息类型统计"""
    load_keys()
    stats = get_message_types_stats()

    print("=" * 70)
    print("微信消息类型统计")
    print("=" * 70)
    print(f"{'local_type':<15} {'类型名称':<15} {'数量':<10}")
    print("-" * 70)

    total = 0
    for msg_type, count in sorted(stats.items(), key=lambda x: x[1], reverse=True):
        type_name = get_type_name(msg_type)
        print(f"{msg_type:<15} {type_name:<15} {count:<10}")
        total += count

    print("-" * 70)
    print(f"{'合计':<15} {'':<15} {total:<10}")
    print()


def cmd_list_messages(since=None, chat=None, msg_type=None, limit=20):
    """列出消息"""
    load_keys()

    since_time = 0
    if since:
        try:
            since_time = int(datetime.strptime(since, "%Y-%m-%d").timestamp())
        except ValueError:
            print(f"❌ 日期格式错误: {since}，应为 YYYY-MM-DD", file=sys.stderr)
            return

    messages = collect_all_messages(
        since_time=since_time,
        chat_filter=chat,
        type_filter=msg_type,
        limit=limit
    )

    print("=" * 90)
    print(f"消息列表（共 {len(messages)} 条）")
    print("=" * 90)
    print(f"{'时间':<20} {'类型':<10} {'local_id':<12} {'来源库':<12}")
    print("-" * 90)

    for msg in messages:
        print(f"{msg['time_str']:<20} {msg['type_name']:<10} {msg['local_id']:<12} {msg['db_source']:<12}")

    print()


def cmd_stats():
    """消息统计"""
    load_keys()
    stats = get_message_types_stats()
    voices = get_voice_messages(limit=1000)

    print("=" * 70)
    print("微信消息统计")
    print("=" * 70)
    print(f"总消息类型数: {len(stats)}")
    print(f"总消息数: {sum(stats.values())}")
    print(f"语音消息数（media_0.db）: {len(voices)}")
    print()

    # 最近24小时消息
    yesterday = int(time.time()) - 86400
    recent = collect_all_messages(since_time=yesterday, limit=1000)
    print(f"最近24小时消息数: {len(recent)}")

    # 按类型统计最近24小时
    recent_types = {}
    for msg in recent:
        t = msg["type_name"]
        recent_types[t] = recent_types.get(t, 0) + 1

    if recent_types:
        print()
        print("最近24小时消息类型分布:")
        for t, c in sorted(recent_types.items(), key=lambda x: x[1], reverse=True):
            print(f"  {t}: {c}")

    print()


def cmd_monitor(interval=30):
    """实时监控新消息"""
    load_keys()

    # 加载状态
    state = {"last_check_time": int(time.time()) - 60, "total_messages": 0}
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())

    print("🎙️  微信消息监控已启动")
    print(f"   轮询间隔: {interval} 秒")
    print(f"   上次检查: {datetime.fromtimestamp(state['last_check_time']).strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("按 Ctrl+C 停止监控")
    print("=" * 70)

    try:
        while True:
            poll_time = datetime.now().strftime("%H:%M:%S")
            since = state["last_check_time"]

            # 采集所有新消息
            messages = collect_all_messages(since_time=since, limit=100)
            voices = get_voice_messages(since_time=since, limit=50)

            if messages or voices:
                print(f"\n[{poll_time}] 发现新消息: 普通 {len(messages)} 条, 语音 {len(voices)} 条")

                # 普通消息
                for msg in messages[-10:]:  # 最多显示10条
                    print(f"  [{msg['time_str']}] {msg['type_name']} (id={msg['local_id']})")

                # 语音消息
                for voice in voices[-5:]:  # 最多显示5条
                    print(f"  [{voice['time_str']}] 语音 from {voice['chat_display']} ({voice['size_bytes']}B)")

                state["total_messages"] += len(messages) + len(voices)

            state["last_check_time"] = int(time.time())
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

            time.sleep(interval)

    except KeyboardInterrupt:
        print()
        print("=" * 70)
        print("监控已停止")
        print(f"累计发现消息: {state['total_messages']} 条")
        print()


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="微信统一消息采集模块")
    subparsers = parser.add_subparsers(dest="command", help="命令")

    # list-types
    subparsers.add_parser("list-types", help="列出所有消息类型统计")

    # list-messages
    list_parser = subparsers.add_parser("list-messages", help="列出消息")
    list_parser.add_argument("--limit", type=int, default=20, help="显示数量")
    list_parser.add_argument("--since", type=str, help="起始日期（YYYY-MM-DD）")
    list_parser.add_argument("--chat", type=str, help="过滤会话wxid")
    list_parser.add_argument("--type", type=int, help="过滤消息类型（local_type）")

    # stats
    subparsers.add_parser("stats", help="消息统计")

    # monitor
    mon_parser = subparsers.add_parser("monitor", help="实时监控新消息")
    mon_parser.add_argument("--interval", type=int, default=30, help="轮询间隔（秒）")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "list-types":
        cmd_list_types()
    elif args.command == "list-messages":
        cmd_list_messages(
            since=args.since,
            chat=args.chat,
            msg_type=args.type,
            limit=args.limit
        )
    elif args.command == "stats":
        cmd_stats()
    elif args.command == "monitor":
        cmd_monitor(interval=args.interval)


if __name__ == "__main__":
    main()
