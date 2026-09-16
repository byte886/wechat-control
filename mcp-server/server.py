#!/usr/bin/env python3
"""
微信消息监控 MCP Server

功能：
1. 暴露微信新消息资源（wechat://messages/new）
2. 提供获取消息、标记已读等工具
3. 后台轮询微信新消息，检测到新消息时主动推送资源更新通知给 MCP 客户端（豆包）
4. 支持重要消息判定（关键词、特定发送者、特定群）

传输方式：STDIO（本地通信，豆包直接启动进程）

用法：
  uv run python server.py          # 启动 MCP Server（STDIO 模式）
  uv run python server.py --sse    # 启动 MCP Server（SSE 模式，http://localhost:8765/sse）
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan
from fastmcp.server.dependencies import get_context

# ============ 配置 ============
def _resolve_wx_bin() -> str:
    """定位 wx 可执行文件。

    MCP server 常由 GUI 宿主（如豆包桌面端经 launchd）以 STDIO 拉起，其 PATH
    往往只有 /usr/bin:/bin:/usr/sbin:/sbin，不含 npm 全局链接所在的
    /usr/local/bin（Intel）或 /opt/homebrew/bin（Apple Silicon），此时
    shutil.which("wx") 会落空、回退成裸 "wx" 又会在子进程调用时
    FileNotFoundError，使全部 MCP 工具失效。故 which 失败时再回退探测常见绝对路径。
    """
    found = shutil.which("wx")
    if found:
        return found
    for candidate in ("/usr/local/bin/wx", "/opt/homebrew/bin/wx"):
        if os.path.exists(candidate):
            return candidate
    return "wx"


WX_CLI_BIN = _resolve_wx_bin()
POLL_INTERVAL = 5  # 轮询间隔（秒）
STATE_FILE = Path.home() / ".wx-cli/mcp_server_state.json"
CONFIG_FILE = Path.home() / ".wx-cli/mcp_server_config.json"

# 默认配置
DEFAULT_CONFIG = {
    "poll_interval_seconds": 5,
    "important_keywords": [],        # 重要关键词列表
    "important_senders": [],         # 重要发送者列表（昵称/备注名）
    "important_groups": [],          # 重要群列表
    "notify_all_messages": True,     # 是否所有新消息都通知（False 则只通知重要消息）
    "max_cached_messages": 100,      # 最大缓存消息数
}

# ============ 全局状态 ============
message_cache: list[dict[str, Any]] = []  # 新消息缓存
last_poll_time: float = 0
monitor_running: bool = False
monitor_started: bool = False  # 懒加载标记
monitor_task: Optional[asyncio.Task] = None
config: dict[str, Any] = {}
captured_ctx: Any = None  # 首次工具请求时捕获的 FastMCP Context，供后台 task 主动推送

# 单次 wx 子进程调用超时（秒）；单次主动推送超时（秒，防止过期 Context 的 await 永久挂死轮询）
WX_CALL_TIMEOUT = 20
PUSH_TIMEOUT = 3


async def get_bg_ctx():
    """后台轮询任务获取可用 Context：优先用首次请求捕获的，兜底用 contextvar。"""
    global captured_ctx
    if captured_ctx is not None:
        return captured_ctx
    try:
        captured_ctx = get_context()
        return captured_ctx
    except Exception:
        return None


# ============ 懒加载：确保后台监控已启动 ============
async def ensure_monitor_started():
    """确保后台消息监控已启动（懒加载，在第一个工具/资源调用时启动）"""
    global monitor_started, monitor_task, config, monitor_running, captured_ctx

    if monitor_started:
        # 自愈：如果后台轮询任务已经结束（异常退出/被取消），重新拉起，避免一次卡死永久停摆
        if monitor_task is not None and monitor_task.done():
            try:
                exc = monitor_task.exception()
            except Exception:
                exc = None
            print(f"[MCP Server] ⚠️ 检测到轮询任务已结束({exc!r})，重新拉起", file=sys.stderr, flush=True)
            monitor_running = True
            monitor_task = asyncio.create_task(poll_wechat_messages())
        return

    monitor_started = True
    config = load_config()
    monitor_running = True

    # 在请求协程内捕获 Context：后台 task 会复制 contextvar，但显式捕获最稳，
    # 供 poll_wechat_messages 主动推送（FastMCP 实例本身没有 .session 属性）
    try:
        captured_ctx = get_context()
    except Exception as e:
        print(f"[MCP Server] ⚠️ 捕获请求 Context 失败: {e!r}", file=sys.stderr, flush=True)
        captured_ctx = None

    print(f"[MCP Server] 微信消息监控已启动（懒加载）", file=sys.stderr, flush=True)
    print(f"[MCP Server] 轮询间隔: {config.get('poll_interval_seconds', POLL_INTERVAL)}秒", file=sys.stderr, flush=True)

    # 启动后台消息监控任务
    monitor_task = asyncio.create_task(poll_wechat_messages())


# ============ 创建 MCP Server ============
mcp = FastMCP(
    "wechat-monitor",
    # 启用资源订阅能力（实验性，用于 server-initiated notification）
    experimental_capabilities={"resources": {"subscribe": True}},
)


# ============ 配置管理 ============
def load_config() -> dict[str, Any]:
    """加载配置，不存在则创建默认配置"""
    if CONFIG_FILE.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text())}
        except Exception:
            pass
    # 保存默认配置
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2))
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict[str, Any]):
    """保存配置"""
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))


# ============ 状态管理 ============
def load_state() -> dict[str, Any]:
    """加载状态（已处理的消息 ID 等）"""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"processed_message_ids": [], "last_poll_time": 0}


def save_state(state: dict[str, Any]):
    """保存状态"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


