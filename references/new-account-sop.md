> 新微信号登录 / 多账号共存切换 / 新号各项验证 / daemon 管理的低频操作 SOP（自 SKILL.md 附录 A 整体下沉，内容保持原样）。登记新号、切换账号或排查 daemon 时按本文件执行。

## 附录A：新微信号登录SOP

当需要在新微信号上使用本技能时，按以下步骤操作。

### A.1 前置检查：版本对齐（必做）

新号登记前，必须对齐以下三个版本维度，并记录到数据字典 `docs/database-schema.md`（只含表结构的脱敏明文、随仓；不得写入账号 wxid/昵称、具体 `Msg_<MD5>` 分表清单、记录条数、库大小，见 SKILL §0）。如版本不一致，需重新验证数据库定义。

#### A.1.1 操作系统版本对齐

```bash
echo "硬件架构: $(uname -m)"          # x86_64 / arm64
echo "macOS 版本: $(sw_vers -productVersion)"  # 如 15.7.8
echo "macOS 构建: $(sw_vers -buildVersion)"    # 如 24G824
echo "内核版本: $(uname -r)"                    # 如 24.6.0
```

**当前基线**（本机已验证）：
| 项目 | 值 |
|------|-----|
| 硬件架构 | x86_64 (Intel 黑苹果 OpenCore) |
| macOS 版本 | 15.7.8 (Build 24G824, Sequoia) |
| 内核版本 | 24.6.0 |
| SIP 状态 | disabled（密钥提取必需） |

**SIP 检查**：
```bash
csrutil status
# 应显示 "System Integrity Protection status: disabled."
```

#### A.1.2 微信版本对齐

```bash
defaults read /Applications/WeChat.app/Contents/Info CFBundleShortVersionString
defaults read /Applications/WeChat.app/Contents/Info CFBundleVersion
# 应输出：4.1.8 和 37335（或对应构建号）
```

**当前基线**（本机已验证）：
| 项目 | 值 | 说明 |
|------|-----|------|
| 微信版本 | 4.1.8 | CFBundleShortVersionString |
| 构建号 | 37335 | CFBundleVersion |
| 客户端版本 | 4066646122 | 进程参数 client_version |
| bundle id | com.tencent.xinWeChat | 进程名 WeChat |

**重要限制**：
- wx-cli 硬性限制微信 ~4.1.8，4.1.10+ 密钥提取可能失败
- 升级微信后必须重新验证所有数据库表结构
- 确认已关闭微信自动更新（微信设置→通用→自动更新）

#### A.1.3 数据库定义对齐

```bash
# 1. SQLCipher 版本
which sqlcipher && sqlcipher --version
# 应输出：4.x（微信4.x使用SQLCipher 4.x加密）

# 2. 核心数据库文件存在性检查
ACCOUNT_DIR=~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<新账号wxid>/db_storage
ls -la "$ACCOUNT_DIR/contact/contact.db"
ls -la "$ACCOUNT_DIR/session/session.db"
ls -la "$ACCOUNT_DIR/message/message_0.db"
ls -la "$ACCOUNT_DIR/message/media_0.db"

# 3. 核心表结构验证（密钥提取后执行）
# contact.db 应有表：contact, name2id, chat_room, chatroom_member
# message_0.db 应有表：Name2Id, Msg_<MD5(wxid)>
# media_0.db 应有表：Name2Id, VoiceInfo, TimeStamp
# session.db 应有表：SessionTable, Name2Id
```

**当前基线**（本机已验证，微信4.1.8）：
| 数据库 | 核心表 | 说明 |
|--------|--------|------|
| contact/contact.db | contact, name2id, chat_room, chatroom_member, stranger | 16张表 |
| session/session.db | SessionTable, Name2Id, SessionDraft | 8张表 |
| message/message_0.db | Name2Id, Msg_<MD5(短wxid)> | 按会话分表 |
| message/media_0.db | Name2Id, VoiceInfo, TimeStamp | 语音BLOB直接存储 |

