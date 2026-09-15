# 语音转写链路（wechat-control 参考）

> 需要把微信语音消息转文字、排查语音取不到/发言人错乱、或验证 media_0.db 存储时读本文。

## 1. 两个存储位置（2026-09-11 大号验证）

**主要来源：`media_0.db` 的 VoiceInfo 表**
- 路径：`db_storage/message/media_0.db`
- 表结构：`VoiceInfo(chat_name_id, create_time, local_id, svr_id, voice_data BLOB, data_index)`
- `voice_data` 直接存 SILK V3 语音 BLOB（前面多 1 字节 `0x02` 头）
- **不需手动播放**，语音自动下载到 media_0.db
- `Name2Id` 表：rowid → user_name(wxid)，用于映射 chat_name_id

**备用/历史：全局缓存目录**
```
~/Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat/2.0b4.0.9/<hash>/Message/MessageTemp/<chat_hash>/Audio/*.aud.silk
```
`.aud.silk`（同样多 1 字节 0x02 头），全局共享、不按账号分，主要是 3.x 迁移的历史语音；新语音优先在 media_0.db。

## 2. 关键坑（必看）

| 问题 | 说明 | 解决 |
|------|------|------|
| **local_id 不全局唯一** | VoiceInfo 的 local_id 每会话自增，不同会话可能相同 | 必须同时用 `chat_name_id + local_id` 定位，否则取错语音 |
| **新消息有延迟** | 收到后约 5–10 秒才写库（WAL） | 轮询等待，或检查 WAL 修改时间 |
| **download_status 含义** | 语音 =1 表示已下载（不是 5）；文字 =0 | 不要用 =5 判断语音 |
| **语音消息类型** | local_type = **34**（不是 3） | 查询用 `local_type = 34` |
| **会话表名映射** | 表名 `Msg_<MD5(短wxid)>`，短 wxid 去掉 `_yyyy` 后缀 | 占位示例：`wxid_xxxxxxxx_yyyy` → 短 `wxid_xxxxxxxx` → `Msg_<MD5>` |

## 3. 发送者识别链路

- **私聊**：`VoiceInfo.chat_name_id` → `Name2Id.user_name`(wxid) → `contact.db` contact 表查昵称
- **群聊**：`chat_name_id` 是群 ID，具体发言人需 join `message_0.db` 会话表的 `real_sender_id` → `Name2Id` 映射
- **新语音通知**：轮询 VoiceInfo 表新增 `create_time` 即可发现

## 4. 转写脚本与验证

- 批量转历史语音：`python3 scripts/voice-transcribe.py transcribe --limit 2`（SILK 解码 + FunASR SenseVoiceSmall 本地离线转写）
- 列出最近语音：`python3 scripts/voice-transcribe.py list --limit 5`
- 新语音实时转写：`python3 scripts/voice-monitor.py monitor`（来一条转一条，直查 media_0.db）
- 直查库验证数据存在：
```bash
sqlcipher .../xwechat_files/<wxid>/db_storage/message/media_0.db << EOF
PRAGMA key="x'<media_key>'";
SELECT chat_name_id, datetime(create_time,'unixepoch','localtime'), local_id, length(voice_data)
  FROM VoiceInfo ORDER BY create_time DESC LIMIT 5;
EOF
```

注意：media_0.db 密钥需 `wx init --force` 提取（微信运行中即可，不需播放语音）；新账号无语音消息时本节可跳过。