# ============ 微信命令执行 ============
def run_wx_command(args: list[str], timeout: int = 15) -> dict[str, Any]:
    """执行 wx 命令并返回 JSON 结果"""
    try:
        result = subprocess.run(
            [WX_CLI_BIN] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return {"error": f"命令执行失败: {result.stderr.strip()}"}
        # 尝试解析 JSON
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"raw_output": result.stdout}
    except subprocess.TimeoutExpired:
        return {"error": "命令执行超时"}
    except FileNotFoundError:
        return {"error": f"未找到 wx 命令: {WX_CLI_BIN}"}
    except Exception as e:
        return {"error": f"执行异常: {str(e)}"}


async def run_wx_async(args: list[str], timeout: int = WX_CALL_TIMEOUT) -> dict[str, Any]:
    """异步执行 wx 命令：把阻塞的 subprocess.run 丢到线程，避免卡住事件循环；
    整体再用 wait_for 兜底，保证单次调用不会无限期挂起。"""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(run_wx_command, args, timeout),
            timeout=timeout + 5,
        )
    except asyncio.TimeoutError:
        return {"error": "wx 命令异步调用超时"}
    except Exception as e:
        return {"error": f"wx 异步调用异常: {e}"}


# ============ 重要消息判定 ============
def is_important_message(msg: dict[str, Any], cfg: dict[str, Any]) -> tuple[bool, str]:
    """判断消息是否重要，返回 (是否重要, 原因)"""
    content = str(msg.get("content", ""))
    sender = str(msg.get("sender", ""))
    chat_name = str(msg.get("chat_name", ""))
    is_group = msg.get("is_group", False)

    # 检查关键词
    for keyword in cfg.get("important_keywords", []):
        if keyword and keyword in content:
            return True, f"包含关键词「{keyword}」"

    # 检查特定发送者
    for sender_name in cfg.get("important_senders", []):
        if sender_name and (sender_name in sender or sender_name in chat_name):
            return True, f"来自重要联系人「{sender_name}」"

    # 检查特定群
    if is_group:
        for group_name in cfg.get("important_groups", []):
            if group_name and group_name in chat_name:
                return True, f"来自重要群「{group_name}」"

    return False, ""


