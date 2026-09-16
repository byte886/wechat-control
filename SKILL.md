---
name: wechat-control
description: "微信本地数据只读查询与 AI 总结技能。基于 wx-cli 解密本机微信 SQLite，支持会话列表、聊天历史、全文搜索、联系人、群成员、未读、统计、朋友圈、公众号、附件解密导出与语音转写；触发如'XX群最近聊了什么''搜关于XX的消息''有哪些未读''XX群有哪些人'。只读本地零风控；发消息/自动回复等写操作仅限小号且逐次明确确认，禁止大号及加删好友、朋友圈/公众号发布。"
compatibility: "macOS (Intel/Apple Silicon) / Windows / Linux。微信版本锁定 ~4.1.8（wx-cli 硬限制，4.1.10+ 密钥存储格式变更会导致提取失败）。密钥提取需关 SIP（macOS）或管理员权限（Windows）；Windows/Linux 未实测。"
---

# wechat-control · 微信本地数据读取与 AI 总结

> 基于 wx-cli 解密本机微信数据库做只读查询与 AI 总结；写操作严格隔离、仅限小号。

## 0. 文件地图与加载边界（先读）

- **运行面（用本技能只加载这些）**：本文件 ＋ 按需读 `references/` ＋ `scripts/`。`third-party/wx-cli/` 是本人 fork 的多账号改造版（git 子模块），`tools/silk-v3-decoder/` 是 SILK 语音解码。
- **`docs/` 是开发面**（PRD、路线图、阶段 spec、监控规划、数据字典），运行时**不要加载**。
- **公开三归宿**（本仓 public，push 即对全世界可见，含全部历史）：能脱敏成通用内容 → 明文随仓；无法脱敏但需保留 → 加密 `.enc` 随仓；无价值 → 删除，**不挪到仓外**。数据字典已剥离账号 wxid/昵称、`Msg_<MD5>` 分表清单、记录条数与库大小，只留表结构明文随仓；聊天正文绝不入库。
- **路径约定**：`scripts/...` 相对技能根目录；他处执行替换为 `~/Doubao/skills/wechat-control/scripts/`（`~` 双机自适应，不写死用户名）。

## 1. 什么时候用 / 不用

自然语言问到微信就用：「XX群/XX人最近聊了什么」「帮我搜关于 XX 的消息」「有哪些未读」「XX群有哪些人/活跃度怎样/发了哪些图片文件」「朋友圈/公众号最近有什么」。
**反触发**：与微信本地数据无关的任务不读本技能。

## 2. 硬红线（每次任务适用）

1. **只读零风控**：sessions/history/search/监控读取/语音转写只读本地库，不碰微信进程、不联网、不操作界面。
2. **写操作仅限小号（alt）**，大号（main）禁写；任何发送前必须用户明确说"发送"（"好/对/嗯"不算）；频率每分钟 ≤3、每小时 ≤20、同一对象 5 分钟 ≤2、23:00–08:00 暂停；命中 10 类黑名单（金钱/隐私/承诺/敏感/辱骂/机密/专业建议/身份冒充/暴露 AI/反问质疑）不发；发送前 OCR 校验聊天标题。
3. **绝不**加好友、删好友、发朋友圈、公众号发布。
4. **密钥提取**：关 SIP ＋ sudo ＋ **不重签微信**（绝不 `codesign --force --deep --sign -`）、不用 Frida/lldb 持续注入（重签＋注入曾触发风控）；提取是短暂操作、用完即释放。
5. **微信锁版本 ~4.1.8**：4.1.10+ 扫不到密钥；关闭自动更新、不主动升级。
6. 密钥同账号、同版本、不重装时**一次提取永久有效**；仅升级/切号/重装后重提。

**账号角色**（真实昵称/wxid 不入仓，用 `bash scripts/wx-account.sh list` 现查）：

| 角色 | 配置名 | wxid（数据目录名） | 用途 |
|---|---|---|---|
| 大号/主号 | main | `〈主号wxid，形如 wxid_xxxxxxxx_yyyy〉` | 日常，只读，禁写 |
| 小号/自动化 | alt | `〈小号wxid〉` | 写测试、自动回复、UI 自动化 |

## 3. 快速上手与命令速查

安装/密钥提取/平台差异 → [install.md](references/install.md)；多账号切换 → [multi-account.md](references/multi-account.md)。

| 目的 | 命令 |
|---|---|
| 会话列表 | `wx sessions [--limit N]` |
| 聊天记录 | `wx history "<名>" [--since D] [--until D] [--limit N] [--json]` |
| 全文搜索 | `wx search "<词>" [--in "<群>"] [--since D] [--type text]` |
| 未读 | `wx unread` |
| 联系人 / 群成员 | `wx contacts` / `wx members "<群>"` |
| 统计（全量，不支持时间范围） | `wx stats "<群>"` |
| 增量新消息 | `wx new-messages` |
| 附件列出 / 解密导出 | `wx attachments "<群>"` / `wx extract <id> --output DIR` |
| 导出记录 | `wx export "<群>" --output f.txt` |
| 朋友圈 / 公众号 / 收藏 | `wx sns-feed`·`sns-search`·`sns-notifications` / `wx biz-articles` / `wx favorites` |
| daemon | `wx daemon status` / `wx daemon stop` |

