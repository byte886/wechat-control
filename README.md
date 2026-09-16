# WeChat Control — 微信自动化控制工具包

> 基于 [jackwener/wx-cli](https://github.com/jackwener/wx-cli) 的微信自动化能力，提供只读查询、实时监控、语音转写、智能回复等功能。

## ✨ 特性

### 📖 只读查询
- **会话列表**：查看所有聊天会话及未读消息
- **历史消息**：读取任意联系人/群聊的历史消息
- **联系人信息**：微信昵称、备注名、微信号、头像
- **消息搜索**：全库搜索关键词
- **统计信息**：消息数量、类型分布

### 🎙️ 语音转写
- **自动转写**：微信语音消息自动转文字（基于 FunASR SenseVoiceSmall）
- **多人识别**：自动识别语音发送者（昵称/备注名/微信号）
- **实时监控**：新语音消息实时检测并转写
- **无需播放**：语音数据直接从数据库读取，无需手动点击播放

### 🔔 消息监控
- **全类型支持**：33种消息类型识别（文本/图片/语音/视频/文件/红包/转账等）
- **群聊监控**：监控指定群聊，关键词触发、@我/@all 检测
- **重要消息判定**：高/中/低三级优先级，自动过滤不重要消息
- **多渠道推送**：控制台、日志、飞书、微信、邮件、Webhook（规划中）
- **每日总结**：自动生成每日消息摘要

### ✍️ 写操作（需严格风控）
- **发送消息**：给指定联系人/群聊发文字消息
- **自动回复**：根据关键词自动回复（仅小号测试）
- **风控保护**：频率限制、夜间静默、黑名单、发送前确认

## 📋 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | macOS 14+（已验证 15.7.8） |
| 微信版本 | 4.1.8（硬性限制，4.1.10+ 密钥提取可能失败） |
| SIP | 必须关闭（密钥提取需要） |
| 架构 | x86_64 / arm64（均支持） |
| 依赖 | wx-cli、SQLCipher、Python 3.9+、FunASR（语音转写） |

## 🚀 快速开始

### 1. 安装依赖

```bash
# 安装 wx-cli（npm 优先；官方一键 install.sh 曾出现 404，不推荐）
npm install -g @jackwener/wx-cli
wx --version            # 应输出 0.3.0 或更高
# 注：npm 官方版仅支持单账号只读；需要主号/小号多账号切换，须用
# third-party/wx-cli 这个 fork 从源码编译，见 references/install.md 与 references/multi-account.md

# 安装 SQLCipher
brew install sqlcipher

# 安装 Python 依赖（仅语音转写需要，torch 体积较大，用到时再装）
pip3 install funasr torch
```

### 2. 关闭 SIP（macOS）

> 密钥提取需要关闭 SIP。重启进入恢复模式，运行 `csrutil disable`。

### 3. 提取数据库密钥

```bash
# 确保微信已登录并运行
sudo wx init --force

# 验证密钥
wx sessions --limit 5
```

### 4. 使用功能

```bash
# 查看会话列表
wx sessions --limit 10

# 查看未读消息
wx unread

# 语音转写（最近10条）
python3 scripts/voice-transcribe.py transcribe --limit 10

# 语音实时监控
python3 scripts/voice-monitor.py monitor --interval 30

# 消息类型统计
python3 scripts/message-collector.py list-types

# 发送消息（仅小号测试）
bash scripts/wechat-ui/send_message.sh "文件传输助手" "测试消息"
```

## 📁 项目结构

```
wechat-control/
├── SKILL.md                    # AI Agent 技能文档（主文档）
├── README.md                   # 项目介绍（本文件）
├── docs/                       # 开发面（AI 运行时不加载）：脱敏设计文档随公开仓
│   ├── PRD.md                  # 产品需求文档（含附录 A-D：各阶段 spec、监控/写操作技术规格、治理规范）
│   ├── ROADMAP.md              # 路线图与当前状态看板
│   └── database-schema.md      # 微信库表结构字典（只留结构、已脱敏），明文随仓
# 注：含个人数据的文件三种归宿——脱敏后明文随仓 / 无法脱敏则加密 .enc 随仓 / 无价值删除，
#     不挪到仓库外；数据字典已剥离账号、会话分表清单、条数/大小；实测日志 test-cases.md 已删。
├── scripts/
│   ├── wx-monitor.py           # 群监控 + 每日总结/推荐群/飞书同步
│   ├── realtime-monitor.py     # 新消息实时监听（daemon 自愈、重要性判定）
│   ├── message-collector.py    # 直读库的统一消息采集（33 种类型）
│   ├── voice-monitor.py        # 语音实时监控 + 转写
│   ├── voice-transcribe.py     # 语音批量转文字（SILK + FunASR）
│   ├── wx-send.py              # 写操作（发送/自动回复，仅小号）
│   ├── wx-account.sh           # 多账号切换（单 App 切换方案）
│   └── wechat-ui/              # 发送消息的 UI 自动化 shell
├── mcp-server/                 # FastMCP 监控服务（STDIO/SSE，详见其内 README）
├── tools/
│   └── silk-v3-decoder/        # 第三方 SILK 语音解码工具
├── references/                 # 操作参考（错误/歧义/时间/大消息量/新号SOP）
└── third-party/                # 本人 fork 的多账号改造版 wx-cli（git 子模块；单账号只读可用 npm 官方版）
```

> 本仓为 **public 公开仓**，入仓内容（含全部历史）一旦 push 即对全世界可见，判据是"内容能否公开"，与它是 SKILL、脚本还是 `docs/` 无关。含个人数据的文件只有三种归宿：脱敏成通用内容后明文随仓 / 无法脱敏则加密为 `.enc` 随仓（无主口令解不开）/ 无价值删除，**不挪到仓库外**。`docs/` 下 PRD（含附录）与 ROADMAP 是通用设计文档，随仓公开但 **AI 运行时不加载**；数据字典 `docs/database-schema.md` 只保留库/表/字段结构（已剥离账号、`Msg_<MD5>` 会话分表清单、记录条数、库大小），明文随仓；本人实测日志不保留。加载边界见 [SKILL.md](SKILL.md) §0。

## 📊 支持的消息类型

| 类型 | local_type | 读取 | 解析 | 监控 |
|------|-----------|------|------|------|
| 文本 | 1 | ✅ | ✅ | ✅ |
| 图片 | 3 | ✅ | 🔲 | ✅ |
| 语音 | 34 | ✅ | ✅（转写） | ✅ |
| 视频 | 43 | ✅ | 🔲 | ✅ |
| 表情 | 47 | ✅ | 🔲 | ✅ |
| 文件 | 8589934592049 | ✅ | 🔲 | ✅ |
| 位置 | 244813135921 | ✅ | 🔲 | ✅ |
| 名片 | 25769803825 | ✅ | 🔲 | ✅ |
| 红包 | 21474836529 | ✅ | 🔲 | ✅ |
| 转账 | 141733920817 | ✅ | 🔲 | ✅ |
| 合并聊天记录 | 81604378673 | ✅ | 🔲 | ✅ |
| 系统消息 | 10000 | ✅ | 🔲 | ✅ |
| ...其他 | ... | ✅ | 🔲 | ✅ |

> ✅ = 已支持，🔲 = 规划中

## ⚠️ 风控与安全

### 只读操作（安全）
- 所有查询、监控、转写均为**只读**，不修改微信数据库
- 不注入、不 hook、不修改微信进程
- 仅通过 SQLCipher 读取数据库文件
- 风险等级：低（与手动查看聊天记录相当）

### 写操作（需严格风控）
- 频率限制：每分钟≤3、每小时≤20、同一对象5分钟≤2
- 夜间静默：23:00-08:00 暂停
- 黑名单：10类敏感内容不发
- 发送前验证：OCR 确认聊天对象
- **建议：仅在小号测试，大号谨慎使用**

## 📚 文档

**运行面（AI 加载）**
- [SKILL.md](SKILL.md) — 技能主入口与路由器（AI Agent 必读，含文件地图/加载边界 §0、硬红线、命令速查、按需加载索引 §5）
- `references/` — 按需加载的操作参考：安装与平台、命令详解、新号登记、多账号、语音链路、监控、实时监听与 daemon、错误/歧义/时间/大消息量/总结模板（**完整索引见 SKILL.md §5**）
- [mcp-server/README.md](mcp-server/README.md) — 可选 MCP 监控服务

**设计/规划文档（已脱敏、随公开仓；AI 运行时不加载，位于 `docs/`）**
- [PRD.md](docs/PRD.md) — 产品需求文档（正文：七模块需求与验收；附录 A-D：第一阶段决策、监控技术规格、写操作技术规格、需求澄清与治理规范）
- [ROADMAP.md](docs/ROADMAP.md) — 开发路线图与「当前状态」看板
- `database-schema.md`：微信本地数据库的**表结构字典**（明文随仓，只含库/表/字段，已剔除账号 wxid/昵称、具体会话分表清单、记录条数、库大小等个人/统计数据，不含消息正文）

## 🛠️ 技术栈

| 层 | 技术 | 职责 |
|----|------|------|
| 底层 | Rust (wx-cli) | 密钥提取、数据库解密、基础查询 |
| 上层 | Python | 语音转写、监控逻辑、UI自动化、推送 |
| 数据库 | SQLCipher 4.x | 微信数据库加密格式 |
| 语音 | FunASR SenseVoiceSmall | 语音转文字（本地运行，不上传） |
| UI | AppleScript / cliclick | 微信界面自动化（发送消息） |

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License

## ⚠️ 免责声明

本项目仅供学习和研究使用。使用本工具需遵守微信的使用条款和相关法律法规。作者不对使用本工具造成的任何后果负责。

---

**微信版本升级提醒**：本工具硬绑定微信 4.1.8。升级微信后，数据库结构和密钥提取方式可能变化，需重新验证。低频操作分篇见 references/：新号登记看 new-account-sop.md、多账号切换看 multi-account.md、实时监听与 daemon 看 realtime-and-daemon.md（完整索引见 SKILL.md §5）。