# ============ 消息轮询 ============
async def safe_send_notifications(text_summary: str):
    """主动推送：绝不抛错、绝不永久挂起。
    历史教训：复用"已结束请求"的 Context 去 await send_* 会永久挂住整个轮询协程，
    因此每个发送都用 wait_for 限时，超时/异常只记录并放弃，绝不向上传播。"""
    ctx = await get_bg_ctx()
    if ctx is None:
        print("[MCP Server] ⚠️ 无可用 Context，仅缓存不推送", file=sys.stderr, flush=True)
        return

    # 方式1：资源更新通知（experimental resources.subscribe）
    try:
        await asyncio.wait_for(
            ctx.session.send_resource_updated("wechat://messages/new"),
            timeout=PUSH_TIMEOUT,
        )
        print("[MCP Server] ✅ 资源更新通知已发送", file=sys.stderr, flush=True)
    except asyncio.TimeoutError:
        print("[MCP Server] ⚠️ 资源更新通知超时，跳过（不影响轮询）", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[MCP Server] ⚠️ 资源更新通知失败: {e!r}", file=sys.stderr, flush=True)

    # 方式2：日志消息通知（不要求客户端先订阅，data 为可读文本）
    try:
        await asyncio.wait_for(
            ctx.session.send_log_message(
                level="info",
                data=text_summary,
                logger="wechat-monitor",
            ),
            timeout=PUSH_TIMEOUT,
        )
        print("[MCP Server] ✅ 日志消息通知已发送", file=sys.stderr, flush=True)
    except asyncio.TimeoutError:
        print("[MCP Server] ⚠️ 日志消息通知超时，跳过（不影响轮询）", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[MCP Server] ⚠️ 日志消息通知失败: {e!r}", file=sys.stderr, flush=True)


async def poll_wechat_messages():
    """后台轮询微信新消息，检测到新消息时主动推送资源更新通知。
    健壮性要求：单轮任何阻塞/异常都不得使循环永久停摆。"""
    global message_cache, last_poll_time, monitor_running

    state = load_state()
    processed_ids = set(state.get("processed_message_ids", []))

    while monitor_running:
        try:
            # 在线程里跑阻塞的 wx 子进程，并整体加超时，不卡住事件循环
            result = await run_wx_async(["new-messages", "--limit", "50", "--json"])

            if isinstance(result, dict) and "error" in result:
                print(f"[MCP Server] 轮询取数失败: {result['error']}", file=sys.stderr, flush=True)
            else:
                messages = result.get("messages", []) or []
                new_messages = []

                for msg in messages:
                    msg_id = f"{msg.get('local_id', '')}_{msg.get('timestamp', '')}_{msg.get('chat', '')}"
                    if msg_id in processed_ids:
                        continue
                    msg["_mcp_msg_id"] = msg_id
                    msg["_detected_at"] = datetime.now().isoformat()
                    important, reason = is_important_message(msg, config)
                    msg["_important"] = important
                    msg["_important_reason"] = reason
                    new_messages.append(msg)
                    processed_ids.add(msg_id)

                if new_messages:
                    message_cache = new_messages + message_cache
                    if len(message_cache) > config.get("max_cached_messages", 100):
                        message_cache = message_cache[:config.get("max_cached_messages", 100)]

                    should_notify = config.get("notify_all_messages", True) or any(
                        m["_important"] for m in new_messages
                    )
                    print(f"[MCP Server] 检测到 {len(new_messages)} 条新消息（notify={should_notify}）", file=sys.stderr, flush=True)
                    for msg in new_messages:
                        star = "⭐" if msg["_important"] else " "
                        print(f"  - {star}[{msg.get('sender', '未知')}] {str(msg.get('content', ''))[:50]}", file=sys.stderr, flush=True)

                    if should_notify:
                        lines = [f"【微信新消息 {len(new_messages)} 条】"]
                        for m in new_messages:
                            where = m.get("chat_name") or m.get("sender") or "未知会话"
                            tag = f"⭐{m.get('_important_reason','')}" if m.get("_important") else ""
                            lines.append(f"[{where}] {m.get('sender','')}: {str(m.get('content',''))[:80]} {tag}".rstrip())
                        # 推送单独保护：即便挂起/失败也不影响主轮询继续
                        await safe_send_notifications("\n".join(lines))

            # 心跳：无论有没有新消息，每轮都更新时间并落盘，外部据此判断轮询是否存活
            last_poll_time = time.time()
            state["processed_message_ids"] = list(processed_ids)[-500:]
            state["last_poll_time"] = last_poll_time
            save_state(state)

        except asyncio.CancelledError:
            # 任务被正常取消时向外抛，其余任何异常都不得杀死循环
            raise
        except BaseException as e:
            print(f"[MCP Server] 轮询循环异常（已忽略并继续）: {e!r}", file=sys.stderr, flush=True)

        try:
            await asyncio.sleep(config.get("poll_interval_seconds", POLL_INTERVAL))
        except asyncio.CancelledError:
            raise


# ============ MCP 资源定义 ============
@mcp.resource("wechat://messages/new")
def get_new_messages_resource() -> str:
    """
    微信新消息列表资源。

    当有新消息时，MCP Server 会主动推送此资源的更新通知（resources/updated）。
    客户端收到通知后，读取此资源即可获取最新的新消息列表。

    返回 JSON 格式的消息列表，每条消息包含：
    - local_id: 消息本地 ID
    - timestamp: 消息时间戳
    - time_str: 格式化时间
    - chat: 会话 ID
    - chat_name: 会话名称
    - sender: 发送者
    - content: 消息内容
    - type: 消息类型（text/voice/image/video/link/system等）
    - is_group: 是否群聊
    - _important: 是否重要消息
    - _important_reason: 重要原因
    - _detected_at: MCP Server 检测到的时间
    """
    return json.dumps({
        "messages": message_cache,
        "total": len(message_cache),
        "last_poll_time": datetime.fromtimestamp(last_poll_time).isoformat() if last_poll_time else None,
        "monitor_running": monitor_running,
    }, ensure_ascii=False, indent=2)


@mcp.resource("wechat://messages/unread")
def get_unread_messages_resource() -> str:
    """微信未读消息资源（从 wx unread 获取）"""
    result = run_wx_command(["unread", "--json"])
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.resource("wechat://sessions")
def get_sessions_resource() -> str:
    """微信会话列表资源"""
    result = run_wx_command(["sessions", "--json"])
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.resource("wechat://config")
def get_config_resource() -> str:
    """MCP Server 配置资源"""
    return json.dumps(config, ensure_ascii=False, indent=2)


# ============ MCP 工具定义 ============
@mcp.tool()
async def get_new_messages(limit: int = 10, important_only: bool = False) -> str:
    """
    获取微信新消息列表。

    Args:
        limit: 返回的最大消息数量，默认 10
        important_only: 是否只返回重要消息，默认 False

    Returns:
        JSON 格式的新消息列表
    """
    await ensure_monitor_started()
    msgs = message_cache
    if important_only:
        msgs = [m for m in msgs if m.get("_important", False)]

    return json.dumps({
        "messages": msgs[:limit],
        "total": len(msgs),
        "returned": min(limit, len(msgs)),
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_monitor_status() -> str:
    """
    获取微信消息监控状态。

    Returns:
        监控状态信息（是否运行、轮询间隔、缓存消息数、最后轮询时间等）
    """
    await ensure_monitor_started()
    return json.dumps({
        "monitor_running": monitor_running,
        "poll_interval_seconds": config.get("poll_interval_seconds", POLL_INTERVAL),
        "cached_messages": len(message_cache),
        "last_poll_time": datetime.fromtimestamp(last_poll_time).isoformat() if last_poll_time else None,
        "important_keywords": config.get("important_keywords", []),
        "important_senders": config.get("important_senders", []),
        "important_groups": config.get("important_groups", []),
        "notify_all_messages": config.get("notify_all_messages", True),
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def mark_messages_as_read() -> str:
    """
    标记所有缓存的新消息为已读（清空消息缓存）。

    调用此工具后，wechat://messages/new 资源将变为空列表。
    """
    await ensure_monitor_started()
    global message_cache
    cleared = len(message_cache)
    message_cache = []
    return json.dumps({
        "success": True,
        "cleared_count": cleared,
        "message": f"已标记 {cleared} 条消息为已读",
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def update_config(
    poll_interval_seconds: Optional[int] = None,
    important_keywords: Optional[list[str]] = None,
    important_senders: Optional[list[str]] = None,
    important_groups: Optional[list[str]] = None,
    notify_all_messages: Optional[bool] = None,
) -> str:
    """
    更新 MCP Server 配置。

    Args:
        poll_interval_seconds: 轮询间隔（秒），可选
        important_keywords: 重要关键词列表，可选
        important_senders: 重要发送者列表（昵称/备注名），可选
        important_groups: 重要群列表，可选
        notify_all_messages: 是否所有新消息都通知，可选

    Returns:
        更新后的配置
    """
    await ensure_monitor_started()
    global config

    if poll_interval_seconds is not None:
        config["poll_interval_seconds"] = max(1, poll_interval_seconds)
    if important_keywords is not None:
        config["important_keywords"] = important_keywords
    if important_senders is not None:
        config["important_senders"] = important_senders
    if important_groups is not None:
        config["important_groups"] = important_groups
    if notify_all_messages is not None:
        config["notify_all_messages"] = notify_all_messages

    save_config(config)
    return json.dumps({
        "success": True,
        "config": config,
        "message": "配置已更新",
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_message_history(chat_name: str, limit: int = 20, since: Optional[str] = None) -> str:
    """
    获取指定会话的历史消息。

    Args:
        chat_name: 会话名称（联系人昵称或群名）
        limit: 返回的最大消息数量，默认 20
        since: 起始日期（YYYY-MM-DD），可选

    Returns:
        JSON 格式的历史消息列表
    """
    await ensure_monitor_started()
    args = ["history", chat_name, "--limit", str(limit), "--json"]
    if since:
        args.extend(["--since", since])
    result = run_wx_command(args)
    return json.dumps(result, ensure_ascii=False, indent=2)


# ============ 主入口 ============
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="微信消息监控 MCP Server")
    parser.add_argument("--sse", action="store_true", help="使用 SSE 传输模式（默认 STDIO）")
    parser.add_argument("--host", default="127.0.0.1", help="SSE 模式监听地址")
    parser.add_argument("--port", type=int, default=8765, help="SSE 模式监听端口")
    args = parser.parse_args()

    if args.sse:
        # SSE 模式：通过 HTTP 访问
        print(f"[MCP Server] SSE 模式启动: http://{args.host}:{args.port}/sse", file=sys.stderr)
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        # STDIO 模式：豆包直接启动进程，通过标准输入输出通信
        print("[MCP Server] STDIO 模式启动", file=sys.stderr)
        mcp.run(transport="stdio")
