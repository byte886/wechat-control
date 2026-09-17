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
  python3 message-collector.py export --output ./out    # 导出媒体（图片解密/视频/语音/文件）
  python3 message-collector.py export --type 3 --limit 5  # 只导出图片
  python3 message-collector.py monitor --interval 30   # 实时监控新消息
  python3 message-collector.py stats                    # 消息统计
"""

import argparse
import hashlib
import html
import json
import os
import platform
import shutil
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
RESOURCE_DB_KEY = None
WXCHAT_BASE = None  # xwechat_files/<wxid> 目录，用于找 msg/video、msg/file


# ============================================================
# 工具函数
# ============================================================

def load_keys():
    """加载数据库密钥和路径"""
    global DB_PATHS, MSG_DB_KEYS, MEDIA_DB_KEY, CONTACT_DB_KEY, RESOURCE_DB_KEY, WXCHAT_BASE

    if not WX_CLI_CONFIG.exists():
        print("❌ wx-cli 配置不存在，请先运行 wx init", file=sys.stderr)
        sys.exit(1)

    config = json.loads(WX_CLI_CONFIG.read_text())
    db_dir = Path(config.get("db_dir", ""))

    if not db_dir.exists():
        print(f"❌ 数据目录不存在: {db_dir}", file=sys.stderr)
        sys.exit(1)

    # xwechat_files/<wxid> = db_dir 的父目录
    # db_dir = .../xwechat_files/<wxid>/db_storage
    WXCHAT_BASE = db_dir.parent

    DB_PATHS = {
        "message_0": db_dir / "message" / "message_0.db",
        "message_1": db_dir / "message" / "message_1.db",
        "message_2": db_dir / "message" / "message_2.db",
        "media": db_dir / "message" / "media_0.db",
        "contact": db_dir / "contact" / "contact.db",
        "session": db_dir / "session" / "session.db",
        "message_resource": db_dir / "message" / "message_resource.db",
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
            elif "message_resource" in db_path_key:
                RESOURCE_DB_KEY = enc_key


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


def _xml_attr(xml_str, tag, attr):
    """从 XML 字符串中提取第一个 <tag ... attr="value" ...> 的属性值"""
    if not xml_str:
        return None
    m = _re.search(rf'<{tag}[^>]*\s{attr}="([^"]*)"', xml_str)
    return m.group(1) if m else None


def _unescape(text):
    """解码 HTML 实体（合并记录中 &#x20; &#x0A; 等）"""
    if not text:
        return text
    return html.unescape(text)


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
    """图片消息：解析 <img> 标签属性，提取 aeskey/尺寸/大小/MD5。
    图片实际数据加密存储在 CDN，需用 aeskey 解密导出（wx attachments/extract）。"""
    if not raw:
        return {}
    result = {
        'aeskey': _xml_attr(raw, 'img', 'aeskey'),
        'md5': _xml_attr(raw, 'img', 'md5'),
        'size_bytes': _xml_attr(raw, 'img', 'length'),
        'hd_size_bytes': _xml_attr(raw, 'img', 'hdlength'),
        'thumb_width': _xml_attr(raw, 'img', 'cdnthumbwidth'),
        'thumb_height': _xml_attr(raw, 'img', 'cdnthumbheight'),
        'thumb_size': _xml_attr(raw, 'img', 'cdnthumblength'),
        'encryver': _xml_attr(raw, 'img', 'encryver'),
        'note': '图片加密存储于CDN，可用 wx attachments + wx extract 解密导出',
    }
    return {k: v for k, v in result.items() if v is not None and v != ''}


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
    """视频消息：解析 <videomsg> 标签属性，提取 aeskey/时长/大小/尺寸。
    视频实际数据加密存储于 CDN，wx-cli 不支持视频解密导出，仅元数据可读。"""
    if not raw:
        return {}
    result = {
        'aeskey': _xml_attr(raw, 'videomsg', 'aeskey'),
        'duration_sec': _xml_attr(raw, 'videomsg', 'playlength'),
        'size_bytes': _xml_attr(raw, 'videomsg', 'length'),
        'thumb_width': _xml_attr(raw, 'videomsg', 'cdnthumbwidth'),
        'thumb_height': _xml_attr(raw, 'videomsg', 'cdnthumbheight'),
        'thumb_size': _xml_attr(raw, 'videomsg', 'cdnthumblength'),
        'from_user': _xml_attr(raw, 'videomsg', 'fromusername'),
        'md5': _xml_attr(raw, 'videomsg', 'md5'),
        'note': '视频加密存储于CDN，wx-cli不支持解密导出；仅元数据可读',
    }
    return {k: v for k, v in result.items() if v is not None and v != ''}


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


# 合并聊天记录子消息 datatype 映射
MERGED_DATATYPES = {
    1: "文本",
    2: "图片",
    3: "语音",
    4: "视频",
    5: "链接",
    6: "文件",
    7: "表情",
    8: "位置",
    9: "名片",
    10: "红包",
    11: "小程序",
    19: "合并聊天记录",
}


