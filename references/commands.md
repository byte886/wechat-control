# wx-cli 命令详解（wechat-control 参考）

> 只读查询命令的完整用法、参数与输出字段。命令选择速查见 SKILL.md；自然语言到命令的映射也在 SKILL.md。
> 所有命令默认输出 YAML，加 `--json` 输出 JSON；输出较大时用 `--limit N` 限制。CHAT 可以是群名、人名、username 或 chatroom ID。

## sessions · 会话列表
列出最近会话（群聊、私聊、公众号、品牌服务、折叠等）。
```bash
wx sessions                 # 所有会话
wx sessions --limit 20      # 前 20 个
```
字段：`chat`（名称）、`chat_type`（group/private/official_account/folded）、`is_group`、`last_msg_type`、`summary`、`time`、`unread`、`username`。

## history · 聊天记录
```bash
wx history "产品群"                    # 默认最近 20 条
wx history "张三" --limit 50
wx history "产品群" --since 2026-09-01
wx history "产品群" --since 2026-09-01 --until 2026-09-30 --limit 200
wx history "产品群" --json
```
字段：`content`、`sender`、`time`、`timestamp`、`type`（文本/图片/语音/视频/链接/文件/系统等）、`local_id`。
> 群名可能显示为 `xxxxxxxxxx@chatroom`（群名称尚未同步到本地），用该 ID 同样可查。大消息量分批策略见 [large-message-handling.md](large-message-handling.md)，时间词解析见 [time-parsing.md](time-parsing.md)。

## search · 全文搜索
```bash
wx search "预算"                       # 搜所有聊天
wx search "预算" --in "产品群"         # 限定会话
wx search "预算" --since 2026-01-01    # 限定时间
wx search "预算" --type text           # 只搜文本
wx search "预算" --limit 10
```
> 合并聊天记录内容、公众号推送标题/摘要可能不被全文索引；为空时换关键词或扩大时间范围。

## unread · 未读消息
```bash
wx unread                # 列出有未读的会话（实时状态，不支持 --since/--until）
```

## contacts · 联系人
```bash
wx contacts --limit 20
```

## members · 群成员
```bash
wx members "产品群"
```
字段：`display`（显示名）、`contact_display`、`group_nickname`（群昵称）、`is_owner`（是否群主）、`username`。

## stats · 统计分析
按小时、消息类型等统计指定会话（全量数据，不支持时间范围参数）。
```bash
wx stats "产品群"
```

## new-messages · 增量新消息
获取自上次检查以来的新消息。
```bash
wx new-messages
```

## attachments / extract · 附件列出与解密导出
```bash
wx attachments "产品群"                          # 列出图片/文件附件，返回 attachment_id
wx extract <attachment_id> --output ~/Desktop/  # 解密导出到目录
```
> 目前 attachments 只列图片等，视频暂不支持解密导出。

## export · 导出聊天记录
导出为 Markdown/文本文件（纯文本，不含图片/视频附件）。
```bash
wx export "产品群" --output ~/Desktop/产品群.txt
wx export "产品群" --since 2026-09-01 --until 2026-09-10 --output ~/Desktop/周.txt
```
大群导出前先 `wx stats` 评估，超 1000 条建议限定时间范围。

## 朋友圈
```bash
wx sns-feed                 # 朋友圈时间线（本地缓存）
wx sns-search "关键词"       # 朋友圈全文搜索
wx sns-notifications        # 朋友圈互动通知（点赞/评论）
```

## 公众号文章
```bash
wx biz-articles             # 公众号推送（本地缓存）
```

## 收藏
```bash
wx favorites
```

## daemon · 后台守护进程
wx-cli 自带 daemon，缓存解密数据库以加速重复查询，通常自动管理。
```bash
wx daemon status
wx daemon stop
```
> 切换账号、改 config.json 的 db_dir、更新 wx 二进制、密钥失效后需重启 daemon；日常收新消息无需重启。完整机制与重启步骤见 [realtime-and-daemon.md](realtime-and-daemon.md)。
