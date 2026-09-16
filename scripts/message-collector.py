#!/usr/bin/env python3
"""
微信统一消息采集模块
整合 wx-cli（文本）、media_0.db（语音）、message_0/1/2.db（所有消息元数据）
支持所有消息类型的识别和统一输出。

用法：
  python3 message-collector.py list-types              # 列出所有消息类型统计
  python3 message-collector.py list-messages --limit 20  # 列出最近消息（元数据）
  python3 message-collector.py list-messages --since "2026-09-10" --type 1  # 按时间和类型过滤
  python3 message-collector.py parse --limit 20        # 解析最近消息（类型识别+内容解析）
  python3 message-collector.py parse --json --limit 5  # JSON 格式输出
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
# 编码规则：local_type = (appmsg_type << 32) | base_type；base_type=49 为 appmsg 卡片类
EXTENDED_TYPE_HINTS = {
    25769803825: "文件",       # appmsg type 6
    81604378673: "合并聊天记录", # appmsg type 19
    21474836529: "红包",       # appmsg type 5（微信红包卡片）
    141733920817: "转账",      # appmsg type 33
    8589934592049: "文件",     # appmsg type 2000（文件传输）
    219043332145: "引用消息",  # appmsg type 51
    244813135921: "引用消息",  # appmsg type 57（带 refermsg 的引用/回复）
    266287972401: "小程序",
    270582939697: "公众号",
    227633266737: "链接卡片",
}

# appmsg 子类型映射（base_type=49 时，高32位解码）
APPMSG_TYPES = {
    5: "链接",
    6: "文件",
    19: "合并聊天记录",
    24: "红包",
    33: "小程序",
    51: "引用消息",
    57: "引用消息",
    2000: "文件",
}


def get_base_type(local_type):
    """获取消息真实类型（local_type 低32位）
    wx-cli: base = (local_type as u64 & 0xFFFFFFFF) as i64
    高32位是版本/会话flag，低32位才是真实类型
    """
    return local_type & 0xFFFFFFFF


def get_type_name(local_type):
    """获取消息类型名称。
    优先级：完整 local_type 精确匹配 → appmsg 子类型解码（base=49 时）→ base_type 映射 → 未知
    """
    # 1. 精确匹配大 local_type
    if local_type in EXTENDED_TYPE_HINTS:
        return EXTENDED_TYPE_HINTS[local_type]
    base = get_base_type(local_type)
    # 2. base=49 (appmsg 卡片类)：解码高32位为 appmsg 子类型
    if base == 49:
        appmsg_type = local_type >> 32
        if appmsg_type in APPMSG_TYPES:
            return APPMSG_TYPES[appmsg_type]
        return f"卡片(类型{appmsg_type})"
    # 3. base_type 映射
    if base in MESSAGE_TYPES:
        return MESSAGE_TYPES[base]
    return f"未知(type={base})"


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
    try:
        result = subprocess.run(
            ["sqlcipher", str(db_path)],
            input=full_sql.encode('utf-8'),
            capture_output=True,
            timeout=30
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if result.returncode != 0:
        return []
    # 用 replace 容错：message_content 可能含二进制 BLOB
    output = result.stdout.decode('utf-8', errors='replace')
    lines = output.strip().split("\n")
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
    """获取会话对应的表名 Msg_<MD5(完整wxid)>
    与 wx-cli src/daemon/query.rs find_msg_shards() 一致：
        table_name = format!("Msg_{:x}", md5::compute(username.as_bytes()))
    username 为完整会话 wxid（不对"短 wxid"做截断）。
    注：collect_all_messages() 直接枚举 sqlite_master 的 Msg_% 表，不依赖本函数。
    """
    table_md5 = hashlib.md5(wxid.encode()).hexdigest()
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
# 消息内容解析（统一解析器）
# ============================================================

import re as _re

def _xml_get(xml_str, tag):
    """从 XML 字符串中提取第一个 <tag>...</tag> 的文本内容（容错正则）"""
    if not xml_str:
        return None
    m = _re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', xml_str, _re.DOTALL)
    return m.group(1).strip() if m else None


def _xml_get_all(xml_str, tag):
    """提取所有 <tag>...</tag> 的文本内容列表"""
    if not xml_str:
        return []
    return _re.findall(rf'<{tag}[^>]*>(.*?)</{tag}>', xml_str, _re.DOTALL)


def zstd_decompress_hex(hex_data):
    """用 zstd CLI 解压 hex 编码的 zstd BLOB，返回解压后的文本"""
    if not hex_data:
        return None
    try:
        raw = bytes.fromhex(hex_data)
        result = subprocess.run(['zstd', '-d', '-c'], input=raw, capture_output=True, timeout=10)
        if result.returncode == 0:
            return result.stdout.decode('utf-8', errors='replace')
    except (ValueError, subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def fetch_raw_content(msg):
    """从数据库获取单条消息的原始内容。
    返回 (content_text, compress_type)：
    - content_text: 文本内容或解压后的文本
    - compress_type: WCDB_CT_message_content（0=普通文本，4=zstd压缩）
    """
    db_name = msg.get('db_source', '')
    table_name = msg.get('table_name', '')
    local_id = msg.get('local_id', 0)
    db_key = MSG_DB_KEYS.get(db_name)
    db_path = DB_PATHS.get(db_name)
    if not db_key or not db_path or not db_path.exists():
        return None, 0

    try:
        # 先查压缩类型和文本内容
        rows = sqlcipher_query(db_path, db_key,
            f"SELECT WCDB_CT_message_content, message_content FROM {table_name} WHERE local_id = {local_id};")
        if not rows:
            return None, 0

        parts = rows[0].split("|", 1) if "|" in rows[0] else [rows[0], ""]
        try:
            compress_type = int(parts[0]) if parts[0].strip() else 0
        except ValueError:
            compress_type = 0
        text_content = parts[1] if len(parts) > 1 else ""

        # zstd 压缩：需要用 hex() 重新取 BLOB
        if compress_type == 4:
            hex_rows = sqlcipher_query(db_path, db_key,
                f"SELECT hex(message_content) FROM {table_name} WHERE local_id = {local_id};")
            if hex_rows:
                decompressed = zstd_decompress_hex(hex_rows[0].strip())
                if decompressed:
                    return decompressed, compress_type
            return text_content, compress_type

        return text_content, compress_type
    except Exception:
        return None, 0


# ---- 各类型解析器 ----

def parse_text(raw):
    """文本消息：群聊格式为 'sender_wxid:\\n内容'，分离发送者"""
    if not raw:
        return {}
    # 群聊文本：第一行是发送者 wxid（含冒号），后面是内容
    lines = raw.split('\n', 1)
    if len(lines) == 2 and lines[0].endswith(':') and len(lines[0]) < 50:
        return {'sender': lines[0][:-1], 'text': lines[1]}
    return {'text': raw}


def parse_image(raw):
    if not raw:
        return {}
    return {
        'img_path': _xml_get(raw, 'imgpath') or _xml_get(raw, 'aespath'),
        'md5': _xml_get(raw, 'md5'),
        'note': '可用 wx attachments + wx extract 解密导出',
    }


def parse_voice(msg):
    """语音消息：从 media_0.db VoiceInfo 取语音数据，提示可转写"""
    result = {'note': '可用 voice-transcribe.py 转写'}
    if not MEDIA_DB_KEY or not DB_PATHS.get('media'):
        return result
    # 通过 local_id 查 VoiceInfo
    rows = sqlcipher_query(DB_PATHS['media'], MEDIA_DB_KEY,
        f"SELECT length(voice_data), data_index FROM VoiceInfo WHERE local_id = {msg['local_id']};")
    if rows:
        parts = rows[0].split("|")
        if len(parts) >= 1:
            result['voice_size_bytes'] = parts[0].strip()
    return result


def parse_video(raw):
    if not raw:
        return {}
    return {
        'duration': _xml_get(raw, 'playlength') or _xml_get(raw, 'duration'),
        'md5': _xml_get(raw, 'md5'),
        'img_path': _xml_get(raw, 'imgpath'),
        'note': 'wx-cli 不支持视频解密导出；仅元数据可读',
    }


def parse_location(raw):
    if not raw:
        return {}
    return {
        'label': _xml_get(raw, 'label') or _xml_get(raw, 'poiname'),
        'address': _xml_get(raw, 'addr') or _xml_get(raw, 'address'),
        'lat': _xml_get(raw, 'x') or _xml_get(raw, 'lat'),
        'lng': _xml_get(raw, 'y') or _xml_get(raw, 'lng'),
    }


def parse_contact_card(raw):
    if not raw:
        return {}
    return {
        'nickname': _xml_get(raw, 'nickname') or _xml_get(raw, 'nick'),
        'username': _xml_get(raw, 'username') or _xml_get(raw, 'alias'),
        'province': _xml_get(raw, 'province'),
        'city': _xml_get(raw, 'city'),
        'sex': _xml_get(raw, 'sex'),
    }


def parse_link_or_file(raw):
    """通用链接/文件/卡片/引用消息解析：提取 title/des/url + refermsg 引用内容"""
    if not raw:
        return {}
    result = {
        'title': _xml_get(raw, 'title'),
        'description': _xml_get(raw, 'des') or _xml_get(raw, 'description'),
        'url': _xml_get(raw, 'url') or _xml_get(raw, 'lowurl'),
        'appname': _xml_get(raw, 'appname'),
        'appmsg_type': _xml_get(raw, 'type'),
    }
    # 引用消息：提取被引用的原文
    refer_type = _xml_get(raw, 'type')
    refer_from = _xml_get(raw, 'fromusr') or _xml_get(raw, 'from_nickname')
    refer_content = _xml_get(raw, 'content')
    if refer_type or refer_from or refer_content:
        result['referenced'] = {
            'msg_type': refer_type,
            'from': refer_from,
            'content': refer_content[:200] if refer_content else None,
        }
    # 去掉 None 值
    return {k: v for k, v in result.items() if v is not None and v != ''}


def parse_call(raw):
    if not raw:
        return {}
    return {
        'duration': _xml_get(raw, 'duration') or _xml_get(raw, 'talktime'),
        'call_type': _xml_get(raw, 'calltype'),
        'status': _xml_get(raw, 'status'),
    }


def parse_red_packet(raw):
    if not raw:
        return {}
    return {
        'title': _xml_get(raw, 'writetitle') or _xml_get(raw, 'title'),
        'description': _xml_get(raw, 'des') or _xml_get(raw, 'sendtitle'),
        'sender': _xml_get(raw, 'sendusername') or _xml_get(raw, 'sendnickname'),
        'note': '红包金额需在微信内领取查看，本地库不存金额',
    }


def parse_transfer(raw):
    if not raw:
        return {}
    return {
        'fee_desc': _xml_get(raw, 'feedesc') or _xml_get(raw, 'pay_memo'),
        'transfer_id': _xml_get(raw, 'transcationid') or _xml_get(raw, 'transferid'),
        'note': '转账金额/状态需在微信内查看，本地库仅存描述',
    }


def parse_merged_record(raw):
    """合并聊天记录解析：解压后解析 recorditem，拆出每条子消息"""
    if not raw:
        return {'note': '内容为空或解压失败', 'sub_messages': []}

    sub_messages = []
    # 提取所有 recorditem
    items = _xml_get_all(raw, 'recorditem')
    for item in items:
        sub = {
            'datatime': _xml_get(item, 'datatime'),
            'sourcename': _xml_get(item, 'sourcename'),
            'title': _xml_get(item, 'title'),
            'desc': _xml_get(item, 'desc'),
            'data_type': _xml_get(item, 'datatype'),
        }
        # 从 recorditem 中提取 URL（完整条目在整条 XML 中）
        urls = _re.findall(r"https?://[^\s<>\"']+", item)
        if urls:
            sub['urls'] = urls
        sub_messages.append(sub)

    # 也从整条 XML 提取所有 URL（合并记录里的链接可能不在 recorditem 内）
    all_urls = _re.findall(r"https?://[^\s<>\"']+", raw)
    # 去重保序
    seen = set()
    unique_urls = []
    for u in all_urls:
        if u not in seen:
            seen.add(u)
            unique_urls.append(u)

    return {
        'sub_message_count': len(sub_messages),
        'sub_messages': sub_messages,
        'all_urls': unique_urls[:50],  # 最多保留 50 条
        'note': f'已拆分 {len(sub_messages)} 条子消息；desc 仅前 5 条摘要，完整内容见 sub_messages',
    }


def parse_system(raw):
    if not raw:
        return {}
    # 系统消息内容通常是纯文本描述
    return {'text': raw[:200] if raw else ''}


# ---- 统一分发 ----

def parse_message(msg):
    """统一消息解析：识别类型 + 尽可能解析内容。
    对于无法解析内容的类型，至少返回类型标识。
    """
    type_name = msg.get('type_name', '未知')
    result = {
        'local_id': msg.get('local_id'),
        'local_type': msg.get('local_type'),
        'type': type_name,
        'time': msg.get('time_str'),
        'sender_id': msg.get('real_sender_id'),
        'content': None,
        'parsed': {},
        'note': None,
    }

    # 语音不需要查 message_content（内容在 media_0.db）
    if type_name == '语音':
        result['parsed'] = parse_voice(msg)
        return result

    # 其他类型查原始内容
    raw, compress_type = fetch_raw_content(msg)
    result['content_compress_type'] = compress_type
    if raw is not None and not raw.strip():
        raw = None

    dispatch = {
        '文本': parse_text,
        '图片': parse_image,
        '视频': parse_video,
        '位置': parse_location,
        '名片': parse_contact_card,
        '链接/文件': parse_link_or_file,
        '通话': parse_call,
        '红包': parse_red_packet,
        '转账': parse_transfer,
        '合并聊天记录': parse_merged_record,
        '系统': parse_system,
        '撤回': parse_system,
        '表情': lambda r: {'text': r[:100] if r else '', 'note': '表情/贴纸'},
        '引用消息': parse_link_or_file,
        '小程序': parse_link_or_file,
        '公众号': parse_link_or_file,
        '链接卡片': parse_link_or_file,
    }

    parser = dispatch.get(type_name)
    if parser:
        try:
            parsed = parser(raw) if type_name not in ('语音',) else parse_voice(msg)
            result['parsed'] = parsed
            # 文本类直接放 content
            if type_name in ('文本', '系统', '撤回', '表情'):
                result['content'] = parsed.get('text', '')
        except Exception as e:
            result['note'] = f'解析异常: {e}'
            result['content'] = raw[:200] if raw else None
    else:
        result['note'] = f'类型「{type_name}」暂不支持内容解析，仅识别类型'
        result['content'] = raw[:200] if raw else None

    return result


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

def cmd_parse(since=None, limit=20, msg_type=None, output_json=False):
    """解析最近消息：识别类型 + 解析内容"""
    load_keys()

    since_time = 0
    if since:
        try:
            since_time = int(datetime.strptime(since, "%Y-%m-%d").timestamp())
        except ValueError:
            print(f"❌ 日期格式错误: {since}，应为 YYYY-MM-DD", file=sys.stderr)
            return

    messages = collect_all_messages(since_time=since_time, type_filter=msg_type, limit=limit)

    if output_json:
        results = []
        for msg in messages:
            results.append(parse_message(msg))
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        return

    print("=" * 80)
    print(f"消息解析（共 {len(messages)} 条）")
    print("=" * 80)

    for i, msg in enumerate(messages, 1):
        parsed = parse_message(msg)
        print(f"\n{'─' * 78}")
        print(f"[{i}] {parsed['time']}  类型: {parsed['type']}  (id={parsed['local_id']}, local_type={parsed['local_type']})")

        if parsed['content']:
            content = parsed['content']
            if len(content) > 300:
                content = content[:300] + "..."
            print(f"  内容: {content}")

        p = parsed['parsed']
        if p:
            for k, v in p.items():
                # content 已在上方展示，不重复显示 text 键
                if k == 'text' and parsed['content']:
                    continue
                if k == 'sub_messages':
                    print(f"  子消息 ({len(v)} 条):")
                    for j, sm in enumerate(v[:5], 1):
                        sender = sm.get('sourcename', '?')
                        title = (sm.get('title') or '')[:60]
                        dt = sm.get('datatime', '')
                        print(f"    {j}. [{dt}] {sender}: {title}")
                    if len(v) > 5:
                        print(f"    ... 还有 {len(v)-5} 条")
                elif k == 'all_urls':
                    if v:
                        print(f"  链接 ({len(v)} 条):")
                        for url in v[:5]:
                            print(f"    - {url[:80]}")
                        if len(v) > 5:
                            print(f"    ... 还有 {len(v)-5} 条")
                else:
                    if v is not None and v != '':
                        val_str = str(v)
                        if len(val_str) > 100:
                            val_str = val_str[:100] + "..."
                        print(f"  {k}: {val_str}")

        if parsed['note']:
            print(f"  ⚠️  {parsed['note']}")

    print(f"\n{'=' * 80}")
    print(f"解析完成: {len(messages)} 条消息")
    print()


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

    # parse
    parse_parser = subparsers.add_parser("parse", help="解析消息（识别类型+解析内容）")
    parse_parser.add_argument("--limit", type=int, default=20, help="显示数量")
    parse_parser.add_argument("--since", type=str, help="起始日期（YYYY-MM-DD）")
    parse_parser.add_argument("--type", type=int, help="过滤消息类型（local_type）")
    parse_parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")

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
    elif args.command == "parse":
        cmd_parse(
            since=args.since,
            limit=args.limit,
            msg_type=args.type,
            output_json=args.json,
        )


if __name__ == "__main__":
    main()
