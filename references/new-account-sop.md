# 新微信号登记 SOP（主干）

> 登记一个新微信号、在新账号上验证只读链路时按本文执行（低频操作手册）。
> 相关专题已拆为独立参考，按需跳转，本文不重复：
> - **多账号共存/切换、为什么不能多开、wx-cli 多账号源码改造** → [multi-account.md](multi-account.md)
> - **语音存储机制、转写链路、发送者识别** → [voice-pipeline.md](voice-pipeline.md)
> - **实时监听器 realtime-monitor、daemon 机制与重启** → [realtime-and-daemon.md](realtime-and-daemon.md)
> - 单账号首次安装与密钥提取、平台差异 → [install.md](install.md)

## A.1 前置检查：版本对齐（必做）

登记前对齐四个维度；版本不一致需重新验证数据库定义。机器硬件/OS 型号台账在双机管理技能，此处只记录与微信数据相关的版本。

**A.1.1 操作系统与 SIP**
```bash
echo "架构: $(uname -m)"; echo "macOS: $(sw_vers -productVersion) $(sw_vers -buildVersion)"; echo "内核: $(uname -r)"
csrutil status        # 密钥提取要求 disabled
```

**A.1.2 微信版本（硬性 ~4.1.8）**
```bash
defaults read /Applications/WeChat.app/Contents/Info CFBundleShortVersionString  # 4.1.8
defaults read /Applications/WeChat.app/Contents/Info CFBundleVersion             # 37335
```
基线：微信 4.1.8 / 构建号 37335 / client_version 4066646122 / bundle id `com.tencent.xinWeChat` / 进程名 `WeChat`。
- wx-cli 硬限制微信 ~4.1.8，4.1.10+ 密钥存储格式变更、可能提取失败；
- 升级微信后必须重新验证所有表结构；确认已关闭微信自动更新。

**A.1.3 数据库定义对齐**
```bash
which sqlcipher && sqlcipher --version     # 微信 4.x 用 SQLCipher 4.x
ACCOUNT_DIR=~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<新账号wxid>/db_storage
ls -la "$ACCOUNT_DIR/contact/contact.db" "$ACCOUNT_DIR/session/session.db" \
       "$ACCOUNT_DIR/message/message_0.db" "$ACCOUNT_DIR/message/media_0.db"
```
核心表基线（微信 4.1.8）：

| 数据库 | 核心表 |
|--------|--------|
| contact/contact.db | contact, name2id, chat_room, chatroom_member, stranger（16 张表） |
| session/session.db | SessionTable, Name2Id, SessionDraft（8 张表） |
| message/message_0.db | Name2Id, Msg_<MD5(短wxid)>（按会话分表） |
| message/media_0.db | Name2Id, VoiceInfo, TimeStamp（语音 BLOB 直存） |

不一致处理：微信版本变 → `wx init --force` 重提密钥；表结构变 → 重新 dump，按脱敏口径（账号行占位、Msg 分表只留一个泛化样例、去掉记录条数与库大小）更新 `docs/database-schema.md` 明文随仓（见 SKILL §0）；SQLCipher 版本变 → 更新解密参数。

**A.1.4 依赖工具**
```bash
which sqlcipher && sqlcipher --version
which wx && wx --version
ls tools/silk-v3-decoder/converter.sh
python3 -c "import funasr; print('FunASR OK')" 2>/dev/null || echo "FunASR 未装（语音转写需要）"
```

## A.2 登录新微信号
1. 在微信中切换/登录新号，等待通讯录与聊天记录同步完成；
2. **登录后点开几个聊天窗口、播放几条语音**，触发数据库懒加载（media_0.db 等需播放后才会创建密钥）。

## A.3 确认新账号数据目录
```bash
ls -lt ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/ | grep -E "^d"
# 最近修改的目录通常是当前账号；目录名形如 wxid_xxxxxxxxxxxxx_xxxx
```
真实昵称/wxid 属个人标识不入仓，用 `bash scripts/wx-account.sh list` 现查，或看本机不入库的 `~/.wx-cli/accounts/main|alt/`；角色定义见 SKILL §2 账号表，文档一律用占位符。

## A.4 提取新账号密钥
```bash
# 方法1：交互输入密码（推荐）。可先 sudo -v 缓存凭证再 sudo wx init
sudo wx init --force
# 方法2：非交互（仅自动化临时用）
echo "YOUR_SUDO_PASSWORD" | sudo -S wx init --force
```
> 安全提示：`echo 口令 | sudo -S` 会让口令进入 shell history 与进程列表，交互场景优先 `sudo -v`。`YOUR_SUDO_PASSWORD` 仅占位，切勿把真实口令写进脚本/仓库（口令只当次口述或交互输入，凭证来处见安全基线技能）。