**版本不一致处理**：
- 如微信版本变化 → 重新运行 `wx init --force` 提取密钥
- 如表结构变化 → 重新 dump 表结构，按脱敏口径（账号行占位、Msg 分表只留一个泛化样例、去掉记录条数与库大小）更新 `docs/database-schema.md`，明文随仓（见 SKILL §0）
- 如SQLCipher版本变化 → 更新解密参数
- 所有变化必须记录到数据字典 `docs/database-schema.md` 的环境与版本信息表（保持脱敏、不含账号与统计数据，见 SKILL §0）

#### A.1.4 依赖工具检查

```bash
which sqlcipher && sqlcipher --version
which wx && wx --version
ls tools/silk-v3-decoder/converter.sh
python3 -c "import funasr; print('FunASR OK')" 2>/dev/null || echo "FunASR 未安装（语音转写需要）"
```

### A.2 登录新微信号

1. 在微信中切换账号或登录新微信号
2. 等待微信完全加载（通讯录、聊天记录同步完成）
3. **重要**：登录后至少点开几个聊天窗口，触发数据库懒加载（部分数据库如 media_0.db 需要播放语音后才会创建密钥）

### A.3 确认新账号的数据目录

```bash
# 列出所有微信账号数据目录，找到新登录的账号
ls -lt ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/ | grep -E "^d"

# 最近修改的目录通常是当前登录的账号
# 目录名格式：wxid_xxxxxxxxxxxxx_xxxx
```

**账号对应关系**（角色固定；真实昵称/wxid 属个人标识，不写入公开仓）：

> 大小号角色定义与用途表见 SKILL.md §2.5（运行面入口保留该表，本文不重复）。

> 真实值用 `bash scripts/wx-account.sh list` 现查，或看本机不入库的 `~/.wx-cli/accounts/main|alt/` 目录；不要把真实昵称/wxid 填回本文件。

### A.4 提取新账号密钥

```bash
# 方法1：交互式输入密码（推荐）
sudo wx init --force

# 方法2：非交互式（仅限自动化临时使用，密码通过标准输入传入）
echo "YOUR_SUDO_PASSWORD" | sudo -S wx init --force
```

> 安全提示：`echo 口令 | sudo -S` 会让口令出现在 shell history 与进程列表；交互式场景建议先执行一次 `sudo -v` 缓存凭证、再跑 `sudo wx init`。`YOUR_SUDO_PASSWORD` 仅为占位符，切勿把真实口令写进脚本或提交到仓库（真实口令只当次口述/交互输入，凭证来处见安全基线技能）。

#### A.4.1 多账号目录检测问题（重要）

wx-cli init 会**自动扫描** `xwechat_files/` 下所有包含 `db_storage` 的子目录，并选择其中一个作为数据目录。**它不依赖当前登录的账号**，可能检测到错误的账号目录（如小号或其他历史账号）。

**症状**：
- `wx init` 输出"找到数据目录: .../wxid_其他账号/db_storage"
- 匹配到 0 个密钥
- `wx sessions` 报错"无法解密 session.db"

**解决方案**：临时把其他账号目录移到 `/tmp/`，只保留目标账号目录，提取完成后再移回来：

```bash
# 1. 把其他账号目录移到 /tmp/
mv ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_其他账号 /tmp/

# 2. 提取目标账号密钥
echo "YOUR_SUDO_PASSWORD" | sudo -S wx init --force

# 3. 恢复其他账号目录
mv /tmp/wxid_其他账号 ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/

# 4. 验证
wx sessions --limit 3
```

**注意**：即使手动修改 `~/.wx-cli/config.json` 中的 `db_dir`，`wx init --force` 仍会自动检测并覆盖。必须用上述移走目录的方法。

