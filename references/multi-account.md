# 多账号共存与切换（wechat-control 参考）

> 需要在主号/小号间切换 wx-cli 指向、了解为什么不能"多开"、或使用多账号改造版时读本文。登记全新微信号的完整流程见 [new-account-sop.md](new-account-sop.md)。

## 1. 结论：单 App 切换账号，不要"多开"（2026-09-11 已验证）

同时运行多个微信实例（多开）在本机已验证会触发风控，**不要再尝试**：

- WeChatTweak `open -n` 共享沙盒多开：触发风控封号（大号中招）；
- 复制 App + 独立 Bundle ID + ad-hoc 重签名：ad-hoc 签名（TeamIdentifier=not set）会被服务器检测，小号也收到安全通知；
- 安卓模拟器多开：封号重灾区；
- 官方多设备（手机 + Mac + iPad）：零风险，但最多 3 个号同时在线。

**最终方案**：只运行一个官方签名 WeChat.app，用哪个号就在微信内切换登录，再用 `wx-account.sh` 让 wx-cli 配置指向该账号的本地数据目录。

## 2. wx-cli 多账号改造（已改源码并重新编译）

原版 3 个问题导致不支持多账号：
1. `init` 忽略配置文件的 `db_dir`，强制自动检测（硬编码 `com.tencent.xinWeChat`）；
2. `find_wechat_pid()` 多进程时 `pgrep` 返回多行，`parse` 失败；
3. 数据目录检测硬编码 `com.tencent.xinWeChat`。

已在 `third-party/wx-cli` 改 3 处源码并重新编译替换 `/usr/local/bin/wx`：
- `src/cli/init.rs`：配置里的 `db_dir` 存在且有效时优先使用；
- `src/scanner/macos.rs`：多进程取第一个 PID，支持配置读取 `wechat_process`；
- `src/config.rs`：支持环境变量 `WX_CLI_BUNDLE_ID` 指定自定义 bundle_id。

> 编译安装步骤随 `third-party/wx-cli` 源码提供；npm 官方版不含此改造。

## 3. 配置目录结构

大小号都在同一个官方沙盒 `com.tencent.xinWeChat` 下，按各自 wxid 目录区分：
```
~/.wx-cli/
├── config.json          # 当前激活账号配置（由 wx-account.sh 切换）
├── all_keys.json        # 当前激活账号密钥
├── daemon.pid / daemon.sock / cache/      # daemon（见 realtime-and-daemon.md）
├── realtime_monitor_state.json / *_config.json / realtime_monitor.log
└── accounts/
    ├── main/            # 大号（主号）：config.json + all_keys.json
    └── alt/             # 小号（自动化专用）：config.json + all_keys.json
```
真实 wxid 用 `bash scripts/wx-account.sh list` 现查，不写进任何文档。

## 4. 切换工具 wx-account.sh

```bash
bash scripts/wx-account.sh list      # 列出所有账号
bash scripts/wx-account.sh use alt   # 切到小号
bash scripts/wx-account.sh use main  # 切回大号
bash scripts/wx-account.sh current   # 显示当前账号
bash scripts/wx-account.sh init alt  # 对指定账号重新提取密钥（需对应微信在运行）
```

**新增账号**：在微信内登录该账号后 `wx-account.sh init <名>` 提取密钥，在 `~/.wx-cli/accounts/<名>/` 生成配置；不要通过复制 App / 改 Bundle ID 多开。

**手动切换**（不用脚本时）：
```bash
cp ~/.wx-cli/accounts/alt/config.json  ~/.wx-cli/config.json
cp ~/.wx-cli/accounts/alt/all_keys.json ~/.wx-cli/all_keys.json   # 切回大号把 alt 换 main
```

切换后需重启 daemon（见 [realtime-and-daemon.md](realtime-and-daemon.md)）。`wx-account.sh` 只切换 wx-cli 读哪个账号的本地库；要读某账号须先在微信内登录并完成密钥提取。

## 5. 同一沙盒下多账号目录检测冲突

`wx init` 会自动扫描 `xwechat_files/` 下所有含 `db_storage` 的子目录并任选一个，**不依赖当前登录账号**，可能选到别的号 → 0 密钥 / 无法解密。即使手改 config.json 的 `db_dir`，`wx init --force` 仍会覆盖。解决办法（临时移走其他账号目录）见 [new-account-sop.md](new-account-sop.md) §A.4.1。
