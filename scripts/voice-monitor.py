#!/usr/bin/env python3
"""
微信语音消息实时监控与转写工具

功能：
1. 轮询 media_0.db 的 VoiceInfo 表，发现新语音消息
2. 自动转写语音内容（FunASR SenseVoiceSmall）
3. 识别发送者（私聊：chat_name_id→wxid→昵称；群聊：real_sender_id→wxid→昵称）
4. 多人语音分别区分，新语音实时通知
5. 支持指定会话监控、全部会话监控

用法：
  python3 voice-monitor.py monitor                  # 启动监控（前台运行，Ctrl+C 停止）
  python3 voice-monitor.py monitor --interval 30   # 30秒轮询一次
  python3 voice-monitor.py monitor --chat 43        # 只监控会话43
  python3 voice-monitor.py once                      # 单次检查（不循环）
  python3 voice-monitor.py list-sessions             # 列出有语音的会话
  python3 voice-monitor.py status                    # 查看监控状态

依赖：
- sqlcipher（解密数据库）
- silk-v3-decoder（SILK → WAV）
- FunASR + SenseVoiceSmall（语音识别）
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

# ============ 配置 ============
SKILL_DIR = Path(__file__).parent.parent
WX_CLI_CONFIG = Path.home() / ".wx-cli/config.json"
ALL_KEYS_PATH = Path.home() / ".wx-cli/all_keys.json"
SILK_DECODER = SKILL_DIR / "tools/silk-v3-decoder/converter.sh"
FUNASR_VENV = Path.home() / "Doubao/chats/2026-08-26/new-chat/gaodun-course-knowledge-base/transcription/venv"
FUNASR_PYTHON = FUNASR_VENV / "bin/python"

STATE_FILE = Path.home() / ".wx-cli/voice_monitor_state.json"

# 数据库密钥（运行时加载）
MEDIA_DB_KEY = None
MSG_DB_KEY = None
CONTACT_DB_KEY = None
DB_PATHS = {}


def get_db_dir_from_config():
    """从 wx-cli 配置文件读取 db_dir（优先），回退到遍历最新账号目录"""
    # 优先从配置文件读取
    if WX_CLI_CONFIG.exists():
        try:
            config = json.loads(WX_CLI_CONFIG.read_text())
            db_dir = config.get("db_dir", "")
            if db_dir and Path(db_dir).exists():
                return Path(db_dir)
        except:
            pass

    # 回退：遍历 com.tencent.xinWeChat 下最新修改的账号目录
    wechat_files = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
    accounts = []
    if wechat_files.exists():
        for d in wechat_files.iterdir():
            if d.is_dir() and d.name.startswith("wxid_"):
                db = d / "db_storage/message/media_0.db"
                if db.exists():
                    accounts.append((db.stat().st_mtime, d))
    if accounts:
        accounts.sort(reverse=True)
        return accounts[0][1] / "db_storage"

    return None


def load_keys():
    """从 all_keys.json 加载所有数据库密钥"""
    global MEDIA_DB_KEY, MSG_DB_KEY, CONTACT_DB_KEY, DB_PATHS
    if not ALL_KEYS_PATH.exists():
        print("❌ 未找到 all_keys.json，请先运行 wx init --force", file=sys.stderr)
        sys.exit(1)

    keys = json.loads(ALL_KEYS_PATH.read_text())

    # 从配置文件获取 db_dir（优先），回退到遍历最新账号目录
    db_dir = get_db_dir_from_config()
    if not db_dir:
        print("❌ 未找到微信数据目录", file=sys.stderr)
        sys.exit(1)

    print(f"📁 数据目录: {db_dir}")

    DB_PATHS = {
        "media": db_dir / "message/media_0.db",
        "message": db_dir / "message/message_0.db",
        "message_1": db_dir / "message/message_1.db",
        "message_2": db_dir / "message/message_2.db",
        "contact": db_dir / "contact/contact.db",
    }

    # 存储所有 message 数据库的密钥
    MSG_DB_KEYS = {}

    for db_path, key_info in keys.items():
        key = key_info.get("enc_key", "") if isinstance(key_info, dict) else key_info
        if "media_0.db" in db_path:
            MEDIA_DB_KEY = key
        elif "message_0.db" in db_path:
            MSG_DB_KEY = key
            MSG_DB_KEYS["message"] = key
        elif "message_1.db" in db_path:
            MSG_DB_KEYS["message_1"] = key
        elif "message_2.db" in db_path:
            MSG_DB_KEYS["message_2"] = key
        elif "contact.db" in db_path:
            CONTACT_DB_KEY = key

    # 把 MSG_DB_KEYS 存到全局变量
    globals()["MSG_DB_KEYS"] = MSG_DB_KEYS

    if not MEDIA_DB_KEY:
        print("❌ 未找到 media_0.db 密钥", file=sys.stderr)
        sys.exit(1)


def sqlcipher_query(db_path, db_key, query, params=None):
    """执行 SQLCipher 查询"""
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


def get_chat_name(chat_name_id):
    """通过 Name2Id 表获取会话 wxid"""
    if not MEDIA_DB_KEY:
        return f"unknown_{chat_name_id}"
    rows = sqlcipher_query(
        DB_PATHS["media"], MEDIA_DB_KEY,
        f"SELECT user_name FROM Name2Id WHERE rowid = {chat_name_id};"
    )
    return rows[0] if rows else f"unknown_{chat_name_id}"


def get_contact_info(wxid):
    """通过 contact.db 获取详细联系人信息
    返回 dict: {wxid, nick_name, remark, alias, display_name, is_group}
    """
    if not CONTACT_DB_KEY or not wxid or wxid.startswith("unknown_"):
        return {"wxid": wxid, "nick_name": "", "remark": "", "alias": "", "display_name": wxid, "is_group": False}

    is_group = "@chatroom" in wxid or "@openim" in wxid

    rows = sqlcipher_query(
        DB_PATHS["contact"], CONTACT_DB_KEY,
        f"SELECT remark, nick_name, alias, local_type FROM contact WHERE username = '{wxid}';"
    )
    if rows:
        parts = rows[0].split("|") if "|" in rows[0] else rows[0].split()
        if len(parts) >= 2:
            remark = parts[0].strip()
            nick_name = parts[1].strip()
            alias = parts[2].strip() if len(parts) > 2 else ""
            local_type = parts[3].strip() if len(parts) > 3 else ""
            # 显示名称优先级：备注名 > 微信昵称 > alias > wxid
            display_name = remark or nick_name or alias or wxid
            return {
                "wxid": wxid,
                "nick_name": nick_name,
                "remark": remark,
                "alias": alias,
                "display_name": display_name,
                "is_group": is_group,
                "local_type": local_type,
            }
    return {"wxid": wxid, "nick_name": "", "remark": "", "alias": "", "display_name": wxid, "is_group": is_group}


def get_nickname(wxid):
    """获取显示名称（兼容旧接口）"""
    info = get_contact_info(wxid)
    return info["display_name"]


def get_group_sender(local_id, chat_wxid):
    """群聊中通过 message_0.db / message_1.db / message_2.db 获取真实发送者"""
    import hashlib
    table_md5 = hashlib.md5(chat_wxid.encode()).hexdigest()
    table_name = f"Msg_{table_md5}"

    # 遍历所有 message 数据库
    for db_name in ["message", "message_1", "message_2"]:
        db_key = globals().get("MSG_DB_KEYS", {}).get(db_name)
        db_path = DB_PATHS.get(db_name)
        if not db_key or not db_path or not db_path.exists():
            continue

        rows = sqlcipher_query(
            db_path, db_key,
            f"SELECT real_sender_id FROM {table_name} WHERE local_id = {local_id};"
        )
        if rows:
            try:
                sender_id = int(rows[0].split("|")[0] if "|" in rows[0] else rows[0].split()[0])
                if sender_id > 0:
                    # 通过 Name2Id 映射到 wxid
                    sender_rows = sqlcipher_query(
                        db_path, db_key,
                        f"SELECT user_name FROM Name2Id WHERE rowid = {sender_id};"
                    )
                    if sender_rows:
                        return sender_rows[0].split("|")[0] if "|" in sender_rows[0] else sender_rows[0]
            except (ValueError, IndexError):
                pass
    return "unknown"


def extract_voice_blob(local_id, chat_name_id, output_path):
    """从 VoiceInfo 表提取语音 BLOB（必须同时用 local_id + chat_name_id 定位）"""
    query = f"SELECT hex(voice_data) FROM VoiceInfo WHERE local_id = {local_id} AND chat_name_id = {chat_name_id};"
    rows = sqlcipher_query(DB_PATHS["media"], MEDIA_DB_KEY, query)
    if not rows:
        return False

    hex_data = None
    for row in rows:
        row = row.strip()
        if row and len(row) > 10:
            hex_data = row
            break

    if not hex_data:
        return False

    try:
        blob = bytes.fromhex(hex_data)
        # 找到 #!SILK_V3 开始的位置（去掉前面的头部字节）
        silk_header = b"#!SILK_V3"
        idx = blob.find(silk_header)
        if idx > 0:
            blob = blob[idx:]
        output_path.write_bytes(blob)
        return True
    except Exception:
        return False


def silk_to_wav(silk_path, wav_path):
    """SILK V3 → WAV"""
    cmd = ["sh", str(SILK_DECODER), str(silk_path), "wav"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    generated = silk_path.with_suffix(".wav")
    if generated.exists():
        if generated != wav_path:
            generated.rename(wav_path)
        return True
    return False


def transcribe_wav(wav_path):
    """用 FunASR 转文字"""
    script = f'''
import warnings
warnings.filterwarnings("ignore")
from funasr import AutoModel

model = AutoModel(model="iic/SenseVoiceSmall", device="cpu", disable_update=True)
res = model.generate(input="{wav_path}", cache={{}}, language="auto", use_itn=True)
text = res[0].get("text", "")
tags_to_remove = [
    "<|zh|>", "<|en|>", "<|yue|>", "<|ja|>", "<|ko|>",
    "<|nospeech|>", "<|Speech|>", "<|withitn|>", "<|woitn|>",
    "<|HAPPY|>", "<|SAD|>", "<|ANGRY|>", "<|NEUTRAL|>",
    "<|FEARFUL|>", "<|DISGUSTED|>", "<|SURPRISED|>",
]
for tag in tags_to_remove:
    text = text.replace(tag, "")
print(text.strip())
'''
    result = subprocess.run(
        [str(FUNASR_PYTHON), "-c", script],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        return f"[识别失败]"
    lines = result.stdout.strip().split("\n")
    return lines[-1].strip() if lines else ""


def load_state():
    """加载监控状态"""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        "last_voice_time": 0,
        "processed_voices": [],
        "monitor_start_time": "",
        "total_transcribed": 0,
    }


def save_state(state):
    """保存监控状态"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def get_new_voices(since_time=0, chat_filter=None):
    """获取新语音消息"""
    where = f"create_time > {since_time}" if since_time > 0 else "1=1"
    if chat_filter:
        where += f" AND chat_name_id = {chat_filter}"

    query = f"SELECT chat_name_id, create_time, local_id, length(voice_data) FROM VoiceInfo WHERE {where} ORDER BY create_time ASC;"
    rows = sqlcipher_query(DB_PATHS["media"], MEDIA_DB_KEY, query)
    voices = []
    for row in rows:
        parts = row.split("|") if "|" in row else row.split()
        if len(parts) >= 4:
            try:
                voices.append({
                    "chat_name_id": int(parts[0]),
                    "create_time": int(parts[1]),
                    "local_id": int(parts[2]),
                    "size": int(parts[3]),
                })
            except (ValueError, IndexError):
                continue
    return voices