def _parse_merged_dataitem(attrs, content, depth=0, max_depth=5):
    """解析合并记录中的单条 dataitem，支持递归嵌套合并记录"""
    dt_match = _re.search(r'datatype="(\d+)"', attrs)
    datatype = int(dt_match.group(1)) if dt_match else 0
    type_name = MERGED_DATATYPES.get(datatype, f"未知({datatype})")

    dataid_match = _re.search(r'dataid="([^"]+)"', attrs)
    desc_raw = _xml_get(content, 'datadesc')
    sub = {
        'datatype': datatype,
        'type': type_name,
        'dataid': dataid_match.group(1) if dataid_match else None,
        'sender': _unescape(_xml_get(content, 'sourcename')),
        'time': _unescape(_xml_get(content, 'sourcetime')),
        'src_create_time': _xml_get(content, 'srcMsgCreateTime'),
        'desc': _unescape(desc_raw),
    }

    # 按类型深度解析
    if datatype == 1:  # 文本
        sub['text'] = _unescape(desc_raw)
    elif datatype == 2:  # 图片
        sub['thumb_width'] = _xml_get(content, 'thumbwidth')
        sub['thumb_height'] = _xml_get(content, 'thumbheight')
        sub['thumb_size'] = _xml_get(content, 'thumbsize')
        sub['full_md5'] = _xml_get(content, 'fullmd5')
        sub['data_size'] = _xml_get(content, 'datasize')
        sub['aeskey'] = _xml_get(content, 'cdnthumbkey') or _xml_get(content, 'cdndatakey')
    elif datatype == 4:  # 视频
        sub['duration'] = _xml_get(content, 'playlength') or _xml_get(content, 'videolength')
        sub['size'] = _xml_get(content, 'datasize') or _xml_get(content, 'length')
        sub['aeskey'] = _xml_get(content, 'cdnvideokey') or _xml_get(content, 'cdndatakey')
    elif datatype == 5:  # 链接
        sub['url'] = _xml_get(content, 'url') or _xml_get(content, 'cdnurl')
        sub['title'] = _xml_get(content, 'title')
    elif datatype == 8:  # 位置
        sub['label'] = _xml_get(content, 'label') or _xml_get(content, 'poiname')
        sub['lat'] = _xml_get(content, 'x') or _xml_get(content, 'lat')
        sub['lng'] = _xml_get(content, 'y') or _xml_get(content, 'lng')
    elif datatype == 19 and depth < max_depth:  # 递归：嵌套合并聊天记录
        nested = _parse_merged_datalist(content, depth + 1, max_depth)
        if nested:
            sub['sub_messages'] = nested
            sub['sub_message_count'] = len(nested)
    elif datatype == 19:
        sub['note'] = f'嵌套合并记录，已达最大深度 {max_depth}'

    # 通用递归：任何 dataitem 内含 <datalist> 都递归解析（兼容非标准嵌套）
    if 'sub_messages' not in sub and '<datalist' in content and depth < max_depth:
        nested = _parse_merged_datalist(content, depth + 1, max_depth)
        if nested:
            sub['sub_messages'] = nested
            sub['sub_message_count'] = len(nested)
            sub['note'] = '检测到嵌套子消息'

    # 去掉 None 值
    return {k: v for k, v in sub.items() if v is not None and v != ''}


def _parse_merged_datalist(xml, depth=0, max_depth=5):
    """解析合并记录中的 <datalist>，返回子消息列表（可递归）"""
    items = _re.findall(r'<dataitem\s+([^>]*)>(.*?)</dataitem>', xml, _re.DOTALL)
    return [_parse_merged_dataitem(attrs, content, depth, max_depth) for attrs, content in items]


