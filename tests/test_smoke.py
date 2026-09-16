#!/usr/bin/env python3
"""
wechat-control 冒烟测试
覆盖：工具函数、消息解析、媒体导出、合并记录递归解析
运行：python3 tests/test_smoke.py
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

# 加载 message-collector（文件名含连字符，用 importlib）
SKILL_DIR = Path(__file__).parent.parent
MC_PATH = SKILL_DIR / "scripts" / "message-collector.py"
spec = importlib.util.spec_from_file_location("message_collector", str(MC_PATH))
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)

PASS = 0
FAIL = 0
ERRORS = []


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        ERRORS.append(name)
        print(f"  ❌ {name} {detail}")


# ============================================================
# 1. 工具函数测试（无需数据库）
# ============================================================
print("\n=== 1. 工具函数 ===")

check("get_base_type 提取低32位", mc.get_base_type(81604378673) == 49)
check("get_base_type 普通类型", mc.get_base_type(3) == 3)
check("get_base_type 大类型", mc.get_base_type(244813135921) == 49)

check("get_type_name 文本", mc.get_type_name(1) == "文本")
check("get_type_name 图片", mc.get_type_name(3) == "图片")
check("get_type_name 语音", mc.get_type_name(34) == "语音")
check("get_type_name 视频", mc.get_type_name(43) == "视频")
check("get_type_name 表情", mc.get_type_name(47) == "表情")
check("get_type_name 合并记录", "合并" in mc.get_type_name(81604378673))
check("get_type_name 引用消息", "引用" in mc.get_type_name(244813135921))

check("_xml_get 提取标签", mc._xml_get("<msg><title>hello</title></msg>", "title") == "hello")
check("_xml_get 不存在标签", mc._xml_get("<msg>text</msg>", "nonexistent") is None)
check("_xml_get 空输入", mc._xml_get("", "title") is None)
check("_xml_get None输入", mc._xml_get(None, "title") is None)

check("_xml_get_all 多标签", len(mc._xml_get_all("<a><b>1</b><b>2</b></a>", "b")) == 2)

check("_month_candidates 3个月", len(mc._month_candidates(1789148772)) == 3)
check("_month_candidates 格式", all(len(m) == 7 and m[4] == '-' for m in mc._month_candidates(1789148772)))

check("zstd_decompress_hex 空输入", mc.zstd_decompress_hex(None) is None)
check("zstd_decompress_hex 空字符串", mc.zstd_decompress_hex("") is None)

check("_make_attachment_id 非空", len(mc._make_attachment_id("test", 1, 100, "image")) > 0)
check("_make_attachment_id base64格式", all(c.isalnum() or c in '-_' for c in mc._make_attachment_id("test", 1, 100, "image")))

check("extract_md5_from_packed_hex 空输入", mc.extract_md5_from_packed_hex(None) is None)


# ============================================================
# 2. 数据库加载测试
# ============================================================
print("\n=== 2. 数据库加载 ===")
try:
    mc.load_keys()
    check("load_keys 不崩溃", True)
    check("DB_PATHS 非空", len(mc.DB_PATHS) > 0)
    check("message_0 路径存在", mc.DB_PATHS.get("message_0", Path("/nonexistent")).exists())
    check("MEDIA_DB_KEY 已加载", mc.MEDIA_DB_KEY is not None and len(mc.MEDIA_DB_KEY) > 0)
    check("RESOURCE_DB_KEY 已加载", mc.RESOURCE_DB_KEY is not None and len(mc.RESOURCE_DB_KEY) > 0)
    check("WXCHAT_BASE 已设置", mc.WXCHAT_BASE is not None and mc.WXCHAT_BASE.exists())
except Exception as e:
    check("load_keys 不崩溃", False, str(e))


# ============================================================
# 3. 消息采集测试
# ============================================================
print("\n=== 3. 消息采集 ===")
try:
    msgs = mc.collect_all_messages(limit=10)
    check("collect_all_messages 返回列表", isinstance(msgs, list))
    check("collect_all_messages 数量<=10", len(msgs) <= 10)
    if msgs:
        m = msgs[0]
        check("消息有 local_id", "local_id" in m)
        check("消息有 local_type", "local_type" in m)
        check("消息有 type_name", "type_name" in m)
        check("消息有 table_name", "table_name" in m)
        check("消息有 create_time", "create_time" in m)
except Exception as e:
    check("collect_all_messages 不崩溃", False, str(e))


# ============================================================
# 4. 消息解析测试（各类型）
# ============================================================
print("\n=== 4. 消息解析 ===")

def test_parse_type(base_type, name, limit=3):
    """测试指定类型的消息解析"""
    try:
        msgs = mc.collect_all_messages(type_filter=base_type, limit=limit)
        if not msgs:
            check(f"parse {name}: 无消息（跳过）", True)
            return
        for m in msgs[:2]:
            result = mc.parse_message(m)
            check(f"parse {name} id={m['local_id']}: 不崩溃", True)
            check(f"parse {name} id={m['local_id']}: 有 parsed 字段", "parsed" in result, str(result.keys()))
    except Exception as e:
        check(f"parse {name}: 不崩溃", False, str(e))


test_parse_type(1, "文本")
test_parse_type(3, "图片")
test_parse_type(34, "语音")
test_parse_type(43, "视频")
test_parse_type(47, "表情")
test_parse_type(49, "卡片类", limit=5)


# ============================================================
# 5. 合并记录递归解析测试
# ============================================================
print("\n=== 5. 合并记录递归解析 ===")
try:
    merged = mc.collect_all_messages(type_filter=81604378673, limit=3)
    if merged:
        for m in merged[:2]:
            result = mc.parse_message(m)
            p = result.get("parsed", {})
            sub = p.get("sub_messages", [])
            check(f"合并记录 id={m['local_id']}: 有子消息", len(sub) > 0, f"子消息数={len(sub)}")
            if sub:
                check(f"合并记录 id={m['local_id']}: 子消息有 type", "type" in sub[0])
                check(f"合并记录 id={m['local_id']}: 子消息有 sender", "sender" in sub[0])
    else:
        check("合并记录: 无消息（跳过）", True)
except Exception as e:
    check("合并记录解析: 不崩溃", False, str(e))


# ============================================================
# 6. 媒体导出测试（dry-run，不实际写文件）
# ============================================================
print("\n=== 6. 媒体导出（临时目录） ===")
with tempfile.TemporaryDirectory() as tmpdir:
    # 图片导出
    try:
        imgs = mc.collect_all_messages(type_filter=3, limit=1)
        if imgs:
            result = mc.export_image(imgs[0], tmpdir)
            check("export_image: 返回 status", "status" in result)
            check(f"export_image: status={result.get('status')}", result.get("status") in ("ok", "fail", "skip"))
    except Exception as e:
        check("export_image: 不崩溃", False, str(e))

    # 语音导出
    try:
        voices = mc.collect_all_messages(type_filter=34, limit=1)
        if voices:
            result = mc.export_voice(voices[0], tmpdir)
            check("export_voice: 返回 status", "status" in result)
            check(f"export_voice: status={result.get('status')}", result.get("status") in ("ok", "fail", "skip"))
    except Exception as e:
        check("export_voice: 不崩溃", False, str(e))

    # 视频导出
    try:
        videos = mc.collect_all_messages(type_filter=43, limit=1)
        if videos:
            result = mc.export_video(videos[0], tmpdir)
            check("export_video: 返回 status", "status" in result)
            check(f"export_video: status={result.get('status')}", result.get("status") in ("ok", "fail", "skip"))
    except Exception as e:
        check("export_video: 不崩溃", False, str(e))

    # export_message 分发
    try:
        msgs = mc.collect_all_messages(limit=5)
        for m in msgs:
            result = mc.export_message(m, tmpdir)
            check(f"export_message id={m['local_id']}: 有 status", "status" in result)
    except Exception as e:
        check("export_message: 不崩溃", False, str(e))


# ============================================================
# 7. get_chat_wxid_from_table 测试
# ============================================================
print("\n=== 7. chat wxid 反查 ===")
try:
    msgs = mc.collect_all_messages(limit=3)
    if msgs:
        wxid = mc.get_chat_wxid_from_table(msgs[0]["table_name"])
        check("get_chat_wxid_from_table: 返回非空", wxid is not None and len(wxid) > 0, f"wxid={wxid}")
except Exception as e:
    check("get_chat_wxid_from_table: 不崩溃", False, str(e))


# ============================================================
# 总结
# ============================================================
print("\n" + "=" * 60)
print(f"冒烟测试完成: ✅ {PASS} 通过 / ❌ {FAIL} 失败")
if ERRORS:
    print(f"失败项: {ERRORS}")
print("=" * 60)
sys.exit(1 if FAIL > 0 else 0)
