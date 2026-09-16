# AGENTS.md — wechat-control AI 操作手册

> 读者：接手本项目的 AI 代理。**每次开工先读本文件**，命令式、可执行。
> 与 README.md（给人看的项目介绍）互补。本文件只放"AI 无法从代码推断、且跨会话必须稳定"的规则；能在别处查到的只给链接、不复制（一处权威，避免双写）。

## 1. 这是什么

微信本地数据**只读**查询 / 语音转写 / 监控总结的个人技能：用 wx-cli 解密本机微信 SQLite，做自然语言查询与每日总结；写操作（发消息/自动回复）严格隔离、仅小号。本仓是 **public 仓**，push 即对全世界可见（含全部历史）。

## 2. 接手路径（先判断走哪条，不靠对话记忆猜）

### 路径 A · 冷启动（首次接触 / 不知道当前到哪 / 跨阶段 / 没把握）
按序读：
1. `SKILL.md` —— 运行面入口：§0 文件地图与加载边界、**§2 硬红线**、§3 命令速查、§5 references 索引、账号角色。
2. `docs/ROADMAP.md` —— 顶部「📍 当前状态（单一进度真相）」：现在到哪 / 下一步 / 阻塞。
3. `docs/project-initiation/00_项目总纲.md` —— 目标、可量化成功标准、里程碑、治理卡。
4. 按需：需求与验收读 `docs/PRD.md`；运行机制读 `references/` 对应篇（索引在 SKILL §5）。

### 路径 B · 续接（用户说"继续 / 接着做 X"，仍是同一类活）
只读：
1. `docs/ROADMAP.md` 顶部「📍 当前状态」（进行中 / 下一步 / 阻塞）。
2. 本次要碰的那一篇 `references/` 或脚本，以及上一次的成品。

不全文重读 PRD / 总纲；**判不准走 A 还是 B 时，就高走 A。**

> 最简交接：把本项目文件夹拖入新对话，说"按 AGENTS.md 续接路径接手继续"，无需长提示词。

## 3. 加载边界与红线（权威在 SKILL.md，此处只给指针）

- **运行面 vs 开发面**：用本技能干活只加载 `SKILL.md` + 按需 `references/` + `scripts/`；`docs/` 是开发面，运行时不加载（见 SKILL §0）。
- **硬红线以 `SKILL.md §2` 为唯一权威**，要点（不展开、不复制阈值）：只读零风控；写操作仅小号 `alt` 且逐次明确确认（大号 `main` 禁写）；微信锁版本 ~4.1.8 不升级；密钥提取关 SIP、不重签、不持续注入。
- 账号身份用 `bash scripts/wx-account.sh current` 现查；密钥从 `~/.wx-cli/all_keys.json` 现读；**不硬编码、不入仓**。
- `wx new-messages` 的增量游标外部禁止手动跑（会与 MCP server 抢游标）。

## 4. Git 与 submodule 提交约定（重要，跨会话固定）

本目录 `~/Doubao/skills/wechat-control` 是上层仓 `~/Doubao`（GitHub `byte886/doubao-workspace`）的 **git submodule**；`third-party/wx-cli` 又是本仓的嵌套 submodule（本人 fork 的多账号改造版）。

改动 wechat-control 后，按"先内后外"两步提交：

```bash
# 1) 子模块内提交并推送（origin 走 SSH；HTTPS 需挂 VPN）
cd ~/Doubao/skills/wechat-control
git status                       # 核对暂存内容，确认无密钥/大文件/个人标识
git add <具体文件>               # 只加本次相关文件
git commit -m "..."
git push                         # main 已跟踪 origin/main

# 2) 回到上层仓，只 bump 本 submodule 指针
cd ~/Doubao
git add skills/wechat-control    # ★ 只加这一个
git status                       # ★ 再次确认暂存区只有 skills/wechat-control
git commit -m "chore: bump wechat-control -> <短sha>"
git push origin main             # ★ 上层 main 无 upstream，必须显式 origin main
```

- **绝不要 `git add skills/project-manager` 或上层其它目录**——那是他人 / 他任务的改动，不夹带。
- 改了嵌套的 `third-party/wx-cli`：先在该目录 commit+push 到本人 fork，再在本仓 bump 它的指针，最后再走上面两步。
- **公开三归宿**（push 即公开，含历史）：能脱敏成通用内容 → 明文随仓；无法脱敏但需留 → 加密 `.enc` 随仓；无价值 → 删除，不挪到仓外。密钥、真实 wxid/昵称/手机号、`Msg_<MD5>` 分表清单、库大小/条数、聊天正文一律不入仓。提交前用 `git grep -nE "1[3-9][0-9]{9}|wxid_[a-z0-9]{6,}"` 自查（注意排除 local_type 常量、lock 哈希、`wxid_xxx` 占位符与 wx-cli 测试夹具）。
- 大文件 / 过程产物 / 临时文件先看 `.gitignore`，新类型及时补 ignore。

## 5. 变更纪律（轻量）

- **L0 顺手修复**（错字、断链、单文件小错）：直接改并在提交说明里记一笔。
- **L1 高扩散**（批量重命名 / 移动、预计 ≥5 处级联、新增或删除"持久文档 / 机制"、修改本规范、重写 git 历史 / force push）：**先给方案 + 全量影响清单，用户确认后再动**。
- **新建持久文档前的门禁**：先逐一指认它要承担的职责是否已被现有文档承担（SKILL / ROADMAP / references / project-initiation）；已被承担就补到权威源，不新建，防重复建设。
- **单一进度真相**："现在做什么 / 下一步 / 阻塞"只维护在 `docs/ROADMAP.md` 顶部「当前状态」；易变值（进度、计数、当天 SHA、剩余量）只改那里，不抄进规范正文。
- **完成一项就回写状态并提交**，不留"标 todo 但实际已完成"的僵尸项；方案被取代当场标注"被 X 取代·关闭"。
- ADR（不可逆决策）只增不改；现行规范保持当前正确，过期内容直接修（历史在 git 可溯）。

## 6. 文档地图

| 层 | 位置 | 装什么 |
|---|---|---|
| 运行面 | `SKILL.md`、`references/`、`scripts/`、`mcp-server/` | AI 用技能时加载：入口、命令、SOP、转写/监控/发送脚本、MCP server |
| 开发面 | `docs/`（PRD 含附录 A-D、ROADMAP、database-schema） | 为什么这么设计、需求验收、路线、表结构字典 |
| 立项 | `docs/project-initiation/`（00 总纲 … 05 治理卡） | 回溯性立项六件套、阶段门 G1/G2/G3、工单切片 |
| 第三方 | `third-party/wx-cli/`（嵌套 submodule）、`tools/silk-v3-decoder/` | wx-cli 本人 fork、SILK 解码 |
| 本机私密（不入仓） | `~/.wx-cli/`（config、all_keys、accounts、`private/`） | 密钥、账号配置、飞书归档目标等个人标识 |
