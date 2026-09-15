---
name: wechat-control
description: "微信本地数据读取与 AI 总结工具。基于 jackwener/wx-cli，通过解密微信本地 SQLite 数据库实现只读查询：会话列表、聊天历史、消息搜索、联系人、群成员、未读消息、统计分析、朋友圈、公众号、附件解密等。适用于用户问'XX群最近聊了什么'、'搜一下关于XX的消息'、'有哪些未读'、'XX群有哪些人'等自然语言查询，自动调用 wx-cli 并返回 AI 总结。全程只读本地文件、零风控风险；另在严格风控下提供**仅限小号**的写操作（发消息/自动回复，见 §10，禁止用于大号）。不做加好友、删好友、朋友圈/公众号发布等写操作。"
compatibility: "macOS (Intel/Apple Silicon) / Windows / Linux。微信版本锁定 ~4.1.8（wx-cli 硬性限制，4.1.10+ 密钥存储格式变更导致提取失败）。密钥提取需关 SIP（macOS）或管理员权限（Windows）。"
---

# wechat-control · 微信本地数据读取与 AI 总结

> 基于 jackwener/wx-cli 的微信本地数据只读工具。解密微信本地数据库，支持自然语言查询和 AI 总结。全程只读，零风控风险。

## 0. 文件地图与加载边界（先读）

**运行面（AI 用本技能时只加载这些，随公开仓分发）**：
- `SKILL.md`（本文件，主入口）＋ `references/`（错误处理、歧义处理、大消息量、时间解析、新号 SOP，按需读取）
- `scripts/`（Python/shell 脚本）、`mcp-server/`（可选 MCP 服务）、`tools/silk-v3-decoder/`（SILK 语音解码）、`third-party/wx-cli/`（本人 fork 的多账号改造版，git 子模块）

**开发文档 `docs/`（给人看，AI 运行时不加载）**：PRD、功能清单、路线图、阶段 spec、监控规划等设计文档随公开仓分发，便于理解设计与协作；但 AI 用本技能时**不要加载 `docs/`**（运行面只需本文件 + `references/`）。其中 `test-cases.md`（本人账号实测日志）与 `database-schema.md`（从本机库 dump 的数据字典，含个人标识）仅维护者本地保留、在 `.gitignore`、不入库；需要表结构时由维护者查本地副本。

**路径约定**：命令中的 `scripts/...` 是相对技能根目录的路径，默认在技能根目录下执行；在其他目录执行时，把 `scripts/` 替换为 `~/Doubao/skills/wechat-control/scripts/`（`~` 双机自适应，不要写死用户名）。

## 1. 什么时候用

用户用自然语言询问微信相关信息时使用，典型触发：
- "XX群/XX人最近聊了什么？"
- "我有哪些未读消息？"
- "帮我搜一下关于XX的消息"
- "XX群有哪些人？"
- "XX群最近活跃度怎么样？"
- "XX群最近发了哪些图片/文件？"
- "朋友圈最近有什么动态？"
- "公众号最近推了什么？"

**只读查询不做写操作**；发消息/自动回复属于 §10 的受控写能力，仅允许小号、需用户明确确认。加好友、删好友、发朋友圈、操作微信界面（§10 UI 发送除外）等一律不做。

## 2. 核心原则（风控红线）

### 2.1 只读为主体，写操作严格隔离
- 只读查询（sessions/history/search/监控读取/语音转写等）只读取本地数据库文件，不碰微信进程、不联网、不操作微信界面，零风控
- 发消息、自动回复属于 §10 的**受控写操作**：仅限小号、手动确认、受频率与黑名单约束，禁止在大号执行
- **任何情况下都不做**：加好友、删好友、发朋友圈、公众号发布

### 2.2 密钥提取安全
- 密钥提取方式：**关 SIP + sudo + 不重签微信**
- **绝对不做**：ad-hoc 重签微信（`codesign --force --deep --sign -`）、Frida/lldb 持续注入 hook 微信进程
- 重签 + 持续注入曾触发微信风控（强制下线 + 违规提示），是红线
- 密钥提取是短暂操作（附加进程→扫描内存→退出），提取完立即释放

### 2.3 微信版本锁定
- wx-cli 当前只支持微信 ~4.1.8
- 微信 4.1.10+ 改变了密钥在内存中的存储格式，wx-cli 扫描不到
- **关闭微信自动更新**，不主动升级微信
- 未来 wx-cli 新版本支持更高版本后，可评估升级

### 2.4 密钥长期有效
- 同账号、同版本、不重装的情况下，密钥提取一次后**永久有效**
- 不需要重复扫描微信内存
- 仅在以下情况重新提取：微信版本升级、切换登录账号、重装微信

### 2.5 账号隔离
- 优先用微信小号操作，不用主号
- 不同账号的密钥和数据目录完全隔离，互不影响
- 切换账号后需重新执行 `wx init` 提取新账号密钥

**账号清单**（角色固定；真实昵称/wxid 属个人标识，不写入公开仓）：

| 角色 | 微信名称 | wxid（数据目录名） | 用途 |
|------|----------|---------------------|------|
| 大号（主号，配置名 main） | 〈主号昵称〉 | `〈主号wxid，形如 wxid_xxxxxxxx_yyyy〉` | 日常使用，只读查询，禁止写操作 |
| 小号（自动化专用，配置名 alt） | 〈小号昵称〉 | `〈小号wxid〉` | 写操作测试、自动回复、UI自动化 |

