# 安装、密钥提取与平台差异（wechat-control 参考）

> 首次安装/卸载 wx-cli、换新机器、首次提取密钥、排查平台差异时读本文。
> 多账号（主号/小号切换、源码编译多账号版）见 [multi-account.md](multi-account.md)；登记一个新微信号的完整 SOP 见 [new-account-sop.md](new-account-sop.md)。

## 1. 前置依赖

**通用**：Node.js >= 18；微信 ~4.1.8 已安装并登录（4.1.10+ 密钥提取可能失败）。

**macOS**：SIP 已关闭（密钥提取需要）；推荐 Homebrew 装 Node：`brew install node`。

**Windows（未实测，基于官方文档）**：以管理员身份运行 PowerShell（读进程内存需要管理员）；Node 从 nodejs.org 或 `winget install OpenJS.NodeJS`；无需关 SIP（无此机制）；微信进程名 `Weixin.exe`。

## 2. 安装 wx-cli

**先选版本**：
- 只做单账号只读查询/总结 → npm 官方版（`@jackwener/wx-cli`）即可。
- 需要多账号切换（主号/小号间切换 wx-cli 指向）→ npm 版不支持，须用本仓 `third-party/wx-cli`（本人 fork 的多账号改造版）源码编译替换 `wx`，步骤见 [multi-account.md](multi-account.md)。

**npm 通用安装（单账号只读推荐，macOS/Windows/Linux）**：
```bash
npm install -g @jackwener/wx-cli
# postinstall 被 npm 安全策略阻止时：
npm install -g --allow-scripts=@jackwener/wx-cli @jackwener/wx-cli
wx --version            # 应输出 0.3.0 或更高
```

> **macOS 多 node 注意**：若系统有多个 node（如豆包沙箱 node 遮蔽 Homebrew node），用 Homebrew node 完整路径执行：Intel 机 `/usr/local/bin/npm`、Apple Silicon `/opt/homebrew/bin/npm`（或 `$(brew --prefix)/bin/npm`）。
> **Windows 注意**：在管理员 PowerShell 执行；也可用官方一键 `irm https://raw.githubusercontent.com/jackwener/wx-cli/main/install.ps1 | iex`（未实测）。

## 3. 卸载与清理

```bash
npm uninstall -g @jackwener/wx-cli
which wx            # macOS/Linux 应 not found；Windows 用 where wx
# 可选：清理配置与密钥（谨慎）
rm -rf ~/.wx-cli/                                   # macOS/Linux
Remove-Item -Recurse -Force "$env:USERPROFILE\.wx-cli"   # Windows
```

其他安装方式：macOS/Linux 一键脚本 `curl -fsSL https://raw.githubusercontent.com/jackwener/wx-cli/main/install.sh | bash`（不推荐，曾 404）；或从 GitHub Releases 下载对应平台二进制放到 PATH 并加执行权限。

## 4. 首次密钥提取（单账号标准流程）

密钥提取是**短暂操作**：附加进程→扫描内存→退出，提取完立即释放。同账号、同版本、不重装时**一次提取永久有效**，无需重复扫描；仅在微信升级、切换账号、重装微信后重新提取。

**原则：关 SIP + sudo + 不重签微信。** 绝对不要 ad-hoc 重签（`codesign --force --deep --sign -`），不要用 Frida/lldb 持续注入 hook 微信进程——重签 + 持续注入曾触发风控（强制下线 + 违规提示）。

**macOS**：
```bash
csrutil status          # 应显示 disabled；未关需进恢复模式关闭（黑苹果改 OpenCore csr-active-config）
sudo wx init            # 关 SIP 后无需重签
sudo wx init --force    # 有旧配置/需强制重扫时
wx sessions             # 能输出会话列表即密钥有效
```

**Windows（未实测）**：管理员 PowerShell 执行 `wx init`（会自动检测 Weixin.exe 进程与数据目录）；被 Defender 拦截需加信任或临时关实时防护（谨慎）。

**成功标志**：
```
找到 19 个加密数据库 / 找到 18 个候选密钥 / 匹配到 16/18 个密钥
成功提取 16 个数据库密钥
密钥已保存: ~/.wx-cli/all_keys.json
配置已保存: ~/.wx-cli/config.json
```

**常见失败对照**：

| 错误 | 原因 | 解决 |
|------|------|------|
| macOS `task_for_pid 失败 (kr=5)` | SIP 未关或未用 sudo | 关 SIP，用 `sudo wx init` |
| Windows 权限不足 / 拒绝访问 | 未用管理员 PowerShell | 右键以管理员身份运行 |
| Windows 被杀软拦截 | Defender 阻止读内存 | 加信任或临时关实时防护（谨慎） |
| `找到 0 个候选密钥` | 登录账号与数据目录不匹配，或微信版本过高（4.1.10+） | 确认登录目标账号；降到微信 ~4.1.8；多账号目录冲突见 new-account-sop §A.4.1 |
| 提示需要重签微信 | wx-cli 默认文案假设重签 | **忽略**，关 SIP + sudo（macOS）/管理员 PowerShell（Windows）即可不重签 |

**为什么不是所有库都有密钥（正常现象）**：微信 4.x 部分库无密钥是**懒加载**——`wx init` 从内存扫描，从未用过的库密钥从未进入内存。实测常缺 `chatbot_message.db` / `third_app_icon.db` / `weclaw.db` 三个边缘库；聊天在 `message_0.db`、媒体在 `media_0.db`、联系人在 `contact.db`，16/17 个密钥已覆盖核心需求，可直接忽略。三重验证证据与补提方法见开发面 `docs/troubleshooting-missing-db-keys.md`（运行时不加载）。

## 5. 项目信息

- 工具：jackwener/wx-cli（https://github.com/jackwener/wx-cli），当前版本 0.3.0，npm 包 `@jackwener/wx-cli`
- 支持平台：macOS (Intel/Apple Silicon) / Windows / Linux
- 支持微信版本：~4.1.8（4.1.10+ 密钥提取可能失败），关闭微信自动更新
- 加密：SQLCipher 4（AES-256-CBC + HMAC-SHA512），密钥内存格式 `x'<64hex_key><32hex_salt>'`
- 配置目录：macOS/Linux `~/.wx-cli/`（config.json + all_keys.json）；Windows `%USERPROFILE%\.wx-cli\`
- 微信数据目录：macOS `~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>/db_storage/`；Windows `文档\WeChat Files\<wxid>\`（微信设置→文件管理查看）
- 微信进程名：macOS `WeChat`；Windows `Weixin.exe`；Linux `wechat`

### 平台差异速查

| 维度 | macOS | Windows（未实测） | Linux（未实测） |
|------|-------|-------------------|----------------|
| 安装 | npm / install.sh | npm / install.ps1（管理员） | npm / install.sh |
| 密钥提取权限 | 关 SIP + sudo | 管理员 PowerShell | root 或 CAP_SYS_PTRACE |
| 是否关 SIP | 需要 | 无此机制 | 无此机制 |
| 微信进程名 | WeChat | Weixin.exe | wechat |
| 数据目录 | `~/Library/Containers/.../db_storage/` | `文档\WeChat Files\<wxid>\` | 依发行版而定 |
| 风控等级 | 只读零风险 | 只读零风险 | 只读零风险 |
| 实测状态 | ✅ Intel Mac + 微信 4.1.8 已通过 | ⚠️ 据官方文档与原理推断 | ⚠️ 未实测 |

> Windows/Linux 部分基于 wx-cli 官方文档与三平台通用加密原理编写，**尚未实机验证**；首次使用请在小号验证密钥提取与数据读取无误后再用于正式环境。
