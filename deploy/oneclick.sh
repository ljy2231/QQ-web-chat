#!/usr/bin/env bash
# QQ Web Chat 无交互一键部署（Docker Compose）。
# 部署完成后打开脚本输出的链接，在网页中设置登录账号和密码。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
ENV_FILE="$ROOT/.env"

die() { printf '错误：%s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

command -v docker >/dev/null 2>&1 || die '未安装 Docker'
docker compose version >/dev/null 2>&1 || die '未安装 Docker Compose v2'
command -v curl >/dev/null 2>&1 || die '需要 curl 做健康检查'
[ -f docker-compose.yml ] || die "未找到 $ROOT/docker-compose.yml"

random_token() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 24
    elif command -v python3 >/dev/null 2>&1; then
        python3 -c 'import secrets; print(secrets.token_hex(24))'
    else
        die '需要 openssl 或 python3 生成随机令牌'
    fi
}

env_value() { # key file
    [ -r "$2" ] || return 0
    sed -n "s/^$1=//p" "$2" | tail -n 1 | sed -e 's/^"//' -e 's/"$//'
}

append_env() { # key value file (only when missing)
    if ! grep -q "^$1=" "$3" 2>/dev/null; then
        printf '%s=%s\n' "$1" "$2" >> "$3"
    fi
}

# 端口和监听地址：显式环境变量优先，其次已有的 .env。
if [ -n "${WEBCHAT_PORT:-}" ]; then
    PORT="$WEBCHAT_PORT"
else
    PORT="$(env_value WEBCHAT_PORT "$ENV_FILE")"; PORT="${PORT:-4001}"
fi
if [ -n "${WEBCHAT_BIND:-}" ]; then
    BIND="$WEBCHAT_BIND"
else
    BIND="$(env_value WEBCHAT_BIND "$ENV_FILE")"; BIND="${BIND:-127.0.0.1}"
fi

# 端口被其他进程占用时尽快失败，避免 compose 给出难懂的报错。
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${PORT}$"; then
    if ! docker compose -p qq-webchat ps --services --filter status=running 2>/dev/null | grep -qx 'webchat'; then
        die "端口 ${PORT} 已被占用。请设置 WEBCHAT_PORT=其他端口 后重试"
    fi
fi

if [ ! -f "$ENV_FILE" ]; then
    NAPCAT_TOKEN="${NAPCAT_ACCESS_TOKEN:-}"
    if [ -z "$NAPCAT_TOKEN" ]; then
        NAPCAT_TOKEN="$(env_value NAPCAT_ACCESS_TOKEN /etc/qq-webchat.env || true)"
    fi
    [ -n "$NAPCAT_TOKEN" ] || NAPCAT_TOKEN="$(random_token)"
    SETUP_TOKEN="${SETUP_TOKEN:-$(random_token)}"
    DEFAULT_PUBLIC_BASE_URL=""
    if [ -z "${PUBLIC_BASE_URL:-}" ] && [ "$BIND" = "127.0.0.1" ]; then
        DEFAULT_PUBLIC_BASE_URL="http://127.0.0.1:${PORT}"
    fi
    PUBLIC_BASE_URL_VALUE="${PUBLIC_BASE_URL:-$DEFAULT_PUBLIC_BASE_URL}"
    umask 077
    cat > "$ENV_FILE" <<EOF
WEB_PASSWORD=
SETUP_TOKEN=${SETUP_TOKEN}
WEB_USERNAME=
NAPCAT_ACCESS_TOKEN=${NAPCAT_TOKEN}
PUBLIC_BASE_URL=${PUBLIC_BASE_URL_VALUE}
ADMIN_QQ_ID=${ADMIN_QQ_ID:-}
TRUSTED_PROXIES=${TRUSTED_PROXIES:-}
DISCLOSE_CLIENT_IP=${DISCLOSE_CLIENT_IP:-true}
WEBCHAT_PORT=${PORT}
WEBCHAT_BIND=${BIND}
EOF
    chmod 600 "$ENV_FILE"
    say "已生成 $ENV_FILE（权限 600）。"
else
    FILE_SETUP_TOKEN="$(env_value SETUP_TOKEN "$ENV_FILE")"
    SETUP_TOKEN="${FILE_SETUP_TOKEN:-${SETUP_TOKEN:-}}"
    if [ -z "$SETUP_TOKEN" ]; then
        SETUP_TOKEN="$(random_token)"
        append_env SETUP_TOKEN "$SETUP_TOKEN" "$ENV_FILE"
    fi
    NAPCAT_TOKEN="$(env_value NAPCAT_ACCESS_TOKEN "$ENV_FILE")"
    if [ -z "$NAPCAT_TOKEN" ]; then NAPCAT_TOKEN="${NAPCAT_ACCESS_TOKEN:-}"; fi
    if [ -z "$NAPCAT_TOKEN" ]; then
        NAPCAT_TOKEN="$(env_value NAPCAT_ACCESS_TOKEN /etc/qq-webchat.env || true)"
        [ -n "$NAPCAT_TOKEN" ] || NAPCAT_TOKEN="$(random_token)"
        append_env NAPCAT_ACCESS_TOKEN "$NAPCAT_TOKEN" "$ENV_FILE"
    fi
    say "复用已有 $ENV_FILE（权限 600）。"
fi

say '正在构建并启动容器（首次可能需要几分钟）...'
docker compose -p qq-webchat up -d --build

say '等待服务就绪...'
ready=0
for _ in $(seq 1 90); do
    if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 2
done
[ "$ready" = 1 ] || die "服务未在预期时间内就绪，请运行：docker compose -p qq-webchat logs --tail=100 webchat"

BASE_URL="$(env_value PUBLIC_BASE_URL "$ENV_FILE")"
BASE_URL="${BASE_URL%/}"
if [ -z "$BASE_URL" ]; then
    if [ "$BIND" = "127.0.0.1" ]; then
        BASE_URL="http://127.0.0.1:${PORT}"
    else
        HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
        BASE_URL="http://${HOST_IP:-127.0.0.1}:${PORT}"
    fi
fi

if curl -sS --max-time 5 "http://127.0.0.1:${PORT}/api/session" | grep -q '"setup_required": *true'; then
    say ''
    say '============================================================'
    say ' QQ Web Chat 已启动。请在浏览器打开下面的初始化链接：'
    say "   ${BASE_URL}/web?setup=${SETUP_TOKEN}"
    say ''
    say ' 打开后设置登录账号和密码，提交后会自动登录。'
    say ' 该链接只能使用一次，请勿分享。'
    say '============================================================'
else
    say ''
    say '============================================================'
    say " QQ Web Chat 已启动：${BASE_URL}/web"
    say ' 该实例已经初始化过，请使用之前的账号密码登录。'
    say ' 如需重置账号，停止容器后删除数据卷中的 credentials.json。'
    say '============================================================'
fi

say ''
say "NapCat 连接令牌保存在 ${ENV_FILE} 的 NAPCAT_ACCESS_TOKEN。"
say '如果 NapCat 尚未使用该令牌，请在 NapCat 配置中填入相同值并重启 NapCat。'
say '通过公网 HTTPS 域名访问时，请在 .env 设置 PUBLIC_BASE_URL 和 TRUSTED_PROXIES 后重启。'