**成功标志**：
```
找到 19 个加密数据库
扫描进程内存寻找密钥...
找到 18 个候选密钥
匹配到 16/18 个密钥
成功提取 16 个数据库密钥
密钥已保存: ~/.wx-cli/all_keys.json
配置已保存: ~/.wx-cli/config.json
```

**常见失败原因**：
- `task_for_pid 失败 (kr=5)`：SIP 未关闭，或未用 sudo
- `找到 0 个候选密钥`：当前登录账号与数据目录不匹配，或微信版本过高
- 提示需要重签微信：**忽略该提示**，关 SIP + sudo 即可不重签提取

#### A.4.2 多账号方案结论（单设备"多开"已验证不可行，2026-09-11）

同时运行多个微信实例（多开）在本机已验证会触发风控，**不要再尝试**。踩坑结论：

- WeChatTweak `open -n` 共享沙盒多开：触发风控封号（大号中招）；
- 复制 App + 独立 Bundle ID + ad-hoc 重签名：ad-hoc 签名（TeamIdentifier=not set）会被微信服务器检测到，小号也收到安全通知；
- 安卓模拟器多开：封号重灾区；
- 官方多设备（手机 + Mac + iPad）：零风险，但最多 3 个号同时在线。

**本机最终方案不是"多开"，而是"单 App 切换账号"**：只运行一个官方签名 WeChat.app，用哪个号就在微信内切换登录，再用 `wx-account.sh` 让 wx-cli 配置指向该账号的本地数据目录（见下文与 A.6）。同一沙盒下多账号的目录检测冲突见 A.4.1。

**wx-cli 多账号支持**（已修改源码并重新编译，2026-09-11；单 App 切换账号仍依赖此改造）：

wx-cli 原版有3个问题导致不支持多开：
1. `init` 命令忽略配置文件的 `db_dir`，强制自动检测（硬编码 `com.tencent.xinWeChat`）
2. `find_wechat_pid()` 多进程时 `pgrep` 返回多行，`parse` 失败
3. 数据目录检测硬编码 `com.tencent.xinWeChat`

已修改 `third-party/wx-cli` 源码3处并重新编译替换 `/usr/local/bin/wx`：
- `src/cli/init.rs`：优先使用配置文件里的 `db_dir`（存在且有效时）
- `src/scanner/macos.rs`：多进程时取第一个 PID，支持配置读取 `wechat_process`
- `src/config.rs`：支持环境变量 `WX_CLI_BUNDLE_ID` 指定自定义 bundle_id

**多账号配置目录结构**（大小号都在同一个官方沙盒 com.tencent.xinWeChat 下，按 wxid 目录区分）：
```
~/.wx-cli/
├── config.json          ← 当前激活账号的配置（由 wx-account.sh 切换）
├── all_keys.json        ← 当前激活账号的密钥
├── daemon.pid           ← daemon 进程信息（JSON 格式，含 pid 和 exe）
├── daemon.sock          ← daemon Unix socket
├── cache/               ← 解密数据库缓存（daemon 自动管理）
├── realtime_monitor_state.json   ← 实时监听器状态
├── realtime_monitor_config.json  ← 实时监听器配置
├── realtime_monitor.log          ← 实时监听器日志
└── accounts/
    ├── main/            ← 大号（主号）
    │   ├── config.json  (db_dir 指向 com.tencent.xinWeChat/〈主号wxid〉)
    │   └── all_keys.json
    └── alt/             ← 小号（自动化专用）
        ├── config.json  (db_dir 指向 com.tencent.xinWeChat/〈小号wxid〉)
        └── all_keys.json
```

> 大小号数据都在同一个沙盒 `com.tencent.xinWeChat` 下、按各自 wxid 目录区分；真实 wxid 用 `wx-account.sh list` 现查。用哪个号就在微信内切换登录，并由 `wx-account.sh` 切换 wx-cli 配置指向。

