# 实时监听与 daemon 运维（wechat-control 参考）

> 启动实时消息监听、排查 daemon 缓存/切换账号后查询不更新时读本文。
> 每日总结、recommend 选群、重要消息推送规则见 [monitoring.md](monitoring.md)。

## 1. 实时消息监听器 realtime-monitor.py

**能力**：
- 启动时自检 daemon 配置（db_dir 是否指向当前登录账号），不对则自动重启 daemon；
- 每 N 秒轮询 `wx new-messages`，daemon 自动检测库更新，**无需每次重启 daemon**；
- 支持全部消息类型：文字、语音、图片、视频、链接、文件、系统消息（撤回等）；
- 新消息打印：发送者、会话、内容、类型、时间、是否群聊；
- 支持重要消息判定（关键词、特定发送者、特定群）、语音消息标记（可选自动转写）；
- 日志 `~/.wx-cli/realtime_monitor.log`。

```bash
# 前台实时监控（10 秒轮询，Ctrl+C 停）
python3 scripts/realtime-monitor.py monitor --interval 10
# 后台常驻
nohup python3 scripts/realtime-monitor.py monitor --interval 10 > /tmp/realtime-monitor.log 2>&1 &
python3 scripts/realtime-monitor.py once                 # 单次检查
python3 scripts/realtime-monitor.py status               # 状态
python3 scripts/realtime-monitor.py config show          # 查看配置
python3 scripts/realtime-monitor.py config set interval_seconds 5
python3 scripts/realtime-monitor.py config add important_keywords "紧急,重要"
python3 scripts/realtime-monitor.py config add important_senders "张三"
python3 scripts/realtime-monitor.py reset                # 重置基线，避免把历史当新消息
python3 scripts/realtime-monitor.py restart-daemon       # 手动重启 daemon
```
状态/配置：`~/.wx-cli/realtime_monitor_state.json`、`realtime_monitor_config.json`、`realtime_monitor.log`。

## 2. wx-cli daemon 机制

- 运行 wx 命令会启动后台 daemon（`/usr/local/bin/wx`），通过 Unix socket 通信；
- daemon 启动时读 `~/.wx-cli/config.json` 的 `db_dir` 并缓存；解密库缓存到 `~/.wx-cli/cache/`；
- **每次查询自动检测加密库是否更新、有更新则重新解密**（收新消息无需重启）。

### 什么时候要重启 daemon

| 场景 | 重启？ | 原因 |
|---|---|---|
| 切换账号 / 改 config.json 的 db_dir（含 wx-account.sh use） | ✅ | daemon 缓存了旧 db_dir，不会自动重读 |
| 正常收新消息 | ❌ | 每次查询自动检测更新并重新解密 |
| 清除解密缓存 | ❌ | daemon 自动管理 |
| wx-cli 二进制更新 | ✅ | 运行中的是旧版本 |
| 密钥失效 / 重新提取 | ✅ | daemon 缓存了旧密钥 |

### 重启标准操作
```bash
# 1. 读并 kill daemon（daemon.pid 是 JSON，不是纯数字）
DAEMON_PID=$(python3 -c "import json;print(json.load(open('$HOME/.wx-cli/daemon.pid'))['pid'])")
kill -9 $DAEMON_PID
# 2. 清残留
rm -f ~/.wx-cli/daemon.sock ~/.wx-cli/daemon.pid
# 3. 切账号时建议清解密缓存（正常使用不必）
rm -f ~/.wx-cli/cache/*.db
# 4. 任意 wx 命令自动重启
wx sessions --limit 1
# 5. 验证读取的 DB_DIR 正确
tail -10 ~/.wx-cli/daemon.log | grep "DB_DIR"
```

### 状态验证
```bash
ps aux | grep "wx" | grep -v grep     # daemon 进程
tail -20 ~/.wx-cli/daemon.log         # daemon 日志
grep "DB_DIR" ~/.wx-cli/daemon.log | tail -1
```
