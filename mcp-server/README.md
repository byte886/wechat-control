# 微信消息监控 MCP Server

基于 FastMCP 4.x 构建的微信消息监控 MCP Server，支持：
- 实时监控微信新消息（文字/语音/图片/视频/链接/系统消息）
- 重要消息判定（关键词/特定发送者/特定群）
- 主动推送通知给 MCP 客户端（豆包）
- 历史消息查询
- 配置持久化

## 架构

```
微信客户端 (message_0.db / media_0.db)
        ↓
wx-cli (数据库解密 + 消息读取)
        ↓
MCP Server (FastMCP 4.x)
  ├── 后台轮询任务（每5秒检查新消息）
  ├── 资源：wechat://messages/new（新消息列表）
  ├── 工具：get_new_messages / get_monitor_status / mark_messages_as_read / update_config / get_message_history
  └── 通知：resources/updated + logging/message（主动推送给客户端）
        ↓
MCP 客户端（豆包）
```

## 安装

### 前置依赖
- Python 3.10+
- uv（Python 包管理器）
- wx-cli（已安装在 `/usr/local/bin/wx`）
- 微信数据库已解密（`~/.wx-cli/all_keys.json`）

### 安装步骤
```bash
cd ~/Doubao/skills/wechat-control/mcp-server
uv sync
```

## 运行

### STDIO 模式（推荐，豆包直接启动进程）
```bash
uv run python server.py
```

### SSE 模式（网络访问）
```bash
uv run python server.py --sse --host 127.0.0.1 --port 8765
# 访问: http://127.0.0.1:8765/sse
```

## 配置豆包连接（STDIO 模式）

1. 打开豆包设置 → MCP 服务器
2. 添加新服务器：
   - **名称**：`wechat-monitor`
   - **传输类型**：`STDIO`
   - **命令**：`uv`（配置框若要求绝对路径，先 `which uv` 自取，形如 `<home>/.local/bin/uv`）
   - **参数**：`run`, `python`, `~/Doubao/skills/wechat-control/mcp-server/server.py`
   - **工作目录**：`~/Doubao/skills/wechat-control/mcp-server`

   > MCP/GUI 配置框不会展开 `~`：上面含 `~` 的路径填入前，先 `echo ~/Doubao/skills/wechat-control/mcp-server/server.py`（工作目录同理）取得绝对路径再粘贴。
3. 保存并连接

## 资源（Resources）

| URI | 说明 |
|---|---|
| `wechat://messages/new` | 新消息列表（JSON），有新消息时服务器主动推送更新通知 |
| `wechat://messages/unread` | 未读消息（从 wx unread 获取） |
| `wechat://sessions` | 会话列表 |
| `wechat://config` | MCP Server 配置 |

## 工具（Tools）

### `get_new_messages`
获取微信新消息列表。

**参数**：
- `limit` (int, 默认10)：返回的最大消息数量
- `important_only` (bool, 默认False)：是否只返回重要消息

### `get_monitor_status`
获取微信消息监控状态（是否运行、轮询间隔、缓存消息数、最后轮询时间等）。

### `mark_messages_as_read`
标记所有缓存的新消息为已读（清空消息缓存）。

### `update_config`
更新 MCP Server 配置。

**参数**：
- `poll_interval_seconds` (int, 可选)：轮询间隔（秒）
- `important_keywords` (list[str], 可选)：重要关键词列表
- `important_senders` (list[str], 可选)：重要发送者列表（昵称/备注名）
- `important_groups` (list[str], 可选)：重要群列表
- `notify_all_messages` (bool, 可选)：是否所有新消息都通知（False 则只通知重要消息）

### `get_message_history`
获取指定会话的历史消息。

**参数**：
- `chat_name` (str, 必填)：会话名称（联系人昵称或群名）
- `limit` (int, 默认20)：返回的最大消息数量
- `since` (str, 可选)：起始日期（YYYY-MM-DD）

## 通知机制

MCP Server 检测到新消息时，会通过两种方式主动推送通知给客户端：

1. **资源更新通知** (`notifications/resources/updated`)：
   - 通知客户端 `wechat://messages/new` 资源已更新
   - 客户端收到通知后，应重新读取该资源获取最新消息

2. **日志消息通知** (`notifications/message`)：
   - 直接推送新消息的详细信息（发送者、内容、类型、是否重要等）
   - 客户端可以直接处理这些信息，无需重新读取资源

## 配置文件

- **配置文件**：`~/.wx-cli/mcp_server_config.json`
- **状态文件**：`~/.wx-cli/mcp_server_state.json`（已处理的消息 ID 等）

### 默认配置
```json
{
  "poll_interval_seconds": 5,
  "important_keywords": [],
  "important_senders": [],
  "important_groups": [],
  "notify_all_messages": true,
  "max_cached_messages": 100
}
```

## 重要消息判定规则

消息被判定为"重要"的条件（满足任一即可）：
1. 消息内容包含 `important_keywords` 中的任一关键词
2. 消息发送者在 `important_senders` 列表中
3. 消息来自 `important_groups` 中的群

## 使用示例

### 豆包中查询新消息
```
用户：有什么新消息？
豆包：调用 get_new_messages(limit=10) → 返回新消息列表
```

### 配置重要消息通知
```
用户：只通知我包含"紧急"或"重要"的消息
豆包：调用 update_config(important_keywords=["紧急", "重要"], notify_all_messages=false)
```

### 查询历史消息
```
用户：张三最近聊了什么？
豆包：调用 get_message_history(chat_name="张三", limit=20) → 返回历史消息
```

## 故障排查

### 1. MCP Server 启动失败
- 检查 uv 是否安装：`uv --version`
- 检查依赖是否安装：`cd ~/Doubao/skills/wechat-control/mcp-server && uv sync`
- 检查 Python 版本：`uv run python --version`（需要 3.10+）

### 2. 检测不到新消息
- 检查 wx-cli 是否正常：`wx sessions --limit 3`
- 检查数据库密钥是否存在：`ls ~/.wx-cli/all_keys.json`
- 检查 daemon 是否运行：`ps aux | grep wx`
- 检查 MCP Server 日志：STDERR 输出

### 3. 收不到通知
- 确认客户端支持 `notifications/resources/updated` 和 `notifications/message`
- 检查 `notify_all_messages` 配置（如果为 false，只有重要消息才通知）
- 检查重要消息判定规则是否正确配置

## 技术细节

- **框架**：FastMCP 4.0.3
- **传输协议**：STDIO（默认）/ SSE（可选）
- **MCP 协议版本**：2024-11-05
- **后台轮询**：asyncio.create_task，懒加载（第一个工具调用时启动）
- **消息去重**：基于 local_id + timestamp + chat 的组合 ID
- **状态持久化**：JSON 文件，最多保留最近 500 个已处理消息 ID

## 相关文档

- [wechat-control SKILL.md](../SKILL.md) — 微信自动化工具总览
- [realtime-monitor.py](../scripts/realtime-monitor.py) — 独立实时监听器（非 MCP）