默认 YAML，`--json` 出 JSON；默认查最近 20 条，大群必加 `--limit`（单批 ≤200）。完整参数、输出字段与示例见 [commands.md](references/commands.md)。

## 4. 自然语言 → 命令

| 用户意图 | 命令 |
|---|---|
| 查未读 / 谁发消息了 | `wx unread` |
| 某群/某人最近聊啥 | `wx history "<名>" --limit 20` |
| 搜消息 / 有人提过 X 吗 | `wx search "<词>"`（限定群加 `--in`） |
| 群成员 | `wx members "<群>"` |
| 活跃度 | `wx stats "<群>"` |
| 联系人 | `wx contacts` |
| 图片/文件 | `wx attachments "<名>"` |
| 朋友圈 / 公众号 | `wx sns-*` / `wx biz-articles` |

- 群名/人名有多个匹配 → **先 `wx sessions` 列候选反问，禁止猜测**，流程见 [ambiguity-handling.md](references/ambiguity-handling.md)。
- 时间词（昨天/最近一周/上月）解析见 [time-parsing.md](references/time-parsing.md)；未说时间默认最近 20 条并说明。
- 几百上千条 → 先 `wx stats` 概览、分批或搜索定位，不做无限制全量，见 [large-message-handling.md](references/large-message-handling.md)。
- 总结版式（简短/详细/未读分组、@我·决策·紧急标注）见 [summary-templates.md](references/summary-templates.md)。

## 5. 按需加载索引（不要一次全读）

| 你要做什么 | 读这篇 |
|---|---|
| 安装/卸载、首次密钥提取、失败对照、平台差异、项目信息 | [install.md](references/install.md) |
| 登记一个新微信号的完整 SOP 与完成清单 | [new-account-sop.md](references/new-account-sop.md) |
| 主号/小号切换、为何不能多开、多账号源码改造、目录冲突 | [multi-account.md](references/multi-account.md) |
| 全部查询命令的参数与输出字段 | [commands.md](references/commands.md) |
| 查询记忆、多群对比、导出、语音/视频/位置/撤回消息呈现 | [advanced-usage.md](references/advanced-usage.md) |
| 语音转写、media_0.db / VoiceInfo、发言人识别 | [voice-pipeline.md](references/voice-pipeline.md) |
| 每日总结、推荐监控群、重要消息判定（wx-monitor） | [monitoring.md](references/monitoring.md) |
| 实时监听 realtime-monitor、daemon 机制与重启 | [realtime-and-daemon.md](references/realtime-and-daemon.md) |
| 报错翻译与处理（E1–E10） | [error-handling.md](references/error-handling.md) |
| 多名匹配 / 歧义确认 | [ambiguity-handling.md](references/ambiguity-handling.md) |
| 大消息量分层、分批与摘要策略 | [large-message-handling.md](references/large-message-handling.md) |
| 自然语言时间解析 | [time-parsing.md](references/time-parsing.md) |
| 总结模板 | [summary-templates.md](references/summary-templates.md) |

## 6. 写操作（探索性质，严格隔离）

只用小号、逐次确认、频率与黑名单限制、发送前 OCR 校验（红线见 §2）。
- UI 发送：`bash scripts/wechat-ui/send_message.sh "<对象>" "<内容>" [--no-verify]`，采用 Cmd+F 搜索＋回车方案（纯键盘、不依赖坐标）；7 步流程与健壮性见开发面 `docs/PRD.md` 附录 C。
- 自动回复框架：`python3 scripts/wx-send.py auto-reply run --interval 60`（探索性质、模板回复、仅文字、需前台运行）。
- 新号写链路验证见 [new-account-sop.md](references/new-account-sop.md) §A.10。

## 7. 与其他能力的关系

- macOS 界面自动化（发消息 UI）、代理开关（下载遇网络问题）：用 mac-system-toolkit 能力；本技能主体只读本地数据。
- 总结同步飞书、定时主动监控推送：分别用飞书文档/知识库能力、定时任务能力（飞书 sync 在脚本中尚未接线）。
- 凭证/口令来处与公开仓防泄漏：遵循安全基线能力，不把口令/token 写进脚本或仓库。

## 8. 版本与平台

工具 jackwener/wx-cli v0.3.0（npm `@jackwener/wx-cli`），微信锁 ~4.1.8；macOS（Intel）＋微信 4.1.8 已实测，Windows/Linux 未实测（首次先在小号验证）。详见 [install.md](references/install.md)。