def parse_merged_record(raw):
    """合并聊天记录解析：zstd 解压后解析 recordinfo/datalist，
    按 datatype 深度解析每条子消息，支持递归嵌套合并记录。"""
    if not raw:
        return {'note': '内容为空或解压失败', 'sub_messages': []}

    # 提取 recordinfo（可能在 CDATA 内）
    title = _unescape(_xml_get(raw, 'title'))
    desc = _unescape(_xml_get(raw, 'desc'))

    # 解析所有 dataitem（递归）
    sub_messages = _parse_merged_datalist(raw, depth=0, max_depth=5)

    # 从整条 XML 提取所有 URL（保序去重）
    all_urls = _re.findall(r"https?://[^\s<>\"']+", raw)
    seen = set()
    unique_urls = []
    for u in all_urls:
        if u not in seen:
            seen.add(u)
            unique_urls.append(u)

    # 统计子消息类型分布
    type_counts = {}
    for s in sub_messages:
        t = s.get('type', '未知')
        type_counts[t] = type_counts.get(t, 0) + 1

    return {
        'title': title,
        'desc': desc[:300] if desc else None,
        'sub_message_count': len(sub_messages),
        'sub_message_types': type_counts,
        'sub_messages': sub_messages,
        'all_urls': unique_urls[:50],
        'note': f'已拆分 {len(sub_messages)} 条子消息，类型分布: {type_counts}',
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
# 媒体导出（图片解密 / 视频复制 / 语音提取 / 文件复制）
# ============================================================

def get_chat_wxid_from_table(table_name):
    """从表名 Msg_<md5(wxid)> 反查 chat wxid（遍历 message_resource.db ChatName2Id）"""
    if not RESOURCE_DB_KEY:
        return None
    target_md5 = table_name.replace("Msg_", "")
    rows = sqlcipher_query(DB_PATHS["message_resource"], RESOURCE_DB_KEY,
        "SELECT user_name FROM ChatName2Id;")
    for r in rows:
        wxid = r.strip()
        if wxid and hashlib.md5(wxid.encode()).hexdigest() == target_md5:
            return wxid
    return None


def extract_md5_from_packed_hex(hex_blob):
    """从 MessageResourceInfo.packed_info (protobuf hex) 提取 32 字节文件 md5。
    复刻 wx-cli resolver：主路径搜 marker 12 22 0a 20，fallback 扫连续 32 字节 hex。"""
    if not hex_blob:
        return None
    try:
        blob = bytes.fromhex(hex_blob)
    except ValueError:
        return None
    # 主路径：marker
    marker = bytes([0x12, 0x22, 0x0A, 0x20])
    pos = blob.find(marker)
    if pos >= 0:
        start = pos + 4
        if start + 32 <= len(blob):
            s = blob[start:start+32].decode('ascii', errors='ignore')
            if all(c in '0123456789abcdefABCDEF' for c in s):
                return s.lower()
    # fallback：连续 32 字节 hex
    for start in range(max(0, len(blob) - 32 + 1)):
        chunk = blob[start:start+32]
        try:
            s = chunk.decode('ascii')
            if all(c in '0123456789abcdefABCDEF' for c in s):
                return s.lower()
        except (UnicodeDecodeError, ValueError):
            continue
    return None


def get_resource_md5(chat_wxid, local_id, create_time, msg_type_lo32):
    """查 message_resource.db 拿附件文件 md5（图片/视频/文件/语音通用）"""
    if not RESOURCE_DB_KEY or not chat_wxid:
        return None
    # ChatName2Id: user_name -> rowid (chat_id)
    rows = sqlcipher_query(DB_PATHS["message_resource"], RESOURCE_DB_KEY,
        f"SELECT rowid FROM ChatName2Id WHERE user_name = '{chat_wxid}';")
    if not rows:
        return None
    chat_id = rows[0].strip()
    # 精确匹配 create_time，fallback 同 local_id 最新
    query = f"""SELECT hex(packed_info) FROM MessageResourceInfo
        WHERE chat_id = {chat_id} AND message_local_id = {local_id}
          AND (message_local_type = {msg_type_lo32} OR message_local_type % 4294967296 = {msg_type_lo32})
          AND message_create_time = {create_time}
        ORDER BY rowid DESC LIMIT 1;"""
    rows = sqlcipher_query(DB_PATHS["message_resource"], RESOURCE_DB_KEY, query)
    if not rows:
        query2 = f"""SELECT hex(packed_info) FROM MessageResourceInfo
            WHERE chat_id = {chat_id} AND message_local_id = {local_id}
              AND (message_local_type = {msg_type_lo32} OR message_local_type % 4294967296 = {msg_type_lo32})
            ORDER BY message_create_time DESC LIMIT 1;"""
        rows = sqlcipher_query(DB_PATHS["message_resource"], RESOURCE_DB_KEY, query2)
    if rows:
        return extract_md5_from_packed_hex(rows[0].strip())
    return None


def _make_attachment_id(chat_wxid, local_id, create_time, kind):
    """构造 wx-cli attachment_id（base64url(json)），kind: image/video/file/voice"""
    import base64 as b64
    payload = json.dumps({
        "v": 1, "chat": chat_wxid, "local_id": local_id,
        "create_time": create_time, "kind": kind
    }, separators=(',', ':'))
    return b64.urlsafe_b64encode(payload.encode()).decode().rstrip('=')


def _month_candidates(create_time):
    """生成消息时间所在月及前后各一月的 YYYY-MM 列表"""
    dt = datetime.fromtimestamp(create_time)
    result = []
    for delta in [-1, 0, 1]:
        m = dt.month + delta; y = dt.year
        while m > 12: m -= 12; y += 1
        while m < 1: m += 12; y -= 1
        result.append(f"{y:04d}-{m:02d}")
    return result


def _ensure_subdir(output_dir, subdir):
    """确保输出子目录存在，返回路径"""
    p = Path(output_dir) / subdir
    p.mkdir(parents=True, exist_ok=True)
    return p


def export_image(msg, output_dir):
    """图片导出：构造 attachment_id → 调 wx extract 解密（自动选高清/标准/缩略图）"""
    chat_wxid = get_chat_wxid_from_table(msg['table_name'])
    if not chat_wxid:
        return {'status': 'skip', 'reason': '无法反查 chat wxid'}
    att_id = _make_attachment_id(chat_wxid, msg['local_id'], msg['create_time'], 'image')
    out_dir = _ensure_subdir(output_dir, 'img')
    out_path = out_dir / f"img_{msg['local_id']}.jpg"
    try:
        r = subprocess.run(['wx', 'extract', att_id, '--output', str(out_path), '--overwrite'],
            capture_output=True, timeout=30)
        if r.returncode == 0 and out_path.exists():
            size = out_path.stat().st_size
            note = '高清原图' if size > 100000 else ('标准' if size > 10000 else '缩略图')
            return {'status': 'ok', 'path': str(out_path), 'size': size, 'note': note}
        err = r.stderr.decode(errors='replace').strip()[:120]
        return {'status': 'fail', 'reason': err or 'wx extract 返回非零'}
    except Exception as e:
        return {'status': 'fail', 'reason': str(e)[:120]}


def ocr_image(image_path):
    """用 macOS Vision 框架做 OCR（复用技能内 ocr_wechat_screenshot.sh）。
    非 macOS 系统直接返回空串（OCR 为 macOS 专属，Windows/Linux 上走音轨转写兜底）。
    返回识别文字，无文字返回空串。
    """
    if platform.system() != "Darwin":
        return ""
    ocr_script = SKILL_DIR / "scripts" / "wechat-ui" / "ocr_wechat_screenshot.sh"
    if not ocr_script.exists():
        return ""
    try:
        r = subprocess.run(['bash', str(ocr_script), str(image_path)],
            capture_output=True, text=True, timeout=15)
        return r.stdout.strip()
    except Exception:
        return ""


def extract_video_audio(video_path, wav_path):
    """从视频提取音轨为 24kHz mono WAV。成功返回 True。"""
    try:
        r = subprocess.run(
            ['ffmpeg', '-y', '-i', str(video_path), '-vn',
             '-acodec', 'pcm_s16le', '-ar', '24000', '-ac', '1', str(wav_path)],
            capture_output=True, timeout=30)
        return Path(wav_path).exists() and Path(wav_path).stat().st_size > 1000
    except Exception:
        return False


def transcribe_wav_file(wav_path):
    """用 FunASR 转写 WAV 文件（复用 voice-transcribe.py 的常驻 worker）。"""
    try:
        import importlib.util as _ilu
        vt_path = SKILL_DIR / "scripts" / "voice-transcribe.py"
        spec = _ilu.spec_from_file_location("voice_transcribe", str(vt_path))
        vt = _ilu.module_from_spec(spec)
        spec.loader.exec_module(vt)
        return vt.transcribe_wav(str(wav_path))
    except Exception:
        return ""


def extract_best_video_frame(video_path, output_dir, local_id):
    """从视频抽 5 帧，OCR 选文字最多的一帧作为代表帧。
    返回 (frame_path, ocr_text)；所有帧都无文字时返回 (第一帧路径, "")。"""
    info = get_video_info(video_path)
    duration = info.get('duration_sec', 0)
    if duration <= 0:
        return None, ""

    out_dir = Path(output_dir)
    candidates = []
    # 均匀抽 5 帧：10%, 30%, 50%, 70%, 90%
    for i, pct in enumerate([0.1, 0.3, 0.5, 0.7, 0.9]):
        ss = max(0.5, min(duration * pct, max(0, duration - 0.5)))
        frame_path = out_dir / f"video_{local_id}_candidate_{i}.jpg"
        if extract_video_frame(video_path, frame_path):
            text = ocr_image(frame_path)
            candidates.append((frame_path, len(text), text))

    if not candidates:
        return None, ""

    # 选 OCR 文字最多的一帧
    candidates.sort(key=lambda x: -x[1])
    best_path, best_len, best_text = candidates[0]

    # 重命名为最终帧，删除其他候选帧
    final_path = out_dir / f"video_{local_id}_frame.jpg"
    best_path.rename(final_path)
    for p, _, _ in candidates[1:]:
        p.unlink(missing_ok=True)
    # 如果第一名就是 best_path（已重命名），跳过
    for p, _, _ in candidates:
        if p.exists() and p != final_path:
            p.unlink(missing_ok=True)

    return final_path, best_text


def get_video_info(video_path):
    """用 ffprobe 获取视频元信息（时长/分辨率/编码），失败返回空 dict"""
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries',
             'format=duration,size:stream=width,height,codec_name',
             '-of', 'json', str(video_path)],
            capture_output=True, text=True, timeout=10)
        info = json.loads(r.stdout)
        fmt = info.get('format', {})
        # 视频流有 width/height，音频流没有；直接找有 width 的流
        streams = [s for s in info.get('streams', []) if s.get('width')]
        v = streams[0] if streams else {}
        return {
            'duration_sec': float(fmt.get('duration', 0)),
            'size_bytes': int(fmt.get('size', 0)),
            'width': v.get('width'),
            'height': v.get('height'),
            'codec': v.get('codec_name'),
        }
    except Exception:
        return {}