**多账号切换工具**：`scripts/wx-account.sh`
```bash
# 列出所有账号
bash scripts/wx-account.sh list

# 切换到小号
bash scripts/wx-account.sh use alt

# 切换回大号
bash scripts/wx-account.sh use main

# 显示当前账号
bash scripts/wx-account.sh current

# 对指定账号重新提取密钥（需对应微信在运行）
bash scripts/wx-account.sh init alt
```

**新增账号**：在微信内登录该账号后，用 `wx-account.sh init <名>` 提取密钥，并在 `~/.wx-cli/accounts/<名>/` 下生成对应配置；不要通过复制 App / 改 Bundle ID 多开（原因见 A.4.2 开头结论）。

### A.5 验证密钥

```bash
# 能正常输出会话列表即说明密钥有效
wx sessions --limit 5

# 验证配置文件中的 db_dir 指向新账号
cat ~/.wx-cli/config.json
# 确认 db_dir 路径包含新账号的 wxid
```

### A.6 多账号切换与共存（2026-09-11 更新）

wx-cli 的配置是全局的（`~/.wx-cli/config.json`），同一时间只指向一个账号；在"单 App 切换账号"方案下，用 `wx-account.sh` 在已配置账号间切换 wx-cli 指向（多开方案已验证不可行，见 A.4.2）。

**使用 wx-account.sh 切换账号**（推荐）：
```bash
# 列出所有已配置账号
bash scripts/wx-account.sh list

# 切换到小号
bash scripts/wx-account.sh use alt

# 切换回大号
bash scripts/wx-account.sh use main

# 显示当前账号
bash scripts/wx-account.sh current

# 对指定账号重新提取密钥（需对应微信在运行）
bash scripts/wx-account.sh init alt
```

**手动切换**（不使用脚本时）：
```bash
# 切换到小号
cp ~/.wx-cli/accounts/alt/config.json ~/.wx-cli/config.json
cp ~/.wx-cli/accounts/alt/all_keys.json ~/.wx-cli/all_keys.json

# 切换回大号
cp ~/.wx-cli/accounts/main/config.json ~/.wx-cli/config.json
cp ~/.wx-cli/accounts/main/all_keys.json ~/.wx-cli/all_keys.json
```

**注意**：`wx-account.sh` 只切换 wx-cli 读取哪个账号的本地数据库；要读取某账号，需先在微信内登录该账号并完成密钥提取。切换后 `wx sessions` / `wx history` 即读取对应账号数据库。

### A.7 语音功能验证（如需）

新账号登录后，如需使用语音转文字功能：

#### A.7.1 语音数据存储机制（2026-09-11 大号验证）

微信 4.x 的语音数据有**两个存储位置**：

1. **media_0.db 的 VoiceInfo 表（主要来源）**：
   - 路径：`db_storage/message/media_0.db`
   - 表结构：`VoiceInfo(chat_name_id, create_time, local_id, svr_id, voice_data BLOB, data_index)`
   - `voice_data` 字段直接存储 SILK V3 格式的语音 BLOB（前面多1字节 0x02 头）
   - **不需要手动点击播放**，语音数据会自动下载到 media_0.db
   - `Name2Id` 表：rowid → user_name(wxid)，用于映射 chat_name_id

2. **全局缓存目录（备用/历史）**：
   ```
   ~/Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat/2.0b4.0.9/<hash>/Message/MessageTemp/<chat_hash>/Audio/*.aud.silk
   ```
   - 格式为 `.aud.silk`（比标准 SILK_V3 多1字节头 0x02）
   - 全局共享，不按账号分开存储
   - 主要是历史语音（3.x 迁移过来的），新语音优先存在 media_0.db

#### A.7.2 关键发现与坑（必须注意）