> 真实昵称与 wxid 用 `bash scripts/wx-account.sh list` 现查，或看本机不入库的 `~/.wx-cli/accounts/main|alt/` 目录；本文档与 references 一律用"主号/小号"角色名和占位符，不要把真实值填回来。

**写操作风控**：发送消息、自动回复等写操作**仅允许在小号（alt）上执行**，禁止在主号（main）上执行写操作，避免触发风控影响主号。只读查询（wx sessions/history/search等）可在任意已提取密钥的账号上执行。

## 3. 安装与卸载

### 3.1 前置依赖

**通用**：
- Node.js >= 18
- 微信 ~4.1.8 已安装并登录（4.1.10+ 密钥提取可能失败，见 §2.3）

**macOS**：
- SIP 已关闭（密钥提取时需要，见 §4）
- Homebrew 推荐用于安装 Node.js：`brew install node`

**Windows（未实测，基于官方文档）**：
- 以**管理员身份**运行 PowerShell（读取进程内存需要管理员权限）
- Node.js 可从 nodejs.org 安装，或用 winget：`winget install OpenJS.NodeJS`
- 不需要关 SIP（Windows 无此机制）
- Windows 微信进程名：`Weixin.exe`

### 3.2 安装 wx-cli

> **先选版本**：
> - **只做单账号只读查询/总结**：装下面的 npm 官方版（`@jackwener/wx-cli`）即可。
> - **需要多账号切换**（在主号/小号间切换 wx-cli 指向，见 references/new-account-sop.md §A.4.2、§A.6）：npm 官方版不支持，须改用本仓 `third-party/wx-cli`（本人 fork 的多账号改造版）从源码编译并替换 `wx`，步骤见 new-account-sop §A.4.2。

**通用方式（npm，单账号只读推荐）**：
```bash
# npm 全局安装（官方推荐，macOS/Windows/Linux 通用）
npm install -g @jackwener/wx-cli

# 如果 postinstall 脚本被 npm 安全策略阻止，加 --allow-scripts
npm install -g --allow-scripts=@jackwener/wx-cli @jackwener/wx-cli

# 验证安装
wx --version  # 应输出 0.3.0 或更高
```

> macOS 注意：如果系统中有多个 node（如豆包沙箱 node shadow 了 Homebrew node），用 Homebrew node 的完整路径执行：Intel 机为 `/usr/local/bin/npm`、Apple Silicon 机为 `/opt/homebrew/bin/npm`（也可用 `$(brew --prefix)/bin/npm` 自取），例：`/usr/local/bin/npm install -g @jackwener/wx-cli`
>
> Windows 注意：需在**管理员 PowerShell** 中执行 npm 安装命令；安装后 `wx` 命令应在 PATH 中可用。

**Windows 一键脚本（未实测，官方提供）**：
```powershell
# 以管理员身份运行 PowerShell
irm https://raw.githubusercontent.com/jackwener/wx-cli/main/install.ps1 | iex
```

### 3.3 卸载 wx-cli

**通用（npm）**：
```bash
# 卸载 npm 包
npm uninstall -g @jackwener/wx-cli

# 验证卸载
which wx    # macOS/Linux：应返回 not found
where wx    # Windows：应返回 not found
```

**清理配置和密钥（可选，谨慎操作）**：
- macOS/Linux：`rm -rf ~/.wx-cli/`
- Windows：`Remove-Item -Recurse -Force "$env:USERPROFILE\.wx-cli"`

### 3.4 其他安装方式
- **macOS/Linux 一键脚本**（不推荐，曾出现 404）：`curl -fsSL https://raw.githubusercontent.com/jackwener/wx-cli/main/install.sh | bash`
- **Windows 一键脚本**（未实测）：见 §3.2 的 PowerShell 命令
- **手动下载二进制**：从 GitHub Releases 下载对应平台文件（macOS arm64/x64、Windows x64、Linux x64/arm64），放到 PATH 目录并加执行权限（Windows 无需 chmod）

## 4. 密钥提取（关键步骤）

### 4.1 前置条件
- 微信已登录并运行（必须是要提取密钥的那个账号）
- 微信保持官方签名，**不要重签**

**macOS**：
- SIP 已关闭（`csrutil status` 应显示 `disabled`）

**Windows（未实测）**：
- 以**管理员身份**运行 PowerShell（读取 Weixin.exe 进程内存需要管理员权限）
- 不需要关 SIP（Windows 无此机制）
- 如遇 Windows Defender 或杀毒软件拦截内存读取，需添加信任或临时关闭（谨慎操作）

### 4.2 执行提取

**macOS**：
```bash
# 用 sudo 执行（关 SIP 后不需要重签）
sudo wx init

# 如果之前有旧配置，强制重新扫描
sudo wx init --force
```

**Windows（未实测）**：
```powershell
# 以管理员身份运行 PowerShell
wx init

# 强制重新扫描
wx init --force
```
> Windows 上 `wx init` 会自动检测 `Weixin.exe` 进程和数据目录（通常在"文档\WeChat Files\<wxid>\"下，具体路径可在微信设置→文件管理中查看）。

### 4.3 成功标志
```
找到 19 个加密数据库
扫描进程内存寻找密钥...
找到 18 个候选密钥
匹配到 16/18 个密钥
成功提取 16 个数据库密钥
密钥已保存: ~/.wx-cli/all_keys.json
配置已保存: ~/.wx-cli/config.json
```