def extract_video_frame(video_path, output_path):
    """从视频抽 1 帧代表性图片（取 10% 处，跳过开头黑屏）。成功返回 True。"""
    try:
        info = get_video_info(video_path)
        duration = info.get('duration_sec', 0)
        # 取 10% 处，最短 0.5 秒，最长不超过时长-0.5 秒
        ss = max(0.5, min(duration * 0.1, max(0, duration - 0.5))) if duration > 0 else 1
        r = subprocess.run(
            ['ffmpeg', '-y', '-ss', f'{ss:.2f}', '-i', str(video_path),
             '-vframes', '1', '-q:v', '2', str(output_path)],
            capture_output=True, timeout=15)
        return Path(output_path).exists()
    except Exception:
        return False


def export_video(msg, output_dir):
    """视频导出：查 resource md5 → 复制 .mp4；自动抽关键帧 + 生成文字描述"""
    chat_wxid = get_chat_wxid_from_table(msg['table_name'])
    file_md5 = get_resource_md5(chat_wxid, msg['local_id'], msg['create_time'], 43)
    if not file_md5:
        return {'status': 'skip', 'reason': 'message_resource.db 无此视频记录'}
    if not WXCHAT_BASE:
        return {'status': 'fail', 'reason': 'WXCHAT_BASE 未设置'}
    video_dir = WXCHAT_BASE / 'msg' / 'video'
    out_dir = _ensure_subdir(output_dir, 'video')
    # 1. 优先找完整视频（原画 > 压缩版）
    for ym in _month_candidates(msg['create_time']):
        for suffix in ['_raw.mp4', '.mp4']:
            src = video_dir / ym / f"{file_md5}{suffix}"
            if src.exists():
                out_path = out_dir / f"video_{msg['local_id']}{suffix}"
                shutil.copy2(src, out_path)
                # 智能选帧：抽5帧 → OCR 选文字最多的一帧
                frame_path, ocr_text = extract_best_video_frame(out_path, out_dir, msg['local_id'])
                info = get_video_info(out_path)
                dur = info.get('duration_sec', 0)
                dur_str = f"{int(dur//60)}分{int(dur%60)}秒" if dur > 60 else f"{dur:.1f}秒"
                res = f"{info.get('width','?')}x{info.get('height','?')}"
                size_mb = out_path.stat().st_size / 1024 / 1024
                time_str = datetime.fromtimestamp(msg['create_time']).strftime('%Y-%m-%d %H:%M:%S')

                # 文字描述：优先 OCR，无文字则音轨转写兜底
                content_desc = ""
                desc_source = ""
                if ocr_text:
                    content_desc = ocr_text[:500]
                    desc_source = "OCR 帧上文字"
                else:
                    # 提取音轨 → FunASR 转写
                    wav_path = out_dir / f"video_{msg['local_id']}_audio.wav"
                    if extract_video_audio(out_path, wav_path):
                        transcript = transcribe_wav_file(wav_path)
                        wav_path.unlink(missing_ok=True)
                        if transcript and not transcript.startswith("[识别失败"):
                            content_desc = transcript[:500]
                            desc_source = "音轨语音转写"
                        else:
                            desc_source = "无文字且音轨转写失败"
                    else:
                        desc_source = "无文字且无音轨"

                # 生成文字描述文件
                info_path = out_dir / f"video_{msg['local_id']}_info.txt"
                desc = f"""视频内容理解
━━━━━━━━━━━━━━━━
文件: {out_path.name}
时长: {dur_str}
分辨率: {res}
大小: {size_mb:.1f}MB
编码: {info.get('codec','?')}
时间: {time_str}
关键帧: {frame_path.name if frame_path else '抽取失败'}
内容来源: {desc_source}
━━━━━━━━━━━━━━━━
{content_desc if content_desc else '(视频无文字内容且无语音，关键帧图片可直接查看画面)'}"""
                info_path.write_text(desc, encoding='utf-8')

                note = '原画' if suffix == '_raw.mp4' else '压缩版'
                if frame_path:
                    note += f' + 关键帧({res})'
                if content_desc:
                    note += f' + {desc_source}'
                return {'status': 'ok', 'path': str(out_path), 'size': out_path.stat().st_size,
                        'note': note, 'frame': str(frame_path) if frame_path else None,
                        'info': str(info_path), 'duration': dur, 'resolution': res,
                        'content': content_desc, 'content_source': desc_source}
    # 2. 兜底：导出缩略图
    for ym in _month_candidates(msg['create_time']):
        thumb = video_dir / ym / f"{file_md5}_thumb.jpg"
        if thumb.exists():
            out_path = out_dir / f"video_{msg['local_id']}_thumb.jpg"
            shutil.copy2(thumb, out_path)
            return {'status': 'ok', 'path': str(out_path), 'size': out_path.stat().st_size,
                    'note': '仅缩略图（视频未下载到本地，在微信中点击播放后可导出完整视频）'}
    return {'status': 'skip', 'reason': f'视频未下载且无缩略图（md5={file_md5[:16]}…）'}