| 问题 | 说明 | 解决方案 |
|------|------|----------|
| **local_id 不全局唯一** | VoiceInfo 表中 local_id 是每个会话内自增，不同会话可能有相同 local_id | 查询时必须同时用 `chat_name_id + local_id` 定位，否则会取到错误的语音 |
| **新消息有延迟** | 微信收到消息后约5-10秒才写入数据库（WAL模式） | 轮询时需要等待，或检查 WAL 文件修改时间 |
| **download_status 含义** | 语音消息 download_status=1 表示已下载（不是5）；文字消息=0 | 不要用 download_status=5 判断语音是否已下载 |
| **语音消息类型** | local_type=**34**（不是3） | 查询语音消息时用 `local_type = 34` |
| **会话表名映射** | 表名是 `Msg_<MD5(短wxid)>`，短wxid去掉 `_yyyy` 后缀 | 例如小号 `〈小号wxid，wxid_xxxxxxxx_yyyy〉` → 短 wxid `wxid_xxxxxxxx` → 表名 `Msg_<MD5>` |

#### A.7.3 发送者识别链路

- **私聊**：`VoiceInfo.chat_name_id` → `Name2Id.user_name`(wxid) → `contact.db` 的 contact 表查昵称
- **群聊**：`chat_name_id` 是群ID，具体发言人需 join `message_0.db` 会话表的 `real_sender_id` → `Name2Id` 映射
- **新语音通知**：轮询 `VoiceInfo` 表新增 `create_time` 即可发现

#### A.7.4 验证步骤

```bash
# 1. 列出最近语音
python3 scripts/voice-transcribe.py list --limit 5

# 2. 转写最近语音（验证完整链路）
python3 scripts/voice-transcribe.py transcribe --limit 2

# 3. 直查 VoiceInfo 表验证数据存在
sqlcipher ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>/db_storage/message/media_0.db << EOF
PRAGMA key="x'<media_key>'";
SELECT chat_name_id, datetime(create_time,'unixepoch','localtime'), local_id, length(voice_data) FROM VoiceInfo ORDER BY create_time DESC LIMIT 5;
EOF
```

**注意**：
- media_0.db 密钥需要 `wx init --force` 提取（微信运行中即可，不需要播放语音）
- 如新账号无语音消息，此步骤可跳过

### A.8 联系人识别验证（必做）

验证 contact.db 密钥和联系人识别功能（昵称/备注名/微信号）：

```bash
# 1. 列出有语音的会话，验证联系人识别
python3 scripts/voice-monitor.py list-sessions

# 2. 验证输出应包含：显示名称、备注名、微信昵称、微信号
# 例如（均为占位，实际以本机查询为准）：
# ID   语音数  最后语音时间    显示名称     备注名     微信昵称     微信号
# 43   3      2026-09-11...  〈备注名〉   〈备注名〉 〈微信昵称〉 wxid_xxxxxxxx
```

**验证要点**：
- 显示名称优先级：备注名 > 微信昵称 > alias > wxid
- 私聊：显示名称通常是备注名，其次是微信昵称
- 群聊：显示名称是群名称（真实群名属个人信息，不写入公开仓，文档中一律以"〈某群名称〉"占位）
- 微信号：个人是 wxid_xxx，群聊是 xxx@chatroom

**常见问题**：
- 显示名称为空：contact.db 密钥未提取或不匹配，重新运行 `wx init --force`
- 只有微信号没有昵称：该联系人未添加好友或信息未同步

### A.9 监控功能初始化（必做）

初始化语音监控和通用监控的状态与配置：

```bash
# 1. 清除旧的监控状态（如果切换了账号）
rm -f ~/.wx-cli/voice_monitor_state.json
rm -f ~/.wx-cli/monitor_state.json

# 2. 验证语音监控能正常启动（单次检查，不循环）
python3 scripts/voice-monitor.py once
# 应输出：首次启动，从30秒前开始监控（缓冲时间）

# 3. 查看监控状态
python3 scripts/voice-monitor.py status

# 4. 配置通用监控（如需）
python3 scripts/wx-monitor.py config list
# 添加监控群（按需）
# python3 scripts/wx-monitor.py config add-group "群名称"
```