### 4.4 常见失败原因

| 错误 | 原因 | 解决 |
|------|------|------|
| macOS: `task_for_pid 失败 (kr=5)` | SIP 未关闭，或未用 sudo | 关 SIP，用 `sudo wx init` |
| Windows: 权限不足 / 拒绝访问 | 未以管理员身份运行 PowerShell | 右键 PowerShell → 以管理员身份运行 |
| Windows: 被杀毒软件拦截 | Defender/杀毒软件阻止读取进程内存 | 添加 wx-cli 信任，或临时关闭实时防护（谨慎） |
| `找到 0 个候选密钥` | 当前登录账号与数据目录不匹配，或微信版本过高（4.1.10+） | 确认微信登录的是目标账号；降到微信 ~4.1.8 |
| 提示需要重签微信 | wx-cli 默认文案假设重签 | **忽略该提示**，关 SIP + sudo（macOS）或管理员 PowerShell（Windows）即可不重签提取 |

### 4.5 验证密钥
```bash
# 能正常输出会话列表即说明密钥有效
wx sessions
```

### 4.6 数据库完整性说明（为什么不是所有库都有密钥）

微信 4.x 的 `db_storage/` 目录下可能有 **20 个甚至更多** `.db` 文件（数据量大时 message/biz_message 会分片，完整可达 32 个），但 `wx init` 通常只能提取到 **17 个左右**的密钥。这是**正常现象**，不是 bug。

**根本原因：微信 4.x 每个数据库使用独立密钥 + 懒加载机制。** 数据库只有在对应功能被实际使用时，微信才会打开它并把密钥加载进内存；`wx init` 是从微信进程内存扫描密钥，从未被使用过的库，其密钥从未进入内存，自然扫不到。

**实测缺失的 3 个库（均为边缘功能，对核心需求无影响）：**

| 数据库 | 位置 | 大小 | 用途 | 为什么没密钥 |
|--------|------|------|------|-------------|
| `chatbot_message.db` | `chatbot/` | ~48KB | 微信内置 AI 助手对话消息（元宝入口、AI 搜索、AI 总结等） | 从未使用过微信 AI 对话功能 |
| `third_app_icon.db` | `third_app_icon/` | ~12KB | 第三方应用图标缓存（小程序、外部 App、应用号图标） | 纯缓存，价值极低 |
| `weclaw.db` | `message/` | ~4KB（空库） | 微信官方客户端辅助/AI Agent 组件库（对应生态内 OpenClaw/ClawBot 能力） | 自建库以来从未被打开，连 `-wal/-shm` 边车文件都没有 |

> ⚠️ 注意：`weclaw.db` 是**微信官方自带**的库，与网上第三方开源项目 WeClaw（WeChat AI Bridge）只是恰好同名，没有任何关系。

**如何验证（三重证据）：**
1. 时间戳：这 3 个库停留在微信安装初始化日期（如 Aug 23），之后从未被写过；而在用的 `message_0.db` 等持续更新
2. `weclaw.db` 只有 4KB（一个空 SQLite 页），无 `-wal/-shm` 文件 → 从未被打开
3. `lsof -p <微信PID>` 确认这 3 个库当前未被微信进程加载

**如果将来真要解密（目前没必要）：** 在微信里触发一次对应功能（如用一次 AI 对话、打开一个小程序），让库加载进内存，再重跑 `sudo wx init --force` 扫描。

**结论：这 3 个库对核心需求（读聊天记录、监控群消息、语音转文字、联系人查询）没有任何价值——聊天内容在 `message_0.db`、媒体在 `media_0.db`、联系人在 `contact.db`，17 个密钥已全覆盖。可以直接忽略。**

## 5. 常用命令详解

> 所有命令默认输出 YAML 格式，加 `--json` 输出 JSON。输出较大时用 `--limit N` 限制数量。

### 5.1 会话列表 `wx sessions`
列出最近的会话（群聊、私聊、公众号、品牌服务等）。
```bash
wx sessions                          # 列出所有会话
wx sessions --limit 20               # 只看前 20 个
```
输出字段：chat（名称）、chat_type（group/private/official_account/folded）、is_group、last_msg_type、summary、time、unread、username。

### 5.2 聊天记录 `wx history <CHAT>`
查看指定会话的聊天记录。CHAT 可以是群名、人名、或 username/chatroom ID。
```bash
wx history "产品群"                   # 查看产品群历史
wx history "张三" --limit 50         # 查看与张三的最近 50 条
wx history "产品群" --since 2026-09-01  # 从指定日期开始
wx history "产品群" --json            # JSON 格式输出
```
输出字段：content、sender、time、timestamp、type（文本/图片/语音/视频/链接/文件/系统等）、local_id。

> 注意：群名可能显示为 chatroom ID（形如 `xxxxxxxxxx@chatroom`），这是因为群名称尚未同步到本地数据库。用该 ID 同样可以查询。

### 5.3 消息搜索 `wx search <KEYWORD>`
全文搜索消息关键词。
```bash
wx search "预算"                      # 搜索所有聊天中的"预算"
wx search "预算" --in "产品群"        # 只在产品群中搜索
wx search "预算" --since 2026-01-01  # 限定时间范围
wx search "预算" --type text           # 只搜文本消息
wx search "预算" --limit 10            # 限制结果数
```
> 注意：合并聊天记录的内容、公众号推送的标题/摘要可能不被全文索引。如果搜索结果为空，尝试换关键词或扩大时间范围。