### A.4.1 多账号目录检测冲突（重要坑）
wx-cli init 会**自动扫描** `xwechat_files/` 下所有含 `db_storage` 的子目录并任选一个，**不依赖当前登录账号**，可能选到别的号。症状：输出"找到数据目录 .../wxid_其他账号/db_storage"、匹配 0 密钥、`wx sessions` 报无法解密 session.db。

即使手改 `~/.wx-cli/config.json` 的 db_dir，`wx init --force` 仍会覆盖。解决：临时移走其他账号目录。
```bash
mv ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_其他账号 /tmp/
echo "YOUR_SUDO_PASSWORD" | sudo -S wx init --force
mv /tmp/wxid_其他账号 ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/
wx sessions --limit 3
```

**成功标志**：找到 19 个加密数据库 / 找到 18 个候选密钥 / 匹配 16/18 / 成功提取 16 个密钥 / 保存 all_keys.json 与 config.json。
**失败原因**：`task_for_pid (kr=5)`＝SIP 未关或未 sudo；`0 个候选密钥`＝账号与目录不匹配或微信版本过高；提示重签＝忽略，关 SIP+sudo 即可。完整平台对照见 [install.md](install.md)。

> 多账号（主号/小号）方案、wx-cli 源码改造与切换工具见 [multi-account.md](multi-account.md)。

## A.5 验证密钥
```bash
wx sessions --limit 5
cat ~/.wx-cli/config.json     # 确认 db_dir 路径含目标账号 wxid
```

## A.8 联系人识别验证（必做）
```bash
python3 scripts/voice-monitor.py list-sessions
# 输出应含：显示名称、备注名、微信昵称、微信号（均以本机查询为准，文档示例用占位）
```
- 显示名称优先级：备注名 > 微信昵称 > alias > wxid；私聊通常是备注名，群聊是群名称（真实群名不入仓，用"〈某群名称〉"占位）；微信号：个人 `wxid_xxx`、群聊 `xxx@chatroom`。
- 显示名称为空：contact.db 密钥未提取/不匹配，重跑 `wx init --force`；只有微信号没昵称：未加好友或信息未同步。

## A.9 监控功能初始化（必做）
```bash
rm -f ~/.wx-cli/voice_monitor_state.json ~/.wx-cli/monitor_state.json   # 切号后清旧状态
python3 scripts/voice-monitor.py once        # 单次检查（首次有 30 秒缓冲）
python3 scripts/voice-monitor.py status
python3 scripts/wx-monitor.py config list    # 通用监控配置（按需 add-group）
```
切号后必须清旧状态，否则漏消息或重复处理。状态/配置文件：`~/.wx-cli/voice_monitor_state.json`、`monitor_config.json`、`monitor_state.json`。监控规划见开发面 `docs/PRD.md` 附录 B（运行时不加载）；实时监听器与 daemon 见 [realtime-and-daemon.md](realtime-and-daemon.md)。

## A.10 写操作功能验证（仅小号）
```bash
bash scripts/wechat-ui/send_message.sh "文件传输助手" "测试消息"            # 完整验证
bash scripts/wechat-ui/send_message.sh "文件传输助手" "测试消息" --no-verify # 跳过发送后 OCR
python3 scripts/wx-send.py auto-reply config add-keyword "在吗"
python3 scripts/wx-send.py auto-reply run --interval 60
```
写操作仅限小号、逐次人工确认，风控要求见 SKILL §6，UI 方案细节见开发面 `docs/PRD.md` 附录 C。

## A.11 完成检查清单

**版本对齐（A.1）**
- [ ] OS/SIP、微信版本（~4.1.8）、SQLCipher 与核心表结构、依赖工具已核对
- [ ] 版本信息按脱敏口径记录到 `docs/database-schema.md`（只含表结构，无账号/统计数据）

**账号登记（A.2–A.5）**
- [ ] 新号已登录并加载、数据目录已确认、密钥提取成功、`wx sessions` 验证通过
- [ ] `wx-account.sh list` 与 `~/.wx-cli/accounts/` 可查到该账号（真实昵称/wxid 不入仓）

**多账号（详见 multi-account.md）**
- [ ] 不用复制 App/改 Bundle ID 多开；在同一官方 App 内切号；配置已存 `accounts/<名>/`；`use <名>` 切换后重启 daemon

**功能验证**
- [ ] 联系人识别（显示名/备注/昵称/微信号）
- [ ] 监控初始化（清旧状态/启动验证/配置）
- [ ] 语音链路（如需，见 voice-pipeline.md）
- [ ] 写操作测试（仅小号，如需）
