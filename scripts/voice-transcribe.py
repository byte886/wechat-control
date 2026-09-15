#!/usr/bin/env python3
"""
微信语音消息转文字工具

功能：
1. 从 media_0.db 读取语音 SILK V3 BLOB
2. 解码为 WAV（silk-v3-decoder）
3. FunASR SenseVoiceSmall 转文字
4. 支持按会话/时间范围/数量批量转文字
5. 输出 JSON 或文本格式

用法：
  python3 voice-transcribe.py list                          # 列出所有语音
  python3 voice-transcribe.py transcribe --limit 10         # 转最近10条语音
  python3 voice-transcribe.py transcribe --chat <chat_name> # 转指定会话的语音
  python3 voice-transcribe.py transcribe --since "2026-09-01"  # 转指定日期后的语音
  python3 voice-transcribe.py transcribe --output json      # 输出JSON格式

依赖：
- sqlcipher（解密 media_0.db）
- silk-v3-decoder（SILK → WAV）
- FunASR + SenseVoiceSmall（语音识别）
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# ============ 配置 ============
SKILL_DIR = Path(__file__).parent.parent
WECHAT_FILES = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
WX_CLI_CONFIG = Path.home() / ".wx-cli/config.json"
ALL_KEYS_PATH = Path.home() / ".wx-cli/all_keys.json"
SILK_DECODER = SKILL_DIR / "tools/silk-v3-decoder/converter.sh"
FUNASR_VENV = Path.home() / "Doubao/chats/2026-08-26/new-chat/gaodun-course-knowledge-base/transcription/venv"
FUNASR_PYTHON = FUNASR_VENV / "bin/python"

# media_0.db 密钥（从 all_keys.json 读取）
MEDIA_DB_KEY = None


def load_keys():
    """从 all_keys.json 加载数据库密钥"""
    global MEDIA_DB_KEY
    if ALL_KEYS_PATH.exists():
        keys = json.loads(ALL_KEYS_PATH.read_text())
        for db_path, key_info in keys.items():
            if "media_0.db" in db_path:
                if isinstance(key_info, str):
                    MEDIA_DB_KEY = key_info
                elif isinstance(key_info, dict):
                    MEDIA_DB_KEY = key_info.get("enc_key", "") or key_info.get("key", "")
                break
    if not MEDIA_DB_KEY:
        print("⚠️  未找到 media_0.db 密钥，请先播放一条语音触发密钥提取，然后运行 wx init --force", file=sys.stderr)
        sys.exit(1)


def get_media_db_path():
    """获取当前账号的 media_0.db 路径（优先从 wx-cli config.json 读取 db_dir）"""
    # 优先从配置文件读取 db_dir（支持多账号切换）
    if WX_CLI_CONFIG.exists():
        try:
            cfg = json.loads(WX_CLI_CONFIG.read_text())
            db_dir = cfg.get("db_dir", "")
            if db_dir:
                db = Path(db_dir) / "message/media_0.db"
                if db.exists():
                    return db
                else:
                    print(f"⚠️  配置的 db_dir 下无 media_0.db: {db}", file=sys.stderr)
        except Exception as e:
            print(f"⚠️  读取配置文件失败: {e}", file=sys.stderr)

    # 回退：遍历所有账号目录，找最新修改的 media_0.db
    accounts = []
    if WECHAT_FILES.exists():
        for d in WECHAT_FILES.iterdir():
            if d.is_dir() and d.name.startswith("wxid_"):
                db = d / "db_storage/message/media_0.db"
                if db.exists():
                    mtime = db.stat().st_mtime
                    accounts.append((mtime, db))
    if not accounts:
        print("❌ 未找到 media_0.db", file=sys.stderr)
        sys.exit(1)
    accounts.sort(reverse=True)
    return accounts[0][1]


def sqlcipher_query(db_path, query, params=None):
    """执行 SQLCipher 查询（通过 stdin 传递 SQL，避免引号转义问题）"""
    key_pragma = f"PRAGMA key = \"x'{MEDIA_DB_KEY}'\";"
    full_sql = f"{key_pragma}\n{query}"
    result = subprocess.run(
        ["sqlcipher", str(db_path)],
        input=full_sql,
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print(f"❌ SQLCipher 错误: {result.stderr}", file=sys.stderr)
        return []
    lines = result.stdout.strip().split("\n")
    # 过滤掉空行和 "ok"（PRAGMA key 的输出）
    return [l for l in lines if l.strip() and l.strip() != "ok"]


def list_voices(limit=20):
    """列出语音消息"""
    db_path = get_media_db_path()
    print(f"📁 数据库: {db_path}")
    print(f"🔑 密钥: {MEDIA_DB_KEY[:16]}...")
    print()

    query = f"SELECT chat_name_id || '|' || create_time || '|' || local_id || '|' || svr_id || '|' || length(voice_data) || '|' || data_index FROM VoiceInfo ORDER BY create_time DESC LIMIT {limit};"
    rows = sqlcipher_query(db_path, query)

    print(f"{'序号':<4} {'会话ID':<10} {'时间':<20} {'local_id':<10} {'大小(B)':<10}")
    print("-" * 70)
    for i, row in enumerate(rows):
        parts = row.split("|")
        if len(parts) >= 5:
            chat_id = parts[0]
            try:
                ts = int(parts[1])
                time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            except:
                time_str = parts[1]
            local_id = parts[2]
            size = parts[4]
            print(f"{i+1:<4} {chat_id:<10} {time_str:<20} {local_id:<10} {size:<10}")

    print(f"\n共 {len(rows)} 条语音（显示最近 {limit} 条）")


def extract_voice_blob(db_path, local_id, output_path, chat_name_id=None):
    """从数据库提取语音 SILK BLOB 到文件（去掉前面的头部字节）
    注意：local_id 在每个会话内自增，不是全局唯一，必须同时用 chat_name_id 定位"""
    if chat_name_id is not None:
        query = f"SELECT hex(voice_data) FROM VoiceInfo WHERE local_id = {local_id} AND chat_name_id = {chat_name_id};"
    else:
        query = f"SELECT hex(voice_data) FROM VoiceInfo WHERE local_id = {local_id} ORDER BY create_time DESC LIMIT 1;"
    rows = sqlcipher_query(db_path, query)
    if not rows:
        return False

    hex_data = None
    for row in rows:
        row = row.strip()
        if row and len(row) > 10:
            hex_data = row
            break

    if not hex_data:
        print(f"❌ 未找到 BLOB 数据", file=sys.stderr)
        return False

    try:
        blob = bytes.fromhex(hex_data)
        # 微信 BLOB 前面有头部字节（如 0x02），找到 #!SILK_V3 开始的位置
        silk_header = b"#!SILK_V3"
        idx = blob.find(silk_header)
        if idx > 0:
            blob = blob[idx:]  # 去掉头部字节
        output_path.write_bytes(blob)
        return True
    except Exception as e:
        print(f"❌ 提取 BLOB 失败: {e}", file=sys.stderr)
        return False


def silk_to_wav(silk_path, wav_path):
    """SILK V3 → WAV（converter.sh 用法：sh converter.sh <input.silk> wav）"""
    cmd = ["sh", str(SILK_DECODER), str(silk_path), "wav"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    # converter.sh 输出的文件名是 <input>.wav，在输入文件同一目录
    generated = silk_path.with_suffix(".wav")
    if generated.exists():
        if generated != wav_path:
            generated.rename(wav_path)
        return True
    return False


def transcribe_wav(wav_path):
    """用 FunASR 转文字（SenseVoiceSmall）"""
    script = f'''
import warnings
warnings.filterwarnings("ignore")
from funasr import AutoModel

model = AutoModel(model="iic/SenseVoiceSmall", device="cpu", disable_update=True)
res = model.generate(input="{wav_path}", cache={{}}, language="auto", use_itn=True)
text = res[0].get("text", "")
# 清理 SenseVoice 标记和噪声
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
        return f"[识别失败: {result.stderr[:100]}]"
    # 只取最后一行（过滤掉版本检查等输出）
    lines = result.stdout.strip().split("\n")
    return lines[-1].strip() if lines else ""


def transcribe_voices(limit=10, chat_name=None, since=None, output_format="text"):
    """批量转文字"""
    db_path = get_media_db_path()
    print(f"📁 数据库: {db_path}")
    print()

    # 构建查询
    where = []
    if chat_name:
        where.append(f"chat_name_id = '{chat_name}'")
    if since:
        try:
            since_ts = int(datetime.strptime(since, "%Y-%m-%d").timestamp())
            where.append(f"create_time >= {since_ts}")
        except:
            pass

    where_clause = " AND ".join(where) if where else "1=1"
    query = f"SELECT chat_name_id || '|' || create_time || '|' || local_id || '|' || svr_id || '|' || length(voice_data) FROM VoiceInfo WHERE {where_clause} ORDER BY create_time DESC LIMIT {limit};"
    rows = sqlcipher_query(db_path, query)

    if not rows:
        print("未找到语音消息")
        return

    print(f"找到 {len(rows)} 条语音，开始转文字...\n")

    results = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        for i, row in enumerate(rows):
            parts = row.split("|") if "|" in row else row.split()
            if len(parts) < 5:
                continue

            chat_id = parts[0]
            try:
                ts = int(parts[1])
                time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            except:
                time_str = parts[1]
            local_id = parts[2]
            size = parts[4]

            print(f"[{i+1}/{len(rows)}] {time_str} | 会话: {chat_id} | 大小: {size}B", end=" ... ")

            # 提取 SILK
            silk_path = tmpdir / f"voice_{local_id}.silk"
            wav_path = tmpdir / f"voice_{local_id}.wav"

            if not extract_voice_blob(db_path, local_id, silk_path, chat_name_id=chat_id):
                print("❌ 提取失败")
                continue

            # 解码 WAV
            if not silk_to_wav(silk_path, wav_path):
                print("❌ 解码失败")
                continue

            # 转文字
            text = transcribe_wav(wav_path)
            print(f"✓ {text}")

            results.append({
                "index": i + 1,
                "chat_name_id": chat_id,
                "create_time": time_str,
                "local_id": local_id,
                "size_bytes": int(size),
                "text": text
            })

    # 输出
    print("\n" + "=" * 60)
    print("转文字结果汇总")
    print("=" * 60)

    if output_format == "json":
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(f"\n[{r['create_time']}] {r['chat_name_id']}:")
            print(f"  {r['text']}")

    print(f"\n共转写 {len(results)} 条语音")


def main():
    parser = argparse.ArgumentParser(description="微信语音消息转文字工具")
    subparsers = parser.add_subparsers(dest="command", help="命令")

    # list 命令
    list_parser = subparsers.add_parser("list", help="列出语音消息")
    list_parser.add_argument("--limit", type=int, default=20, help="显示数量")

    # transcribe 命令
    trans_parser = subparsers.add_parser("transcribe", help="转文字")
    trans_parser.add_argument("--limit", type=int, default=10, help="转写数量")
    trans_parser.add_argument("--chat", type=str, help="指定会话ID")
    trans_parser.add_argument("--since", type=str, help="指定日期（YYYY-MM-DD）")
    trans_parser.add_argument("--output", choices=["text", "json"], default="text", help="输出格式")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    load_keys()

    if args.command == "list":
        list_voices(args.limit)
    elif args.command == "transcribe":
        transcribe_voices(args.limit, args.chat, args.since, args.output)


if __name__ == "__main__":
    main()