### 5.4 未读消息 `wx unread`
列出有未读消息的会话。
```bash
wx unread
```

### 5.5 联系人 `wx contacts`
列出所有联系人。
```bash
wx contacts --limit 20
```

### 5.6 群成员 `wx members <CHAT>`
查看指定群的成员列表。
```bash
wx members "产品群"
```
输出字段：display（显示名）、contact_display、group_nickname（群昵称）、is_owner（是否群主）、username。

### 5.7 统计分析 `wx stats <CHAT>`
查看指定会话的统计数据（按小时、按消息类型等）。
```bash
wx stats "产品群"
```

### 5.8 新消息 `wx new-messages`
获取自上次检查以来的新消息（增量查询）。
```bash
wx new-messages
```

### 5.9 附件 `wx attachments <CHAT>` + `wx extract`
列出会话的图片/文件附件，并解密导出。
```bash
wx attachments "产品群"               # 列出附件，返回 attachment_id
wx extract <attachment_id> --output ~/Desktop/  # 解密导出到指定目录
```

### 5.10 导出 `wx export <CHAT>`
导出聊天记录到文件。
```bash
wx export "产品群" --output ~/Desktop/产品群聊天记录.txt
```

### 5.11 朋友圈相关
```bash
wx sns-feed                           # 朋友圈时间线（本地缓存）
wx sns-search "关键词"                 # 朋友圈全文搜索
wx sns-notifications                  # 朋友圈互动通知（点赞/评论）
```

### 5.12 公众号文章
```bash
wx biz-articles                       # 公众号文章推送（本地缓存）
```

### 5.13 收藏
```bash
wx favorites                          # 微信收藏内容
```

### 5.14 Daemon 管理
wx-cli 自带后台守护进程，缓存解密数据库，加速重复查询。通常自动管理，无需手动操作。
```bash
wx daemon status                      # 查看 daemon 状态
wx daemon stop                        # 停止 daemon
```

## 6. 自然语言意图映射

用户用自然语言提问时，按以下规则映射到 wx-cli 命令：

| 用户意图 | 典型说法 | 调用命令 |
|---------|---------|---------|
| 查未读 | "有哪些未读？"、"谁给我发消息了？" | `wx unread` |
| 查某群/某人最近聊天 | "XX群最近聊了什么？"、"张三最近发了什么？" | `wx history "<名称>" --limit 20` |
| 搜消息 | "搜一下关于XX的消息"、"有没有人提过XX？" | `wx search "<关键词>"` |
| 搜指定群内消息 | "在产品群里搜一下XX" | `wx search "<关键词>" --in "<群名>"` |
| 查群成员 | "XX群有哪些人？"、"谁在这个群里？" | `wx members "<群名>"` |
| 查群活跃度 | "XX群最近活跃吗？"、"这个群每天发多少消息？" | `wx stats "<群名>"` |
| 查联系人 | "我有哪些联系人？"、"有没有XX这个人？" | `wx contacts` |
| 查图片/文件 | "XX群最近发了什么图片？"、"那个文件在哪？" | `wx attachments "<名称>"` |
| 查朋友圈 | "朋友圈最近有什么？"、"谁给我点赞了？" | `wx sns-feed` / `wx sns-notifications` |
| 查公众号 | "公众号最近推了什么？" | `wx biz-articles` |

### 歧义处理
- 群名/人名有歧义时（如多个群包含"产品"二字），**先调用 `wx sessions` 列出候选，反问用户确认具体指哪个**，不要猜测
- 时间范围不明确时，默认查最近 20 条或最近 7 天，在回答中说明"默认查了最近 N 条，需要更早的告诉我"

## 7. AI 总结模板

### 7.1 默认格式（简短要点）
查询结果返回后，按以下格式总结：

```
【<会话名> · <时间范围>】共 N 条消息，M 人参与

核心话题：
1. <话题1简要>
2. <话题2简要>
3. <话题3简要>

重要提醒：
- ⚠️ @我的消息：<内容>
- 📌 关键决策：<内容>
- 🔥 紧急事项：<内容>

（需要详细展开某个话题，或看原始消息，告诉我）
```

### 7.2 详细格式（用户说"详细点"时）
按话题分类，带原始消息引用：
```
【<会话名> · <时间范围>】

【话题1：<话题名>】
- <发送人>: <消息内容>
- <发送人>: <消息内容>
小结：<该话题的结论或进展>

【话题2：<话题名>】
...

【重要消息标注】
- @我：...
- 决策：...
- 紧急：...
```

### 7.3 未读消息格式
```
【未读消息】共 N 条（M 个独立来源）

📢 公众号推送：
1. <公众号名>（<时间>）：<摘要>

💬 私聊/群聊：
1. <名称>（<时间>）：<摘要>

⚠️ 重要提醒：
- 无 @我的消息 / 有 @我：<内容>
- 无紧急事项 / 紧急：<内容>
```

### 7.4 重要消息识别规则
- **@我**：消息内容包含 @当前用户昵称或 @all
- **关键决策**：包含"决定/通过/同意/否决/截止/ deadline / 安排"等关键词
- **紧急事项**：包含"紧急/马上/立刻/今天必须/ ASAP"等关键词，或时间敏感内容

## 8. 高级用法与体验优化