def process_voice(voice):
    """处理单条语音：转写 + 识别发送者"""
    chat_name_id = voice["chat_name_id"]
    local_id = voice["local_id"]
    create_time = voice["create_time"]
    time_str = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S")

    # 获取会话信息
    chat_wxid = get_chat_name(chat_name_id)
    chat_info = get_contact_info(chat_wxid)
    is_group = chat_info["is_group"]

    # 获取发送者信息
    if is_group:
        sender_wxid = get_group_sender(local_id, chat_wxid)
        sender_info = get_contact_info(sender_wxid)
        chat_display = f"群聊: {chat_info['display_name']}"
    else:
        sender_wxid = chat_wxid
        sender_info = chat_info
        chat_display = f"私聊: {sender_info['display_name']}"

    # 转写语音
    text = ""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        silk_path = tmpdir / f"voice_{local_id}.silk"
        wav_path = tmpdir / f"voice_{local_id}.wav"

        if extract_voice_blob(local_id, chat_name_id, silk_path):
            if silk_to_wav(silk_path, wav_path):
                text = transcribe_wav(wav_path)

    return {
        "time": time_str,
        "chat_display": chat_display,
        "chat_wxid": chat_wxid,
        "chat_nick_name": chat_info["nick_name"],
        "chat_remark": chat_info["remark"],
        "is_group": is_group,
        "sender_wxid": sender_wxid,
        "sender_nick_name": sender_info["nick_name"],
        "sender_remark": sender_info["remark"],
        "sender_alias": sender_info["alias"],
        "sender_display": sender_info["display_name"],
        "local_id": local_id,
        "chat_name_id": chat_name_id,
        "size": voice["size"],
        "text": text,
    }