def export_voice(msg, output_dir, transcode=False):
    """语音导出：从 media_0.db VoiceInfo 拿 SILK V3 BLOB → 写 .sil；可选转 WAV"""
    if not MEDIA_DB_KEY:
        return {'status': 'skip', 'reason': '无 media_0.db 密钥'}
    rows = sqlcipher_query(DB_PATHS["media"], MEDIA_DB_KEY,
        f"SELECT hex(voice_data), length(voice_data) FROM VoiceInfo WHERE local_id = {msg['local_id']};")
    if not rows:
        return {'status': 'skip', 'reason': 'VoiceInfo 无此语音'}
    parts = rows[0].split('|', 1)
    if len(parts) < 2 or not parts[0].strip():
        return {'status': 'fail', 'reason': 'voice_data 为空'}
    try:
        silk_bytes = bytes.fromhex(parts[0].strip())
        out_dir = _ensure_subdir(output_dir, 'voice')
        silk_path = out_dir / f"voice_{msg['local_id']}.sil"
        silk_path.write_bytes(silk_bytes)
        note = 'SILK V3 格式'
        out_path = silk_path

        if transcode:
            # 调 silk-v3-decoder 转 WAV（用法：converter.sh <input.sil> wav，输出 <input>.wav）
            converter = SKILL_DIR / 'tools' / 'silk-v3-decoder' / 'converter.sh'
            if converter.exists():
                r = subprocess.run(['bash', str(converter), str(silk_path), 'wav'],
                    capture_output=True, timeout=30)
                generated_wav = silk_path.with_suffix('.wav')
                if r.returncode == 0 and generated_wav.exists():
                    wav_path = out_dir / f"voice_{msg['local_id']}.wav"
                    generated_wav.rename(wav_path)
                    out_path = wav_path
                    note = 'WAV 格式（已从 SILK V3 转码，24kHz mono）'
                    silk_path.unlink(missing_ok=True)
                else:
                    note = 'SILK V3 格式（WAV 转码失败，保留原始格式）'
            else:
                note = 'SILK V3 格式（未找到 silk-v3-decoder，跳过转码）'

        return {'status': 'ok', 'path': str(out_path), 'size': out_path.stat().st_size, 'note': note}
    except Exception as e:
        return {'status': 'fail', 'reason': str(e)[:120]}


