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
- **撤回消息**：显示 `[系统] "xxx" 撤回了一条消息`，撤回正文无法从当前库恢复。**不做防撤回/撤回恢复/代撤回**（稳定实现需注入或 hook 微信进程，曾触发风控，见 ROADMAP W-6）。唯一例外：实时监控若在对方撤回**之前**已把该消息抓取落盘，本地副本仍在，可标注"该消息后被撤回"——这只是只读存档的偶然附带（best-effort），监控未运行、轮询间隔内撤回、媒体未下载完即撤回都会漏，不承诺。
- **链接消息**：普通链接能拿到完整 URL；小程序链接在微信 4.1.8 不支持展示，显示"当前版本不支持展示该内容"。

## 5. 合并聊天记录 / 收藏卡片：全量提取（绕过 CLI 前 10 条截断）

`wx history "<会话>"` 对「合并聊天记录」「[链接]收藏卡片」只显示前 10 条并提示"还有 N 条"——这是 wx-cli `src/daemon/query.rs` 里 `take(10)` 的硬截断，不是数据缺失。需要全量列表时，只读直读数据库（不改工具、不重编译）：

1. **定位分表**：表名 `Msg_<md5(username)>`，username 是会话的原始 ID（文件传输助手为 `filehelper`，md5 用小写 hex）。库为 `db_storage/message/message_0.db`。
2. **取密钥**：`~/.wx-cli/all_keys.json` 的 `["message/message_0.db"]["enc_key"]`（64 hex）。连接只用 `PRAGMA key = "x'<enc_key>'";`，无需 cipher_compatibility。
3. **取消息体**：
   ```sql
   SELECT local_id, local_type, WCDB_CT_message_content, hex(message_content)
   FROM Msg_<md5> WHERE local_id IN (<id>,...);
   ```
   - 合并记录 `local_type = 81604378673`；`WCDB_CT_message_content = 4` 表示 `message_content` 是 **zstd 压缩 BLOB**（TEXT 则直接是 XML）。
   - `hex()` 输出 → Python `bytes.fromhex` → `zstd -d -c`（本机用 zstd CLI，Python 无 zstandard 模块）→ appmsg XML。
4. **解析**：`<recorditem>` 内嵌 `<![CDATA[<recordinfo>…<desc>…]]>`；注意 CDATA 里的 `<desc>` **只是前 5 条 + 字面 `...` 的截断摘要**（换行是 `&#x0A;`、空格是 `&#x20;`）。**完整条目在整条解压 XML 中重复出现**，直接对解压 XML 用正则 `https?://www\.doubao\.com/thread/[A-Za-z0-9]+`（或按实际数据类型换模式）抓全，同条消息内保序去重即可；`...` 是"还有更多条"标记，不是 URL 被截断。
5. **多份合并**：跨多条消息按时间或衔接关系拼接、保序去重。

实测（2026-09-16，大号文件传输助手）：local_id 218（97 条）+ 219（28 条），交集 1 条（首尾相接），合并去重得 124 条豆包 thread 链接。

> **更通用的目标已立项为监控子功能**：不止提取链接，而是识别任意合并记录并把每条子消息（发送人 / 时间 / 类型 / 文本 / 链接 / 媒体）结构化拆分、实时监控自动展开——见 PRD §2.3.7 与 ROADMAP **M-11（待办）**。本节是其已验证的"链接全量提取"路径。