### 8.1 查询历史记忆（recent queries）
每次用户查询某个会话后，AI 自动记录到 `~/.wx-cli/recent_queries.json`，方便后续快捷查询。

**记录格式**：
```json
{
  "chat": "会话名或ID",
  "type": "history/search/stats/unread",
  "time": "2026-09-10 13:00",
  "keyword": "用户用的关键词（可选）"
}
```

**使用场景**：
- 用户说"上次那个群"、"最近查的"、"刚才那个" → 从 recent_queries.json 找最近的记录
- 用户说"再看看那个群" → 直接用最近一次查询的会话
- 最多保留 50 条，按时间倒序；有多个候选时列出让用户确认

**AI 工作流**：
1. 每次执行 `wx history` / `wx search` / `wx stats` 等查询后，读取 recent_queries.json
2. 追加新记录（去重：同一 chat 5 分钟内不重复记录）
3. 超过 50 条时删除最旧的
4. 用户用"上次/最近/刚才"等指代时，先读 recent_queries.json 找候选

### 8.2 多群对比查询
用户说"对比一下 A 群和 B 群"、"这两个群最近都在聊什么"、"哪个群更活跃"时：

**工作流**：
1. 分别调用 `wx history <群A>` 和 `wx history <群B>` 获取消息
2. 分别用 `wx stats` 获取统计数据
3. 分别总结核心话题
4. 用表格对比：消息量、参与人数、活跃时段、共同话题、差异话题
5. 给出结论：哪个群更活跃、关注点有何不同

**对比表格模板**：
```
| 维度 | 群A | 群B |
|------|-----|-----|
| 消息量（近7天） | N 条 | M 条 |
| 参与人数 | X 人 | Y 人 |
| 最活跃时段 | 时段 | 时段 |
| 核心话题 | 话题1, 话题2 | 话题3, 话题4 |
| 共同话题 | 共同话题 | |
```

### 8.3 导出功能
`wx export` 可以把聊天记录导出为 Markdown 文件，方便存档或用其他工具查看。

**命令**：
```bash
# 导出全部聊天记录
wx export "群名" --output ~/Downloads/chat_export.md

# 限定时间范围
wx export "群名" --since 2026-09-01 --until 2026-09-10 --output ~/Downloads/chat_week.md

# 导出私聊
wx export "联系人名" --output ~/Downloads/private_chat.md
```

**使用建议**：
- 大群导出前先 `wx stats` 看消息量，超过 1000 条建议限定时间范围
- 导出的 Markdown 包含时间、发送人、消息内容，格式清晰
- 导出后告诉用户文件路径，用户可以用 Typora、Obsidian 等工具查看
- 图片/视频等附件不会包含在导出里（纯文本导出）

### 8.4 语音/视频/位置消息说明
- **语音消息**：`wx history` 中显示为 `[语音]`——wx-cli 本身不解码语音，但本技能已用 `scripts/voice-transcribe.py`（silk-v3-decoder 解码 + FunASR SenseVoiceSmall 本地转写）补齐：批量转历史语音用 `voice-transcribe.py transcribe`、新语音实时转写用 `voice-monitor.py monitor`，存储机制与坑见 references/new-account-sop.md §A.7
- **视频消息**：history 中显示为 `[视频]`，需在微信里点击下载后才会保存到本地；wx-cli 的 attachments 目前只列图片，视频暂不支持解密导出
- **位置消息**：history 中显示完整的 XML，包含经纬度、地址、POI 名称，可以解析出位置信息
- **撤回消息**：显示为 `[系统] "xxx" 撤回了一条消息`，撤回的内容无法恢复
- **链接消息**：普通链接能拿到完整 URL；小程序链接微信 4.1.8 不支持展示，显示"当前版本不支持展示该内容"

## 9. 主动监控与每日总结（第二阶段）

### 9.1 概述
基于第一阶段的只读查询能力，扩展主动监控、实时推送、每日总结、飞书同步。
- **监控脚本**：`scripts/wx-monitor.py`
- **配置文件**：`~/.wx-cli/monitor_config.json`
- **状态文件**：`~/.wx-cli/monitor_state.json`
- **每日总结存档**：`~/.wx-cli/daily_summary_YYYY-MM-DD.md`

**监控/采集脚本分工（按场景选，不要混用）**：

| 脚本 | 定位 | 何时用 |
|---|---|---|
| `wx-monitor.py` | 群监控 + **每日总结 / 推荐监控群** / 飞书同步 | 要 daily 总结、recommend 选群（即本章命令） |
| `realtime-monitor.py` | **实时监听**新消息，daemon 自检自愈、重要性判定，可选 `--transcribe-voice` | 要实时盯新消息；详见 new-account-sop §A.12 |
| `voice-monitor.py` | 语音消息实时监控 + 转写（直查 media_0.db） | 只盯语音、来一条转一条 |
| `voice-transcribe.py` | 语音批量转文字（SILK 解码 + FunASR） | 一次性转写历史语音 |
| `message-collector.py` | 直读 message/media 库，33 种消息类型采集/统计 | wx-cli 覆盖不到的类型（图片/视频/文件等） |
| `mcp-server/server.py` | 以 MCP（STDIO/SSE）向豆包暴露新消息资源与工具 | 要在豆包内以 MCP 常驻接入，见 mcp-server/README |

### 9.2 快速开始
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

# 6. 生成当日总结并同步到飞书
python3 scripts/wx-monitor.py daily --sync