def cmd_monitor(interval=60, chat_filter=None, once=False):
    """启动语音监控"""
    load_keys()
    state = load_state()

    # 首次启动时从当前时间前30秒开始监控（缓冲时间，避免漏掉刚发的语音）
    if state["last_voice_time"] == 0:
        state["last_voice_time"] = int(time.time()) - 30
        print(f"ℹ️  首次启动，从30秒前开始监控（缓冲时间）")

    if not state["monitor_start_time"]:
        state["monitor_start_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("🎙️  微信语音监控已启动")
    print(f"   数据库: {DB_PATHS['media']}")
    print(f"   轮询间隔: {interval} 秒")
    print(f"   监控范围: {'全部会话' if not chat_filter else f'会话 {chat_filter}'}")
    print(f"   上次处理时间: {datetime.fromtimestamp(state['last_voice_time']).strftime('%Y-%m-%d %H:%M:%S') if state['last_voice_time'] else '无'}")
    print()
    print("按 Ctrl+C 停止监控")
    print("=" * 60)

    try:
        while True:
            poll_time = datetime.now().strftime("%H:%M:%S")
            since = state["last_voice_time"]
            new_voices = get_new_voices(since_time=since, chat_filter=chat_filter)

            if new_voices:
                print(f"\n[{poll_time}] 发现 {len(new_voices)} 条新语音，开始处理...")
                for i, voice in enumerate(new_voices):
                    print(f"  [{i+1}/{len(new_voices)}] 处理中...", end=" ", flush=True)
                    result = process_voice(voice)
                    print(f"✓")

                    # 输出通知
                    print()
                    print("🔔 新语音消息")
                    print("-" * 50)
                    print(f"  时间: {result['time']}")
                    print(f"  会话: {result['chat_display']}")
                    if result['is_group']:
                        print(f"  发送者: {result['sender_display']}")
                        if result['sender_remark']:
                            print(f"    备注名: {result['sender_remark']}")
                        if result['sender_nick_name']:
                            print(f"    微信昵称: {result['sender_nick_name']}")
                        print(f"    微信号: {result['sender_wxid']}")
                    else:
                        if result['sender_remark']:
                            print(f"  备注名: {result['sender_remark']}")
                        if result['sender_nick_name']:
                            print(f"  微信昵称: {result['sender_nick_name']}")
                        print(f"  微信号: {result['sender_wxid']}")
                    print(f"  语音大小: {result['size']} B")
                    print(f"  转写内容: {result['text']}")
                    print("-" * 50)
                    print()

                    # 记录已处理
                    voice_key = f"{voice['chat_name_id']}_{voice['local_id']}_{voice['create_time']}"
                    state["processed_voices"].append(voice_key)
                    state["total_transcribed"] = state.get("total_transcribed", 0) + 1
                    if voice["create_time"] > state["last_voice_time"]:
                        state["last_voice_time"] = voice["create_time"]

                # 限制已处理列表大小
                if len(state["processed_voices"]) > 1000:
                    state["processed_voices"] = state["processed_voices"][-1000:]

                save_state(state)
            else:
                print(f"[{poll_time}] 无新语音")

            if once:
                print("\n✅ 单次检查完成")
                break

            time.sleep(interval)

    except KeyboardInterrupt:
        print()
        print("=" * 60)
        print("监控已停止")
        print(f"累计转写: {state.get('total_transcribed', 0)} 条语音")
        print()


def cmd_list_sessions():
    """列出有语音的会话"""
    load_keys()
    query = "SELECT chat_name_id, count(*), max(create_time) FROM VoiceInfo GROUP BY chat_name_id ORDER BY max(create_time) DESC LIMIT 20;"
    rows = sqlcipher_query(DB_PATHS["media"], MEDIA_DB_KEY, query)

    print(f"{'ID':<6} {'语音数':<6} {'最后语音时间':<20} {'显示名称':<20} {'备注名':<15} {'微信昵称':<15} {'微信号'}")
    print("-" * 120)
    for row in rows:
        parts = row.split("|") if "|" in row else row.split()
        if len(parts) >= 3:
            chat_id = parts[0]
            count = parts[1]
            last_time = datetime.fromtimestamp(int(parts[2])).strftime("%Y-%m-%d %H:%M:%S")
            wxid = get_chat_name(int(chat_id))
            info = get_contact_info(wxid)
            print(f"{chat_id:<6} {count:<6} {last_time:<20} {info['display_name'][:18]:<20} {info['remark'][:13]:<15} {info['nick_name'][:13]:<15} {wxid}")


def cmd_status():
    """查看监控状态"""
    state = load_state()
    print("📊 语音监控状态")
    print("-" * 40)
    print(f"  启动时间: {state.get('monitor_start_time', '未启动')}")
    print(f"  累计转写: {state.get('total_transcribed', 0)} 条")
    print(f"  上次处理: {datetime.fromtimestamp(state['last_voice_time']).strftime('%Y-%m-%d %H:%M:%S') if state.get('last_voice_time') else '无'}")
    print(f"  已处理记录: {len(state.get('processed_voices', []))} 条")


def main():
    parser = argparse.ArgumentParser(description="微信语音消息实时监控与转写")
    subparsers = parser.add_subparsers(dest="command", help="命令")

    # monitor 命令
    mon_parser = subparsers.add_parser("monitor", help="启动语音监控")
    mon_parser.add_argument("--interval", type=int, default=60, help="轮询间隔（秒）")
    mon_parser.add_argument("--chat", type=str, help="只监控指定会话ID")
    mon_parser.add_argument("--once", action="store_true", help="单次检查后退出")

    # once 命令（简写）
    subparsers.add_parser("once", help="单次检查新语音")

    # list-sessions 命令
    subparsers.add_parser("list-sessions", help="列出有语音的会话")

    # status 命令
    subparsers.add_parser("status", help="查看监控状态")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "monitor":
        cmd_monitor(
            interval=args.interval,
            chat_filter=args.chat,
            once=args.once
        )
    elif args.command == "once":
        cmd_monitor(once=True)
    elif args.command == "list-sessions":
        cmd_list_sessions()
    elif args.command == "status":
        cmd_status()


if __name__ == "__main__":
    main()
