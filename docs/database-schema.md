# 微信数据库 Schema 梳理

## 环境与版本信息（重要！升级微信后需重新验证）

| 项目 | 值 | 说明 |
|------|-----|------|
| **微信版本** | 4.1.8 (Build 37335) | CFBundleShortVersionString / CFBundleVersion |
| **客户端版本** | 4066646122 | 进程参数 client_version |
| **macOS 版本** | 15.7.8 (Build 24G824) | Sequoia |
| **内核版本** | 24.6.0 | Darwin |
| **硬件架构** | x86_64 (Intel) | 黑苹果 OpenCore |
| **SIP 状态** | disabled | 已关闭（密钥提取必需） |
| **微信 bundle id** | com.tencent.xinWeChat | 进程名 WeChat |
| **SQLCipher 版本** | 4.x | 数据库加密格式 |
| **测试账号** | 主号 main / 小号 alt；账号 wxid 等私人标识只存于本地 wx-cli 配置，不写入本文档 |

> ⚠️ **重要提醒**：微信数据库结构、表名、字段名、加密方式可能随版本变化。
> 升级微信后，必须重新运行 `wx init --force` 提取密钥，并验证本文档中的表结构是否仍然有效。
> 如果表结构变化，需要重新生成此文档并更新相关脚本。

---

## 数据库文件清单

| 数据库 | 说明 |
|--------|------|
| contact/contact.db | 联系人、群聊、群成员 |
| contact/contact_fts.db | 联系人全文索引 |
| session/session.db | 会话列表、未读消息 |
| message/message_0.db | 消息数据库0（主） |
| message/message_1.db | 消息数据库1（分片） |
| message/message_2.db | 消息数据库2（分片） |
| message/media_0.db | 媒体/语音数据库 |
| message/message_fts.db | 消息全文索引 |
| message/message_resource.db | 消息资源（图片/视频等） |
| message/biz_message_0.db | 业务消息0 |
| message/biz_message_1.db | 业务消息1 |
| message/biz_message_2.db | 业务消息2 |
| favorite/favorite.db | 收藏 |
| sns/sns.db | 朋友圈 |
| general/general.db | 通用设置 |
| head_image/head_image.db | 头像缓存 |
| emoticon/emoticon.db | 表情 |
| hardlink/hardlink.db | 硬链接 |
| solitaire/solitaire.db | 接龙 |
| bizchat/bizchat.db | 业务聊天 |
| chatbot/chatbot_message.db | 聊天机器人 |
| third_app_icon/third_app_icon.db | 第三方应用图标 |
| message/weclaw.db | 未知 |

---

## contact/contact.db（联系人数据库）

**共 16 张表**

### biz_info

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | 主键 |
| username | TEXT |  |
| type | INTEGER |  |
| accept_type | INTEGER |  |
| child_type | INTEGER |  |
| home_url | TEXT |  |
| version | INTEGER |  |
| external_info | TEXT |  |
| brand_info | TEXT |  |
| brand_icon_url | TEXT |  |
| brand_list | TEXT |  |
| brand_flag | INTEGER |  |
| belong | TEXT |  |
| ext_buffer | BLOB |  |
| sync_version | TEXT |  |

### chat_room

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | 主键 |
| username | TEXT |  |
| owner | TEXT |  |
| ext_buffer | BLOB |  |

### chat_room_info_detail

| 字段名 | 类型 | 说明 |
|--------|------|------|
| room_id_ | INTEGER | 主键 |
| username_ | TEXT |  |
| announcement_ | TEXT |  |
| announcement_editor_ | TEXT |  |
| announcement_publish_time_ | INTEGER |  |
| chat_room_status_ | INTEGER |  |
| xml_announcement_ | TEXT |  |
| ext_buffer_ | BLOB |  |

### chatroom_member

| 字段名 | 类型 | 说明 |
|--------|------|------|
| room_id | INTEGER |  |
| member_id | INTEGER |  |

### contact

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | 主键 |
| username | TEXT |  |
| local_type | INTEGER |  |
| alias | TEXT |  |
| encrypt_username | TEXT |  |
| flag | INTEGER |  |
| delete_flag | INTEGER |  |
| verify_flag | INTEGER |  |
| remark | TEXT |  |
| remark_quan_pin | TEXT |  |
| remark_pin_yin_initial | TEXT |  |
| nick_name | TEXT |  |
| pin_yin_initial | TEXT |  |
| quan_pin | TEXT |  |
| big_head_url | TEXT |  |
| small_head_url | TEXT |  |
| head_img_md5 | TEXT |  |
| chat_room_notify | INTEGER |  |
| is_in_chat_room | INTEGER |  |
| description | TEXT |  |
| extra_buffer | BLOB |  |
| chat_room_type | INTEGER |  |