# 7. 查看监控状态
python3 scripts/wx-monitor.py status
```

### 9.3 命令详解

| 命令 | 说明 |
|------|------|
| `recommend` | 分析所有群的活跃度和人数，按消息量排序给出推荐列表，用户输入序号选择 |
| `monitor` | 启动监控（前台运行），每 N 分钟轮询 `wx new-messages`，重要消息实时推送 |
| `monitor --once` | 单次轮询，测试用 |
| `daily` | 生成当日所有监控群的总结（消息统计、重要消息、消息类型、最近文本） |
| `daily --sync` | 生成总结并同步到飞书知识库 |
| `config list` | 查看当前配置 |
| `config add-group <群名/ID>` | 添加监控群 |
| `config remove-group <群名/ID>` | 移除监控群 |
| `config add-keyword <关键词>` | 添加自定义关键词 |
| `config remove-keyword <关键词>` | 移除关键词 |
| `config add-person <人名>` | 添加特定人（发任何消息都推送） |
| `config remove-person <人名>` | 移除特定人 |
| `status` | 查看监控状态（启动时间、上次轮询、累计推送） |

### 9.4 重要消息判定规则（优先级从高到低）
1. **@我 或 @all**（必须推送，默认开启）
2. **自定义关键词匹配**（用户配置，如"紧急"、"重要"、"截止"、"开会"）
3. **特定人发送的消息**（用户配置，如老板、家人、重要客户）
4. **系统消息**（入群/退群/撤回，默认不推送）
5. **其他** → 不实时推送，只进每日总结

不重要的消息（普通聊天、图片、视频、语音等）不会实时推送，只在每日总结里出现，避免信息过载。

### 9.5 推送通道
- **重要消息**：豆包内实时通知（包含群名、发送人、时间、触发原因、消息内容）
- **每日总结**：豆包推送 + 飞书文档存档（AI 知识库 → 豆包 → 微信自动化 → 监控总结）
- **后续需求（已记录，暂不实现）**：微信推送到指定人/群、公众号通知

### 9.6 配置说明
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `monitor_groups` | 监控的群列表 `[{"name": "...", "id": "..."}]` | `[]` |
| `keywords` | 自定义关键词列表 | `[]` |
| `important_persons` | 特定人列表 | `[]` |
| `push_at_mention` | @我 时推送 | `true` |
| `push_at_all` | @all 时推送 | `true` |
| `poll_interval_minutes` | 轮询间隔（分钟） | `5` |
| `daily_summary_time` | 每日总结时间 | `21:00` |
| `feishu_sync` | 是否同步到飞书 | `false` |
| `current_user_nickname` | 当前用户昵称（用于 @我 判断） | `""` |

### 9.7 性能保护
- 单次轮询新消息超过 **500 条**时，输出性能提醒，建议减少监控群或增加轮询间隔
- 建议监控群不超过 **20 个**，超过时提醒
- 已推送消息 ID 最多保留 **1000 条**，自动清理旧记录（防止状态文件过大）
- 轮询间隔可配置（1-30 分钟），默认 5 分钟，对性能影响极小

### 9.8 推荐话术流程（AI 推荐监控群）
1. AI 运行 `wx sessions` 获取所有会话
2. 对每个群运行 `wx stats` 获取消息量和活跃度
3. 按活跃度（近7天消息量）排序，取 Top 10
4. 输出推荐列表（群名、消息量、人数、推荐理由）
5. 用户输入序号选择，写入配置
6. 用户也可以手动添加不在推荐列表的群

## 10. 写操作：发送消息与UI自动化（第三阶段）

### 10.1 定位与风控要求

写操作（发送消息）是**探索性质**的功能，与只读查询严格隔离。核心风控原则：

- **只用小号**：禁止对主号执行写操作
- **手动确认**：任何发送动作前必须经用户明确说"发送"（"好/对/嗯"不算）
- **频率限制**：每分钟≤3条、每小时≤20条、同一对象5分钟≤2条、23:00–08:00自动暂停
- **黑名单过滤**：10类敏感内容（金钱/隐私/承诺/敏感/辱骂/机密/专业建议/身份冒充/暴露AI/反问质疑）命中不发
- **发送前验证**：OCR识别当前聊天标题，确认对象正确后才发送

### 10.2 快速开始

```bash
# 发送消息到指定聊天（带完整验证，约9.3秒）
bash ~/Doubao/skills/wechat-control/scripts/wechat-ui/send_message.sh "文件传输助手" "消息内容"