**监控配置文件位置**：
- 语音监控状态：`~/.wx-cli/voice_monitor_state.json`
- 通用监控配置：`~/.wx-cli/monitor_config.json`
- 通用监控状态：`~/.wx-cli/monitor_state.json`

**注意**：
- 切换账号后必须清除旧状态，否则会漏掉新账号的历史消息或重复处理
- 首次启动有30秒缓冲时间，会处理最近30秒内的语音
- 监控规划见 `docs/monitoring-plan.md`（设计文档，随仓公开但 AI 运行时不加载，见 SKILL §0）

### A.10 写操作功能验证（仅小号）

**仅允许在小号上执行写操作测试**：

```bash
# 发送测试消息到文件传输助手
bash scripts/wechat-ui/send_message.sh "文件传输助手" "测试消息"

# 快速模式（跳过发送后验证）
bash scripts/wechat-ui/send_message.sh "文件传输助手" "测试消息" --no-verify
```

**自动回复配置**（仅小号）：
```bash
# 添加触发关键词
python3 scripts/wx-send.py auto-reply config add-keyword "在吗"

# 启动自动回复
python3 scripts/wx-send.py auto-reply run --interval 60
```

### A.11 完成检查清单

**版本对齐（A.1）**
- [ ] 操作系统版本对齐（硬件架构/macOS版本/构建号/内核版本/SIP状态）
- [ ] 微信版本对齐（版本号/构建号/客户端版本，确认~4.1.8）
- [ ] 数据库定义对齐（SQLCipher版本/核心表结构验证）
- [ ] 依赖工具检查（sqlcipher/wx/silk-decoder/FunASR）
- [ ] 版本信息已记录到数据字典 `docs/database-schema.md`（脱敏明文、只含表结构，见 SKILL §0）

**账号登记（A.2-A.6）**
- [ ] 新微信号已登录并加载完成
- [ ] 数据目录已确认
- [ ] 密钥提取成功（`wx init --force`，含多账号目录处理）
- [ ] 密钥验证通过（`wx sessions` 正常输出）
- [ ] 账号已能在 `wx-account.sh list` 与本机 `~/.wx-cli/accounts/` 中查到（真实昵称/wxid 不写入公开仓，SKILL §2.5 只保留角色占位）

**多账号配置（A.4.2 / A.6，单 App 切换账号）**
- [ ] 不使用复制 App / 改 Bundle ID 多开（已验证会触发风控）
- [ ] 目标微信号已在同一个官方 WeChat.app 内切换登录并加载完成
- [ ] wx-cli 已支持多账号（配置文件 db_dir / 多进程 PID / 环境变量 BUNDLE_ID）
- [ ] 账号配置已保存到 `~/.wx-cli/accounts/<name>/`
- [ ] `wx-account.sh list` 可正常列出所有账号
- [ ] `wx-account.sh use <name>` 可正常切换并读取对应账号数据

**功能验证（A.7-A.10）**
- [ ] 语音功能验证（如需，含VoiceInfo表/转写/发送者识别）
- [ ] **联系人识别验证**（显示名称/备注名/微信昵称/微信号）
- [ ] **监控功能初始化**（清除旧状态/验证启动/配置监控）
- [ ] 写操作测试（仅小号，如需）

---

### A.12 实时消息监听器（realtime-monitor.py，2026-09-11 新增）

**脚本位置**：`scripts/realtime-monitor.py`

**功能**：
- 启动时自动检查 daemon 配置（db_dir 是否指向当前登录账号），不对则自动重启 daemon
- 每 N 秒轮询 `wx new-messages`，daemon 自动检测数据库更新，**无需每次重启 daemon**
- 支持**所有消息类型**：文字、语音、图片、视频、链接、文件、系统消息（撤回等）
- 检测到新消息时打印详细通知：发送者、会话、内容、类型、时间、是否群聊
- 支持重要消息判定：关键词、特定发送者、特定群
- 支持语音消息标记（可选择自动转写）
- 日志记录到 `~/.wx-cli/realtime_monitor.log`

