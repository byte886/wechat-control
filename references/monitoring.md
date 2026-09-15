# 主动监控与每日总结 · 操作手册

> 本文档聚焦 **wx-monitor.py 的每日总结 / recommend 选群 / 重要消息推送**（运行面参考，按需读取）。

**监控/采集脚本分工（按场景选，不要混用）**：

| 脚本 | 定位 | 何时用 / 详见 |
|---|---|---|
| `wx-monitor.py` | 群监控 ＋ **每日总结 / recommend 选群** / 飞书同步（⏳ 未接线） | 要 daily 总结、recommend 选群 → 本文 |
| `realtime-monitor.py` | **实时监听**新消息、daemon 自检自愈、重要性判定、可选语音转写 | 要实时盯新消息 → [realtime-and-daemon.md](realtime-and-daemon.md) |
| `voice-monitor.py` | 语音消息实时监控 ＋ 转写（直查 media_0.db） | 只盯语音、来一条转一条 → [voice-pipeline.md](voice-pipeline.md) |
| `voice-transcribe.py` | 语音批量转文字（SILK 解码 ＋ FunASR） | 一次性转历史语音 → voice-pipeline.md |
| `message-collector.py` | 直读 message/media 库，33 种消息类型采集/统计 | wx-cli 覆盖不到的类型（图片/视频/文件等） |
| `mcp-server/server.py` | 以 MCP（STDIO/SSE）向豆包暴露新消息资源与工具 | 要在豆包内 MCP 常驻接入，见 mcp-server/README |

## 1. 概述

基于第一阶段的只读查询能力，扩展主动监控、实时推送、每日总结。
- **监控脚本**：`scripts/wx-monitor.py`
- **配置文件**：`~/.wx-cli/monitor_config.json`
- **状态文件**：`~/.wx-cli/monitor_state.json`
- **每日总结存档**：`~/.wx-cli/daily_summary_YYYY-MM-DD.md`

## 2. 快速开始

```bash
# 1. 推荐监控群（AI 分析活跃度，给出推荐列表，用户选择）
python3 scripts/wx-monitor.py recommend

# 2. 查看当前配置
python3 scripts/wx-monitor.py config list

# 3. 启动监控（前台运行，Ctrl+C 停止）
python3 scripts/wx-monitor.py monitor

# 4. 单次轮询（测试用，不进入循环）
python3 scripts/wx-monitor.py monitor --once

# 5. 生成当日总结
python3 scripts/wx-monitor.py daily

# 6. 生成当日总结并尝试同步飞书（⚠️ 飞书同步尚未接线，脚本内为 TODO 占位，当前仅打印标记，见 wx-monitor.py:542）
python3 scripts/wx-monitor.py daily --sync

# 7. 查看监控状态
python3 scripts/wx-monitor.py status
```

## 3. 命令详解

| 命令 | 说明 |
|------|------|
| `recommend` | 分析所有群的活跃度和人数，按消息量排序给出推荐列表，用户输入序号选择 |
| `monitor` | 启动监控（前台运行），每 N 分钟轮询 `wx new-messages`，重要消息实时推送 |
| `monitor --once` | 单次轮询，测试用 |
| `daily` | 生成当日所有监控群的总结（消息统计、重要消息、消息类型、最近文本） |
| `daily --sync` | ⏳ 飞书同步尚未接线（脚本 TODO，当前仅打印"飞书同步功能开发中"标记） |
| `config list` | 查看当前配置 |
| `config add-group <群名/ID>` | 添加监控群 |
| `config remove-group <群名/ID>` | 移除监控群 |
| `config add-keyword <关键词>` | 添加自定义关键词 |
| `config remove-keyword <关键词>` | 移除关键词 |
| `config add-person <人名>` | 添加特定人（发任何消息都推送） |
| `config remove-person <人名>` | 移除特定人 |
| `status` | 查看监控状态（启动时间、上次轮询、累计推送） |

## 4. 重要消息判定规则（优先级从高到低）

1. **@我 或 @all**（必须推送，默认开启）
2. **自定义关键词匹配**（用户配置，如"紧急"、"重要"、"截止"、"开会"）
3. **特定人发送的消息**（用户配置，如老板、家人、重要客户）
4. **系统消息**（入群/退群/撤回，默认不推送）
5. **其他** → 不实时推送，只进每日总结

不重要的消息（普通聊天、图片、视频、语音等）不会实时推送，只在每日总结里出现，避免信息过载。

## 5. 推送通道

- **重要消息**：豆包内实时通知（包含群名、发送人、时间、触发原因、消息内容）
- **每日总结**：豆包推送（飞书同步 ⏳ 开发中，见 wx-monitor.py TODO；规划位置：AI 知识库 → 豆包 → 微信自动化 → 监控总结）
- **后续需求（已记录，暂不实现）**：微信推送到指定人/群、公众号通知、飞书消息/文档通道

## 6. 配置说明

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `monitor_groups` | 监控的群列表 `[{"name": "...", "id": "..."}]` | `[]` |
| `keywords` | 自定义关键词列表 | `[]` |
| `important_persons` | 特定人列表 | `[]` |
| `push_at_mention` | @我 时推送 | `true` |
| `push_at_all` | @all 时推送 | `true` |
| `poll_interval_minutes` | 轮询间隔（分钟） | `5` |
| `daily_summary_time` | 每日总结时间 | `21:00` |
| `feishu_sync` | 是否同步到飞书（⏳ 配置项已留，同步逻辑未接线） | `false` |
| `current_user_nickname` | 当前用户昵称（用于 @我 判断） | `""` |

## 7. 性能保护

- 单次轮询新消息超过 **500 条**时，输出性能提醒，建议减少监控群或增加轮询间隔
- 建议监控群不超过 **20 个**，超过时提醒
- 已推送消息 ID 最多保留 **1000 条**，自动清理旧记录（防止状态文件过大）
- 轮询间隔可配置（1-30 分钟），默认 5 分钟，对性能影响极小

## 8. 推荐话术流程（AI 推荐监控群）

1. AI 运行 `wx sessions` 获取所有会话
2. 对每个群运行 `wx stats` 获取消息量和活跃度
3. 按活跃度（近7天消息量）排序，取 Top 10
4. 输出推荐列表（群名、消息量、人数、推荐理由）
5. 用户输入序号选择，写入配置
6. 用户也可以手动添加不在推荐列表的群