# 快速模式（跳过发送后OCR验证，约8秒）
bash ~/Doubao/skills/wechat-control/scripts/wechat-ui/send_message.sh "文件传输助手" "消息内容" --no-verify
```

### 10.3 实现原理：为什么选 Cmd+F 方案

对比了三种UI控制方案，最终选择 **Cmd+F搜索+回车选中** 作为核心方案：

| 方案 | 可靠性 | 原因 |
|------|--------|------|
| OCR识别+坐标点击 | 不稳定 | 坐标转换易出错，窗口大小变化影响大，多次误点"搜索聊天记录"或误发群 |
| Computer Use / axcli | 不可靠 | 微信聊天区不暴露给AX（axcli仅9元素），搜索框/输入框不是可操作元素 |
| **Cmd+F搜索+回车选中** | **可靠** | 纯键盘操作，不依赖坐标，微信原生支持，已验证多次成功 |

**完整7步流程**：
1. reopen恢复窗口 + 调整大小 + 切回聊天页
2. Cmd+F搜索 → 清空 → 粘贴 → 回车选中
3. OCR验证当前聊天标题（安全检查）
4. 点击输入框（跳过导航栏+会话列表共232px）
5. 粘贴消息内容
6. 回车发送
7. OCR验证发送结果（--no-verify模式跳过）

### 10.4 健壮性处理

脚本已验证能处理以下极端场景：

| 场景 | 处理方式 |
|------|----------|
| 窗口最小化 | `reopen` 命令恢复窗口（比 `activate` 更可靠） |
| 窗口过小（看不到搜索框） | 检测尺寸 < 700x500 则自动调整到 700x500 |
| 当前在通讯录/收藏等非聊天页 | 点击左侧导航栏"微信"图标切回聊天页 |
| 搜索框有残留/重复输入 | Cmd+A全选 + Delete清空后再粘贴 |
| 有弹窗遮挡 | ESC关闭弹窗后再操作 |

### 10.5 性能与速度

| 模式 | 正常状态耗时 | 说明 |
|------|-------------|------|
| 默认模式（带双重OCR验证） | 9.3秒 | 发送前验证标题 + 发送后验证消息 |
| `--no-verify` 模式 | 8.0秒 | 只做发送前标题验证，跳过发送后验证 |

**各阶段耗时**（正常状态）：
- 步骤1 激活+恢复窗口+切回聊天页：~2秒
- 步骤2 Cmd+F搜索+选中：~1.5秒
- 步骤3 OCR验证标题：~2秒
- 步骤4-6 点击输入框+粘贴+发送：~1.5秒
- 步骤7 OCR验证发送结果：~2秒

### 10.6 文件结构

```
scripts/wechat-ui/
├── send_message.sh              # 主发送脚本（7步完整流程）
├── lib_wechat_ui.sh             # 坐标转换库（窗口rect获取、归一化坐标点击）
├── ocr_wechat_screenshot.sh     # macOS Vision OCR调用（中文+英文识别）
├── prepare_wechat_viewport.sh   # 激活微信到前台（不改变窗口大小）
└── search_chat_and_click_local_result.sh  # OCR搜索+点击备选方案（不稳定，仅参考）
```

### 10.7 自动回复（已实现框架，探索性质）

自动回复主循环已实现，定位为**探索性质**，不建议用于生产环境。

**快速开始**：
```bash
# 配置触发关键词
python3 ~/Doubao/skills/wechat-control/scripts/wx-send.py auto-reply config add-keyword "在吗"
python3 ~/Doubao/skills/wechat-control/scripts/wx-send.py auto-reply config add-keyword "谢谢"

# 配置指定联系人/群（可选，不配置则只按关键词触发）
python3 ~/Doubao/skills/wechat-control/scripts/wx-send.py auto-reply config add-contact "文件传输助手"

# 启动自动回复（前台运行，Ctrl+C停止）
python3 ~/Doubao/skills/wechat-control/scripts/wx-send.py auto-reply run --interval 60

# 查看状态
python3 ~/Doubao/skills/wechat-control/scripts/wx-send.py auto-reply status