def export_file(msg, output_dir):
    """文件导出：从 message_content 解析文件名 → 从 msg/file/ 复制（明文）"""
    raw, _ = fetch_raw_content(msg)
    if not raw:
        return {'status': 'skip', 'reason': '无法获取消息内容'}
    filename = _xml_get(raw, 'title') or _xml_get(raw, 'filename') or _xml_get(raw, 'name')
    if not filename:
        return {'status': 'skip', 'reason': '无法解析文件名'}
    if not WXCHAT_BASE:
        return {'status': 'fail', 'reason': 'WXCHAT_BASE 未设置'}
    file_dir = WXCHAT_BASE / 'msg' / 'file'
    out_dir = _ensure_subdir(output_dir, 'file')
    for ym in _month_candidates(msg['create_time']):
        src = file_dir / ym / filename
        if src.exists():
            safe_name = f"file_{msg['local_id']}_{filename}"
            out_path = out_dir / safe_name
            shutil.copy2(src, out_path)
            return {'status': 'ok', 'path': str(out_path), 'size': out_path.stat().st_size}
    return {'status': 'skip', 'reason': f'文件未找到（{filename}），可能未下载或已清理'}


def export_merged_record(msg, output_dir, transcode=False):
    """合并聊天记录内媒体导出：解析子消息，尝试导出图片/视频/语音/文件。
    合并记录转发时媒体通常不缓存到本地，大部分会跳过；已缓存的视频/文件可直接复制。"""
    raw, _ = fetch_raw_content(msg)
    if not raw:
        return {'status': 'skip', 'reason': '无法获取合并记录内容'}
    if not WXCHAT_BASE:
        return {'status': 'fail', 'reason': 'WXCHAT_BASE 未设置'}

    results = []
    # 图片子消息（datatype=2）：fullmd5 标签
    for i, m in enumerate(re.finditer(r'<dataitem[^>]*datatype="2"[^>]*>(.*?)</dataitem>', raw, re.DOTALL)):
        fullmd5 = _xml_get(m.group(1), 'fullmd5')
        if fullmd5:
            # 全局搜索 .dat 文件
            found = None
            for p in (WXCHAT_BASE / 'msg' / 'attach').rglob(f"{fullmd5}*.dat"):
                found = p; break
            if found:
                results.append({'type': '图片', 'status': 'skip',
                    'reason': f'已缓存但解密需原始会话信息（{found.name}），暂不支持自动解密'})
            else:
                results.append({'type': '图片', 'status': 'skip',
                    'reason': '未缓存到本地（需在微信中点击查看原图后才能导出）'})
    # 视频子消息（datatype=4）：videomd5 或 md5
    for m in re.finditer(r'<dataitem[^>]*datatype="4"[^>]*>(.*?)</dataitem>', raw, re.DOTALL):
        vmd5 = _xml_get(m.group(1), 'videomd5') or _xml_get(m.group(1), 'md5')
        if vmd5:
            video_dir = WXCHAT_BASE / 'msg' / 'video'
            found = None
            for p in video_dir.rglob(f"{vmd5}*.mp4"):
                found = p; break
            if found:
                out_dir = _ensure_subdir(output_dir, 'video')
                out_path = out_dir / f"merged_video_{msg['local_id']}_{found.name}"
                shutil.copy2(found, out_path)
                results.append({'type': '视频', 'status': 'ok', 'path': str(out_path),
                    'size': out_path.stat().st_size, 'note': '合并记录内视频'})
            else:
                results.append({'type': '视频', 'status': 'skip', 'reason': '未下载到本地'})
    # 文件子消息（datatype=6）：filename
    for m in re.finditer(r'<dataitem[^>]*datatype="6"[^>]*>(.*?)</dataitem>', raw, re.DOTALL):
        fname = _xml_get(m.group(1), 'title') or _xml_get(m.group(1), 'filename')
        if fname:
            file_dir = WXCHAT_BASE / 'msg' / 'file'
            found = None
            for p in file_dir.rglob(fname):
                found = p; break
            if found:
                out_dir = _ensure_subdir(output_dir, 'file')
                out_path = out_dir / f"merged_file_{msg['local_id']}_{fname}"
                shutil.copy2(found, out_path)
                results.append({'type': '文件', 'status': 'ok', 'path': str(out_path),
                    'size': out_path.stat().st_size, 'note': '合并记录内文件'})
            else:
                results.append({'type': '文件', 'status': 'skip', 'reason': f'未找到（{fname}）'})

    if not results:
        return {'status': 'skip', 'reason': '合并记录内无可导出媒体（全是文本/链接/表情等）'}
    ok = sum(1 for r in results if r['status'] == 'ok')
    skip = sum(1 for r in results if r['status'] == 'skip')
    return {'status': 'ok' if ok > 0 else 'skip',
            'note': f'合并记录内媒体：成功 {ok} / 跳过 {skip}',
            'details': results}