**常用命令**：
```bash
# 启动实时监控（10秒轮询，前台运行，Ctrl+C 停止）
python3 scripts/realtime-monitor.py monitor --interval 10

# 后台持续运行
nohup python3 scripts/realtime-monitor.py monitor --interval 10 > /tmp/realtime-monitor.log 2>&1 &

# 单次检查（不循环）
python3 scripts/realtime-monitor.py once

# 查看监控状态
python3 scripts/realtime-monitor.py status

# 查看配置
python3 scripts/realtime-monitor.py config show

# 修改轮询间隔
python3 scripts/realtime-monitor.py config set interval_seconds 5

# 添加重要关键词
python3 scripts/realtime-monitor.py config add important_keywords "紧急,重要"

# 添加重要发送者
python3 scripts/realtime-monitor.py config add important_senders "张三"

# 重置状态基线（重新初始化，避免把历史消息当成新消息）
python3 scripts/realtime-monitor.py reset

# 手动重启 daemon
python3 scripts/realtime-monitor.py restart-daemon
```

**状态与配置文件**：
- 状态：`~/.wx-cli/realtime_monitor_state.json`
- 配置：`~/.wx-cli/realtime_monitor_config.json`
- 日志：`~/.wx-cli/realtime_monitor.log`

---

### A.13 daemon 管理规范（2026-09-11 新增）

**wx-cli daemon 机制**：
- wx-cli 运行时会启动一个后台 daemon 进程（`/usr/local/bin/wx`），通过 Unix socket 通信
- daemon 启动时读取 `~/.wx-cli/config.json` 的 `db_dir`，并缓存该配置
- daemon 把解密后的数据库缓存到 `~/.wx-cli/cache/` 目录
- **每次查询时，daemon 自动检测加密数据库是否有更新，有更新则重新解密**（无需重启 daemon）

**什么时候需要重启 daemon**：

| 场景 | 是否需要重启 | 原因 |
|---|---|---|
| 切换账号 / 修改 `config.json` 的 `db_dir` | ✅ 需要 | daemon 启动时缓存了 db_dir，修改配置后不会自动重新读取 |
| 正常收到新消息 | ❌ 不需要 | daemon 每次查询自动检测数据库更新并重新解密 |
| 清除解密缓存 | ❌ 不需要 | daemon 自动管理缓存 |
| wx-cli 二进制更新 | ✅ 需要 | 运行中的 daemon 是旧版本 |
| 数据库密钥失效/重新提取 | ✅ 需要 | daemon 缓存了旧密钥 |

**重启 daemon 的标准操作**：
```bash
# 1. 读取并 kill daemon 进程（daemon.pid 是 JSON 格式，不是纯数字）
DAEMON_PID=$(python3 -c "import json; print(json.load(open('$HOME/.wx-cli/daemon.pid'))['pid'])")
kill -9 $DAEMON_PID

# 2. 清除残留文件
rm -f ~/.wx-cli/daemon.sock ~/.wx-cli/daemon.pid

# 3. 清除解密缓存（切换账号时建议清除，正常使用不需要）
rm -f ~/.wx-cli/cache/*.db

# 4. 运行任意 wx 命令自动重启 daemon
wx sessions --limit 1

# 5. 验证 daemon 读取的 DB_DIR 是否正确
tail -10 ~/.wx-cli/daemon.log | grep "DB_DIR"
```

**验证 daemon 状态**：
```bash
# 查看 daemon 进程
ps aux | grep "wx" | grep -v grep

# 查看 daemon 日志
tail -20 ~/.wx-cli/daemon.log

# 查看 daemon 读取的 DB_DIR
grep "DB_DIR" ~/.wx-cli/daemon.log | tail -1
```



