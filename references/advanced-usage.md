# 高级用法与消息类型说明（wechat-control 参考）

> 查询记忆、多群对比、导出，以及语音/视频/位置/撤回/链接等特殊消息的处理。基础命令见 [commands.md](commands.md)。

## 1. 查询历史记忆（recent queries）

每次查询某会话后，记录到 `~/.wx-cli/recent_queries.json`（最多 50 条，时间倒序）：
```json
{ "chat": "会话名或ID", "type": "history/search/stats/unread", "time": "2026-09-10 13:00", "keyword": "可选" }
```
- 用户说"上次那个群""最近查的""刚才那个""再看看那个群" → 先读该文件找最近记录，有多个候选就列出确认，不猜测。
- 工作流：每次 `history`/`search`/`stats` 后追加记录（同一 chat 5 分钟内去重），超 50 条删最旧。

## 2. 多群对比

用户说"对比 A 群和 B 群""哪个群更活跃"时：
1. 分别 `wx history <群>` 取消息、`wx stats <群>` 取统计；
2. 分别总结核心话题；
3. 表格对比并给结论。

| 维度 | 群A | 群B |
|------|-----|-----|
| 消息量（近7天） | N 条 | M 条 |
| 参与人数 | X 人 | Y 人 |
| 最活跃时段 | 时段 | 时段 |
| 核心话题 | 话题1、话题2 | 话题3、话题4 |
| 共同话题 / 差异话题 | … | … |

## 3. 导出

`wx export` 导出 Markdown，可限定时间，便于存档或在 Typora/Obsidian 查看；纯文本、不含附件。大群先 `wx stats`，超 1000 条限定时间范围。命令见 [commands.md](commands.md)。

## 4. 各类消息怎么呈现

- **语音消息**：`wx history` 中显示 `[语音]`。wx-cli 本身不解码语音，本技能用脚本补齐（SILK V3 解码 + FunASR SenseVoiceSmall 本地转写）：批量转历史语音用 `voice-transcribe.py transcribe`，新语音实时转写用 `voice-monitor.py monitor`。存储机制、字段坑、发送者识别与验证步骤见 [voice-pipeline.md](voice-pipeline.md)。
- **视频消息**：history 中显示 `[视频]`，需在微信里点击下载后才存本地；wx-cli 的 attachments 暂不支持视频解密导出。
- **位置消息**：history 中显示完整 XML，含经纬度、地址、POI 名称，可解析出位置信息。
- **撤回消息**：显示 `[系统] "xxx" 撤回了一条消息`，撤回内容无法恢复。
- **链接消息**：普通链接能拿到完整 URL；小程序链接在微信 4.1.8 不支持展示，显示"当前版本不支持展示该内容"。