### contact_label

| 字段名 | 类型 | 说明 |
|--------|------|------|
| label_id_ | INTEGER | 主键 |
| label_name_ | TEXT |  |
| sort_order_ | INTEGER |  |

### encrypt_name2id

| 字段名 | 类型 | 说明 |
|--------|------|------|
| username | TEXT | 主键 |

### name2id

| 字段名 | 类型 | 说明 |
|--------|------|------|
| username | TEXT | 主键 |

### openim_acct_type

| 字段名 | 类型 | 说明 |
|--------|------|------|
| lang_id | INTEGER |  |
| acc_type_id | TEXT | 主键 |
| update_time | INTEGER |  |
| ext_buffer | BLOB |  |

### openim_appid

| 字段名 | 类型 | 说明 |
|--------|------|------|
| lang_id | INTEGER |  |
| app_id | TEXT | 主键 |
| acct_type_id | TEXT |  |
| update_time | INTEGER |  |
| ext_buffer | BLOB |  |

### openim_wording

| 字段名 | 类型 | 说明 |
|--------|------|------|
| lang_id | INTEGER |  |
| app_id | TEXT | 主键 |
| wording_id | TEXT |  |
| wording | TEXT |  |
| pinyin | TEXT |  |
| quan_pin | TEXT |  |
| update_time | INTEGER |  |
| ext_buffer | BLOB |  |

### oplog

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | 主键 |
| buffer | BLOB |  |

### sqlite_sequence

| 字段名 | 类型 | 说明 |
|--------|------|------|
| name | TEXT |  |
| seq | INTEGER |  |

### stranger

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | 主键 |
| username | TEXT |  |
| local_type | INTEGER |  |
| alias | TEXT |  |
| encrypt_username | TEXT |  |
| flag | INTEGER |  |
| delete_flag | INTEGER |  |
| verify_flag | INTEGER |  |
| remark | TEXT |  |
| remark_quan_pin | TEXT |  |
| remark_pin_yin_initial | TEXT |  |
| nick_name | TEXT |  |
| pin_yin_initial | TEXT |  |
| quan_pin | TEXT |  |
| big_head_url | TEXT |  |
| small_head_url | TEXT |  |
| head_img_md5 | TEXT |  |
| chat_room_notify | INTEGER |  |
| is_in_chat_room | INTEGER |  |
| description | TEXT |  |
| extra_buffer | BLOB |  |
| chat_room_type | INTEGER |  |

### stranger_ticket_info

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | 主键 |
| ticket | TEXT |  |

### ticket_info

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | 主键 |
| ticket | TEXT |  |

## session/session.db（会话数据库）

**共 7 张表**

### Name2Id

| 字段名 | 类型 | 说明 |
|--------|------|------|
| user_name | TEXT | 主键 |

### SessionDeleteTable

| 字段名 | 类型 | 说明 |
|--------|------|------|
| username | TEXT | 主键 |
| delete_time | INTEGER |  |

### SessionDraft

| 字段名 | 类型 | 说明 |
|--------|------|------|
| username | TEXT |  |
| window_id | TEXT |  |
| timestamp | INTEGER |  |
| draft_data | BLOB |  |

### SessionNoContactInfoTable

| 字段名 | 类型 | 说明 |
|--------|------|------|
| username | TEXT | 主键 |
| session_title | TEXT |  |

### SessionTable

| 字段名 | 类型 | 说明 |
|--------|------|------|
| username | TEXT | 主键 |
| type | INTEGER |  |
| unread_count | INTEGER |  |
| unread_first_msg_srv_id | INTEGER |  |
| unread_first_pat_msg_local_id | INTEGER |  |
| unread_first_pat_msg_sort_seq | INTEGER |  |
| is_hidden | INTEGER |  |
| summary | TEXT |  |
| draft | TEXT |  |
| status | INTEGER |  |
| last_timestamp | INTEGER |  |
| sort_timestamp | INTEGER |  |
| last_clear_unread_timestamp | INTEGER |  |
| last_msg_locald_id | INTEGER |  |
| last_msg_type | INTEGER |  |
| last_msg_sub_type | INTEGER |  |
| last_msg_sender | TEXT |  |
| last_sender_display_name | TEXT |  |
| last_msg_ext_type | INTEGER |  |