# 查看日志
python3 ~/Doubao/skills/wechat-control/scripts/wx-send.py auto-reply log --limit 20
```

**主循环流程**：
1. 轮询 `wx new-messages` 获取新消息（默认60秒间隔）
2. 消息ID去重（已回复的不再回复）
3. 触发判定：指定联系人/群 或 关键词匹配
4. 频率限制检查（每分钟≤3、每小时≤20、同一对象5分钟≤2）
5. 夜间模式（23:00–08:00自动暂停）
6. 生成回复（当前为模板回复，后续可接入AI生成）
7. 回复内容黑名单检查
8. 调用 `send_message.sh` 发送
9. 记录日志 + 更新频率计数

**当前限制**：
- 回复生成使用简单模板，非AI生成（后续可接入豆包API）
- 仅支持文字消息回复
- 需前台运行，未做daemon化
- 定位为探索性质，不建议用于重要场景

## 11. 常见问题

### Q1: wx init 提示需要重签微信，要重签吗？
**不要重签。** 那是 wx-cli 的默认文案，假设用户用重签方式。关 SIP + sudo 即可不重签提取密钥。重签会改变微信 code identity，可能触发风控。

### Q2: 密钥提取成功，但 wx sessions 报错"无法解密 session.db"？
可能是密钥与当前数据目录不匹配。确认：
1. 微信当前登录的是目标账号
2. `~/.wx-cli/config.json` 里的 db_dir 指向正确的账号数据目录
3. 用 `sudo wx init --force` 重新提取

### Q3: 搜索返回 0 条结果？
可能原因：
- 关键词只出现在合并聊天记录或公众号标题中，不被全文索引
- 时间窗口限制，加 `--since 2026-01-01` 扩大范围
- 消息类型不匹配，加 `--type text` 或去掉类型过滤
- 换个更短/更常见的关键词试试

### Q4: 群名显示为一串 ID（如 xxxxxxxxxx@chatroom）？
这是正常的，群名称尚未同步到本地数据库。用该 ID 同样可以查询历史、成员、统计。在微信里打开该群聊一次，群名称可能会同步。

### Q5: 可以用大号吗？
可以，但建议先用小号跑通所有功能。大号读取同样安全（纯本地文件操作），但大号更重要，建议等功能稳定、错误处理完善后再用。切换大号后需重新 `sudo wx init` 提取大号密钥。

### Q6: 微信自动升级了怎么办？
如果升级到 4.1.10+，wx-cli 可能无法提取密钥。解决：
1. 降级回 4.1.8（从腾讯官方下载旧版安装包）
2. 或等待 wx-cli 新版本支持更高微信版本
3. 降级前备份微信数据

### Q7: 读取微信数据会被微信官方发现吗？
不会。wx-cli 读取的是微信本地的数据库文件（SQLite + SQLCipher），不碰微信进程、不联网、不操作微信界面。微信完全感知不到你在读它的数据库文件。密钥提取时短暂附加进程，但关 SIP + sudo + 不重签的方式是安全的，且只在提取密钥时发生一次。

### Q8: Windows 上能用吗？
wx-cli 官方支持 Windows（有 win32-x64 预编译二进制和 PowerShell 安装脚本），加密原理和密钥格式三平台通用。但**本技能的 Windows 部分尚未实测**，基于官方文档和原理编写。Windows 上使用要点：
- 以**管理员身份**运行 PowerShell（读取 Weixin.exe 进程内存需要）
- 不需要关 SIP（Windows 无此机制）
- 微信版本同样需 ~4.1.8（4.1.10+ 提取可能失败）
- 数据目录在"文档\WeChat Files\<wxid>\"（微信设置→文件管理中查看）
- 首次使用建议先在微信小号上验证，确认密钥提取和数据读取正常后再用于正式环境

## 12. 与其他技能的关系

| 技能 | 关系 | 说明 |
|------|------|------|
| **mac-system-toolkit** | 互补 | 提供 macOS 界面自动化能力（cu/AppleScript，§10 发消息的 UI 操作即建立其上）与代理开关能力（安装 wx-cli/下载安装包遇网络问题时使用）；本技能主体只读本地数据 |
| **lark-doc / lark-wiki** | 下游 | 第二阶段可把 AI 总结结果同步到飞书文档/知识库 |
| **doubao-cron-scheduler** | 下游 | 第二阶段做主动监控推送时，用定时任务定期调用 wx-cli 并推送总结 |

## 13. 项目信息

- **工具**：jackwener/wx-cli
- **GitHub**：https://github.com/jackwener/wx-cli
- **当前版本**：0.3.0
- **npm 包**：@jackwener/wx-cli
- **支持平台**：macOS (Intel/Apple Silicon) / Windows / Linux
- **支持微信版本**：~4.1.8（4.1.10+ 密钥提取可能失败）
- **配置目录**：
  - macOS/Linux：`~/.wx-cli/`（config.json + all_keys.json）
  - Windows：`%USERPROFILE%\.wx-cli\`
- **微信数据目录**：
  - macOS：`~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>/db_storage/`
  - Windows：`文档\WeChat Files\<wxid>\`（具体路径在微信设置→文件管理中查看；数据库文件通常在 Msg 或 db_storage 子目录下）
- **微信进程名**：
  - macOS：`WeChat`
  - Windows：`Weixin.exe`

### 13.1 平台差异速查表

| 维度 | macOS | Windows（未实测） | Linux（未实测） |
|------|-------|-------------------|----------------|
| 安装方式 | npm / install.sh | npm / install.ps1（管理员 PowerShell） | npm / install.sh |
| 密钥提取权限 | 关 SIP + sudo | 管理员身份运行 PowerShell | root 或 CAP_SYS_PTRACE |
| 是否需要关 SIP | ✅ 需要 | ❌ 无此机制 | ❌ 无此机制 |
| 微信进程名 | WeChat | Weixin.exe | wechat |
| 配置目录 | `~/.wx-cli/` | `%USERPROFILE%\.wx-cli\` | `~/.wx-cli/` |
| 数据目录 | `~/Library/Containers/.../xwechat_files/<wxid>/db_storage/` | `文档\WeChat Files\<wxid>\` | 依发行版而定 |
| 加密算法 | SQLCipher 4 (AES-256-CBC + HMAC-SHA512) | 完全相同 | 完全相同 |
| 密钥内存格式 | `x'<64hex_enc_key><32hex_salt>'` | 完全相同 | 完全相同 |
| 版本限制 | ~4.1.8 | 同样 ~4.1.8 | 同样 ~4.1.8 |
| 风控等级 | 只读零风险 | 同样只读零风险 | 同样只读零风险 |
| 实测状态 | ✅ 已实测通过（Intel Mac + 微信 4.1.8） | ⚠️ 未实测，基于官方文档和原理推断 | ⚠️ 未实测 |

> **Windows/Linux 使用前注意**：本技能的 macOS 路径和命令已实测通过；Windows/Linux 部分基于 wx-cli 官方文档、npm 包结构和微信三平台通用的加密原理编写，**尚未在实际 Windows/Linux 环境中验证**。首次在 Windows/Linux 上使用时，建议先在测试环境（微信小号）验证密钥提取和数据读取，确认无误后再用于正式环境。如遇问题，参考 wx-cli GitHub Issues 或官方文档。

---


## 附录：新微信号登录 / 多账号 / daemon 管理 SOP（按需阅读）

登记新微信号、大小号多开共存与切换、新号密钥/语音/联系人/监控/写操作链路验证、daemon 启停与排障，属于**低频操作手册**，已整体下沉到 [references/new-account-sop.md](references/new-account-sop.md)；需要时再读，日常只读查询不必加载。
