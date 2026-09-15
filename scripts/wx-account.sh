#!/bin/bash
# wx-account.sh — 微信多账号切换工具
# 用法:
#   wx-account list           列出所有已配置账号
#   wx-account use <name>     切换到指定账号（main/alt 或自定义名）
#   wx-account current        显示当前使用的账号
#   wx-account init <name>    对指定账号执行 wx init --force（提取密钥）
#
# 账号配置目录: ~/.wx-cli/accounts/<name>/
#   - config.json   数据库路径等配置
#   - all_keys.json 数据库密钥

set -e

WX_CLI_DIR="$HOME/.wx-cli"
ACCOUNTS_DIR="$WX_CLI_DIR/accounts"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

usage() {
    echo "微信多账号切换工具"
    echo ""
    echo "用法:"
    echo "  $0 list              列出所有已配置账号"
    echo "  $0 use <name>        切换到指定账号"
    echo "  $0 current           显示当前使用的账号"
    echo "  $0 init <name>       对指定账号执行 wx init --force（需微信在运行）"
    echo ""
    echo "已配置账号:"
    list_accounts
}

list_accounts() {
    if [ ! -d "$ACCOUNTS_DIR" ]; then
        echo "  （无已配置账号）"
        return
    fi
    local current=$(get_current_account)
    for dir in "$ACCOUNTS_DIR"/*/; do
        [ -d "$dir" ] || continue
        local name=$(basename "$dir")
        local config="$dir/config.json"
        local keys="$dir/all_keys.json"
        local marker=""
        [ "$name" = "$current" ] && marker=" ${GREEN}← 当前${NC}"
        if [ -f "$config" ]; then
            local db_dir=$(python3 -c "import json;print(json.load(open('$config')).get('db_dir','?'))" 2>/dev/null)
            local key_count="?"
            [ -f "$keys" ] && key_count=$(python3 -c "import json;print(len(json.load(open('$keys'))))" 2>/dev/null)
            echo -e "  ${YELLOW}$name${NC} (密钥: $key_count 个)$marker"
            echo "    db_dir: $db_dir"
        else
            echo -e "  ${YELLOW}$name${NC} (无配置)$marker"
        fi
    done
}

get_current_account() {
    # 通过对比当前 config.json 的 db_dir 判断当前账号
    local current_config="$WX_CLI_DIR/config.json"
    [ -f "$current_config" ] || { echo "?"; return; }
    local current_db=$(python3 -c "import json;print(json.load(open('$current_config')).get('db_dir',''))" 2>/dev/null)
    for dir in "$ACCOUNTS_DIR"/*/; do
        [ -d "$dir" ] || continue
        local name=$(basename "$dir")
        local config="$dir/config.json"
        [ -f "$config" ] || continue
        local db=$(python3 -c "import json;print(json.load(open('$config')).get('db_dir',''))" 2>/dev/null)
        [ "$db" = "$current_db" ] && { echo "$name"; return; }
    done
    echo "?"
}

use_account() {
    local name="$1"
    local account_dir="$ACCOUNTS_DIR/$name"
    if [ ! -d "$account_dir" ]; then
        echo -e "${RED}错误: 账号 '$name' 不存在${NC}"
        echo "可用账号:"
        list_accounts
        exit 1
    fi
    if [ ! -f "$account_dir/config.json" ]; then
        echo -e "${RED}错误: 账号 '$name' 缺少 config.json${NC}"
        exit 1
    fi
    # 切换配置
    cp "$account_dir/config.json" "$WX_CLI_DIR/config.json"
    # 切换密钥（如果有）
    if [ -f "$account_dir/all_keys.json" ]; then
        cp "$account_dir/all_keys.json" "$WX_CLI_DIR/all_keys.json"
        echo -e "${GREEN}✅ 已切换到账号: $name${NC}"
    else
        echo -e "${YELLOW}⚠️  已切换到账号: $name（但没有密钥文件，需先运行 wx init）${NC}"
    fi
    # 显示当前配置
    local db_dir=$(python3 -c "import json;print(json.load(open('$WX_CLI_DIR/config.json')).get('db_dir','?'))" 2>/dev/null)
    echo "   db_dir: $db_dir"
}

init_account() {
    local name="$1"
    local account_dir="$ACCOUNTS_DIR/$name"
    if [ ! -d "$account_dir" ]; then
        echo -e "${RED}错误: 账号 '$name' 不存在${NC}"
        exit 1
    fi
    echo -e "${YELLOW}⚠️  即将对账号 '$name' 执行 wx init --force${NC}"
    echo "   这会覆盖该账号的密钥文件，请确认对应微信正在运行。"
    echo ""
    # 切换到该账号配置
    use_account "$name"
    echo ""
    echo "执行 wx init --force ..."
    sudo wx init --force
    # 保存密钥到账号目录
    if [ -f "$WX_CLI_DIR/all_keys.json" ]; then
        cp "$WX_CLI_DIR/all_keys.json" "$account_dir/all_keys.json"
        local count=$(python3 -c "import json;print(len(json.load(open('$account_dir/all_keys.json'))))" 2>/dev/null)
        echo -e "${GREEN}✅ 密钥已保存到 $account_dir/all_keys.json（$count 个）${NC}"
    fi
}

# 主逻辑
case "${1:-}" in
    list)
        list_accounts
        ;;
    use)
        if [ -z "$2" ]; then
            echo -e "${RED}错误: 请指定账号名${NC}"
            list_accounts
            exit 1
        fi
        use_account "$2"
        ;;
    current)
        echo -e "当前账号: ${GREEN}$(get_current_account)${NC}"
        ;;
    init)
        if [ -z "$2" ]; then
            echo -e "${RED}错误: 请指定账号名${NC}"
            exit 1
        fi
        init_account "$2"
        ;;
    *)
        usage
        ;;
esac