def export_message(msg, output_dir, transcode=False):
    """统一导出分发：按消息类型选择导出方式"""
    base = get_base_type(msg['local_type'])
    if base == 3:
        return export_image(msg, output_dir)
    elif base == 43:
        return export_video(msg, output_dir)
    elif base == 34:
        return export_voice(msg, output_dir, transcode=transcode)
    elif base == 49:
        appmsg_type = msg['local_type'] >> 32
        if appmsg_type in (6, 2000):
            return export_file(msg, output_dir)
        elif appmsg_type == 19:
            return export_merged_record(msg, output_dir, transcode=transcode)
        return {'status': 'skip', 'reason': f'appmsg 类型 {appmsg_type} 不支持导出'}
    else:
        return {'status': 'skip', 'reason': f'类型 {msg.get("type_name", base)} 无需导出'}


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

def transcribe_voice(local_id):
    """语音转文字：复用 voice-transcribe.py 的 SILK→WAV→FunASR 全链路。
    首次调用需加载 FunASR 模型（10-30秒），后续复用常驻 worker。"""
    try:
        import importlib.util as _ilu
        import tempfile as _tf
        vt_path = Path(__file__).parent / "voice-transcribe.py"
        spec = _ilu.spec_from_file_location("voice_transcribe", str(vt_path))
        vt = _ilu.module_from_spec(spec)
        spec.loader.exec_module(vt)
        vt.load_keys()

        with _tf.TemporaryDirectory() as tmpdir:
            silk_path = Path(tmpdir) / f"voice_{local_id}.silk"
            wav_path = Path(tmpdir) / f"voice_{local_id}.wav"
            # 提取 SILK BLOB
            if not vt.extract_voice_blob(vt.get_media_db_path(), local_id, silk_path):
                return "[转写失败: 无法提取语音数据]"
            # SILK → WAV
            if not vt.silk_to_wav(silk_path, wav_path):
                return "[转写失败: SILK→WAV 转码失败]"
            # FunASR 转文字
            text = vt.transcribe_wav(str(wav_path))
            return text if text else "[转写失败: 无识别结果]"
    except Exception as e:
        return f"[转写失败: {str(e)[:80]}]"