### SessionUnreadListTable_1

| 字段名 | 类型 | 说明 |
|--------|------|------|
| username_id | INTEGER | 主键 |
| server_id | INTEGER |  |
| create_time | INTEGER |  |
| local_id | INTEGER |  |

### SessionUnreadStatTable_1

| 字段名 | 类型 | 说明 |
|--------|------|------|
| username_id | INTEGER | 主键 |
| unread_stat | INTEGER |  |

## message/message_0.db（消息数据库0（主））

**固定系统表 + 会话分表**：下列系统表结构固定；此外每个聊天会话一张 `Msg_<MD5>` 分表（数量随账号而异，属个人数据，不列出），所有分表结构相同，下文仅给一个泛化样例

### DeleteInfo

| 字段名 | 类型 | 说明 |
|--------|------|------|
| chat_name_id | INTEGER |  |
| delete_table_name | TEXT |  |

### DeleteResInfo

| 字段名 | 类型 | 说明 |
|--------|------|------|
| local_id | INTEGER | 主键 |
| session_name_id | INTEGER |  |
| msg_create_time | INTEGER |  |
| msg_local_id | INTEGER |  |
| res_path | TEXT |  |

### HistoryAddMsgInfo

| 字段名 | 类型 | 说明 |
|--------|------|------|
| session_name_id | INTEGER | 主键 |
| history_id | INTEGER |  |
| server_id | INTEGER |  |
| is_revoke | INTEGER |  |

### HistorySysMsgInfo

| 字段名 | 类型 | 说明 |
|--------|------|------|
| session_name_id | INTEGER | 主键 |
| history_id | INTEGER |  |
| server_id | INTEGER |  |
| is_revoke | INTEGER |  |

### MessageGroupTimeInfo

| 字段名 | 类型 | 说明 |
|--------|------|------|
| chatname_id | INTEGER |  |
| group_id | TEXT |  |
| create_time | INTEGER |  |
| initial_sort_seq | INTEGER |  |
| birth_time | INTEGER |  |

> **会话分表命名规则**：消息按会话分表，每个聊天对象/群一张，表名 = `Msg_` 拼接短 wxid 的 MD5（32 位十六进制）；各分表结构完全相同（见下方样例）。具体分表清单与各表消息条数属个人数据，不在本文档列出。

### Msg_<MD5(短wxid)>（会话消息表样例，所有分表同构）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| local_id | INTEGER | 主键 |
| server_id | INTEGER |  |
| local_type | INTEGER |  |
| sort_seq | INTEGER |  |
| real_sender_id | INTEGER |  |
| create_time | INTEGER |  |
| status | INTEGER |  |
| upload_status | INTEGER |  |
| download_status | INTEGER |  |
| server_seq | INTEGER |  |
| origin_source | INTEGER |  |
| source | TEXT |  |
| message_content | TEXT |  |
| compress_content | TEXT |  |
| packed_info_data | BLOB |  |
| WCDB_CT_message_content | INTEGER |  |
| WCDB_CT_source | INTEGER |  |

### Name2Id

| 字段名 | 类型 | 说明 |
|--------|------|------|
| user_name | TEXT | 主键 |
| is_session | INTEGER |  |

### SendInfo

| 字段名 | 类型 | 说明 |
|--------|------|------|
| chat_name_id | INTEGER |  |
| msg_local_id | INTEGER |  |

### TimeStamp

| 字段名 | 类型 | 说明 |
|--------|------|------|
| timestamp | INTEGER |  |

### sqlite_sequence

| 字段名 | 类型 | 说明 |
|--------|------|------|
| name | TEXT |  |
| seq | INTEGER |  |

### wcdb_builtin_compression_record

| 字段名 | 类型 | 说明 |
|--------|------|------|
| tableName | TEXT | 主键 非空 |
| columns | TEXT | 非空 |
| rowid | INTEGER |  |

## message/media_0.db（媒体/语音数据库）

**共 3 张表**

### Name2Id

| 字段名 | 类型 | 说明 |
|--------|------|------|
| user_name | TEXT | 主键 |

### TimeStamp

| 字段名 | 类型 | 说明 |
|--------|------|------|
| timestamp | INTEGER |  |

### VoiceInfo

| 字段名 | 类型 | 说明 |
|--------|------|------|
| chat_name_id | INTEGER |  |
| create_time | INTEGER |  |
| local_id | INTEGER |  |
| svr_id | INTEGER |  |
| voice_data | BLOB |  |
| data_index | TEXT |  |