def cmd_parse(since=None, limit=20, msg_type=None, output_json=False, transcribe=False):
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

        # 语音消息转文字（--transcribe）
        if transcribe and get_base_type(msg['local_type']) == 34:
            print(f"  ⏳ 正在转写语音 (id={parsed['local_id']})...")
            text = transcribe_voice(parsed['local_id'])
            print(f"  🎙️  转写: {text}")

        p = parsed['parsed']
        if p:
            for k, v in p.items():
                # content 已在上方展示，不重复显示 text 键
                if k == 'text' and parsed['content']:
                    continue
                if k == 'sub_messages':
                    print(f"  子消息 ({len(v)} 条):")
                    for j, sm in enumerate(v[:8], 1):
                        sm_type = sm.get('type', '?')
                        sender = sm.get('sender', '?')
                        sm_time = sm.get('time', '')
                        # 文本显示内容，其他显示 desc
                        if sm_type == '文本':
                            detail = (sm.get('text') or sm.get('desc') or '')[:60]
                        elif sm_type == '合并聊天记录':
                            detail = f"[嵌套合并记录 {sm.get('sub_message_count', '?')} 条]"
                        else:
                            detail = (sm.get('desc') or '')[:60]
                        print(f"    {j}. [{sm_type}] {sender} {sm_time}: {detail}")
                    if len(v) > 8:
                        print(f"    ... 还有 {len(v)-8} 条")
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


def cmd_export(output_dir="./wechat_exports", since=None, limit=20, msg_type=None, transcode=False):
    """统一导出：收集最近消息，对可导出类型执行导出，输出清单"""
    load_keys()

    since_time = 0
    if since:
        try:
            since_time = int(datetime.strptime(since, "%Y-%m-%d").timestamp())
        except ValueError:
            print(f"❌ 日期格式错误: {since}，应为 YYYY-MM-DD", file=sys.stderr)
            return

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    messages = collect_all_messages(since_time=since_time, type_filter=msg_type, limit=limit)

    print("=" * 70)
    print(f"媒体导出（共扫描 {len(messages)} 条消息）")
    print(f"输出目录: {out_path.resolve()}")
    if transcode:
        print("语音转 WAV: 已开启")
    print("=" * 70)

    stats = {'ok': 0, 'skip': 0, 'fail': 0}
    type_stats = {}

    for i, msg in enumerate(messages, 1):
        type_name = msg.get('type_name', '?')
        result = export_message(msg, str(out_path), transcode=transcode)
        status = result.get('status', 'skip')
        stats[status] = stats.get(status, 0) + 1
        type_stats[type_name] = type_stats.get(type_name, 0) + 1

        if status == 'ok':
            # 合并记录可能有多个子结果
            if 'details' in result:
                note = result.get('note', '')
                print(f"  [{i:3d}] ✅ {type_name:<8} {note}")
                for d in result['details']:
                    if d['status'] == 'ok':
                        size_kb = d.get('size', 0) / 1024
                        dnote = f" ({d.get('note', '')})" if d.get('note') else ""
                        print(f"         └─ {d['type']}: {Path(d['path']).name} ({size_kb:.1f}KB){dnote}")
            else:
                size_kb = result.get('size', 0) / 1024
                note = f" ({result['note']})" if 'note' in result else ""
                print(f"  [{i:3d}] ✅ {type_name:<8} {Path(result['path']).name} ({size_kb:.1f}KB){note}")
        elif status == 'fail':
            print(f"  [{i:3d}] ❌ {type_name:<8} id={msg['local_id']}: {result.get('reason', '?')}")
        # skip 不打印，太吵

    print("-" * 70)
    print(f"统计: 成功 {stats['ok']} / 跳过 {stats['skip']} / 失败 {stats['fail']}")
    print(f"扫描类型分布: {type_stats}")
    print(f"输出目录: {out_path.resolve()}")
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
    parse_parser.add_argument("--transcribe", action="store_true", help="语音消息自动转文字（首次需加载 FunASR 模型，较慢）")

    # export
    exp_parser = subparsers.add_parser("export", help="导出媒体文件（图片解密/视频/语音/文件）")
    exp_parser.add_argument("--output", type=str, default="./wechat_exports", help="输出目录")
    exp_parser.add_argument("--limit", type=int, default=20, help="处理最近 N 条消息")
    exp_parser.add_argument("--since", type=str, help="起始日期（YYYY-MM-DD）")
    exp_parser.add_argument("--type", type=int, help="只导出指定类型（local_type，如 3=图片 43=视频 34=语音）")
    exp_parser.add_argument("--transcode", action="store_true", help="语音导出时自动转 WAV（SILK V3 → WAV）")

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
            transcribe=args.transcribe,
        )
    elif args.command == "export":
        cmd_export(
            output_dir=args.output,
            since=args.since,
            limit=args.limit,
            msg_type=args.type,
            transcode=args.transcode,
        )


if __name__ == "__main__":
    main()
