#!/usr/bin/env bash
# QQ Web Chat one-command installer.
#
#   curl -fsSL https://raw.githubusercontent.com/ljy2231/QQ-web-chat/main/deploy/install.sh | bash
#
# Environment overrides:
#   QQWEBCHAT_IMAGE       image reference (default ghcr.io/ljy2231/qq-web-chat:latest)
#   QQWEBCHAT_MIRROR      registry/mirror prefix, e.g. hub.002230.xyz:44300
#   QQWEBCHAT_DIR         install directory (default /opt/qq-webchat)
#   QQWEBCHAT_PORT        host port (default 4001)
#   QQWEBCHAT_BIND        host bind address (default 127.0.0.1; use 0.0.0.0 behind HTTPS proxy)
#   QQWEBCHAT_SETUP_TOKEN existing first-run token to reuse
#   NAPCAT_ACCESS_TOKEN   NapCat connection token to reuse
#   PUBLIC_BASE_URL       public HTTPS URL (required for file sending via NapCat behind a domain)
#   TRUSTED_PROXIES       comma-separated reverse-proxy CIDRs
set -euo pipefail

IMAGE="${QQWEBCHAT_IMAGE:-ghcr.io/ljy2231/qq-web-chat:latest}"
if [[ -n "${QQWEBCHAT_MIRROR:-}" ]]; then
  IMAGE="${QQWEBCHAT_MIRROR%/}/${IMAGE}"
fi
DIR="${QQWEBCHAT_DIR:-/opt/qq-webchat}"
PORT="${QQWEBCHAT_PORT:-4001}"
BIND="${QQWEBCHAT_BIND:-127.0.0.1}"

say() { printf '%s\n' "$*"; }
fail() { printf '错误: %s\n' "$*" >&2; exit 1; }

say "=== QQ Web Chat 一键安装 ==="

command -v docker >/dev/null 2>&1 || fail "未找到 docker。请先安装 Docker: https://docs.docker.com/engine/install/"
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  fail "未找到 docker compose 插件，请安装 Compose v2。"
fi
docker info >/dev/null 2>&1 || fail "当前用户无权访问 Docker，请用 root 或把用户加入 docker 组。"
command -v curl >/dev/null 2>&1 || fail "需要 curl 做健康检查和初始化提示。"

[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1 && PORT <= 65535 )) || fail "端口无效: $PORT"

if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${PORT}$"; then
  say "警告: 宿主机端口 ${PORT} 似乎已被占用，启动可能失败。可用 QQWEBCHAT_PORT 指定其他端口。"
fi

umask 077
mkdir -p "$DIR" || fail "无法创建安装目录 $DIR；请用 root 运行或先授予目录写权限。"
DIR="$(cd "$DIR" && pwd -P)"
cd "$DIR"

DATA_DIR="$DIR/data"
if ! install -d -o 10001 -g 10001 -m 700 "$DATA_DIR" 2>/dev/null; then
  mkdir -p "$DATA_DIR"
  chown 10001:10001 "$DATA_DIR" 2>/dev/null || fail "无法把数据目录 $DATA_DIR 交给容器用户 10001；请用 root 执行。"
  chmod 700 "$DATA_DIR"
fi

ENV_FILE="$DIR/.env"
file_get() {
  [ -f "$2" ] || return 0
  local line
  line="$(grep -m1 "^$1=" "$2" 2>/dev/null || true)"
  line="${line#*=}"
  line="${line%$'\r'}"
  line="${line#\"}"; line="${line%\"}"
  line="${line#\'}"; line="${line%\'}"
  printf '%s' "$line"
}
env_get() { file_get "$1" "$ENV_FILE"; }

# Explicit environment variables win over an existing .env, so repeat runs can
# change the port or public URL without deleting data.
PORT="${QQWEBCHAT_PORT:-$(env_get WEBCHAT_PORT)}"; PORT="${PORT:-4001}"
BIND="${QQWEBCHAT_BIND:-$(env_get WEBCHAT_BIND)}"; BIND="${BIND:-127.0.0.1}"
SETUP_TOKEN="${QQWEBCHAT_SETUP_TOKEN:-$(env_get SETUP_TOKEN)}"
NAPCAT_TOKEN="${NAPCAT_ACCESS_TOKEN:-$(env_get NAPCAT_ACCESS_TOKEN)}"
PUBLIC_BASE_URL_VALUE="${PUBLIC_BASE_URL:-$(env_get PUBLIC_BASE_URL)}"
WEB_PASSWORD_VALUE="${WEB_PASSWORD:-$(env_get WEB_PASSWORD)}"
WEB_USERNAME_VALUE="${WEB_USERNAME:-$(env_get WEB_USERNAME)}"
ADMIN_QQ_ID_VALUE="${ADMIN_QQ_ID:-$(env_get ADMIN_QQ_ID)}"
TRUSTED_PROXIES_VALUE="${TRUSTED_PROXIES:-$(env_get TRUSTED_PROXIES)}"
DISCLOSE_CLIENT_IP_VALUE="${DISCLOSE_CLIENT_IP:-$(env_get DISCLOSE_CLIENT_IP)}"
DISCLOSE_CLIENT_IP_VALUE="${DISCLOSE_CLIENT_IP_VALUE:-true}"

if [[ -z "$SETUP_TOKEN" ]]; then
  SETUP_TOKEN="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
fi
if [[ -z "$NAPCAT_TOKEN" ]]; then
  NAPCAT_TOKEN="$(file_get NAPCAT_ACCESS_TOKEN /etc/qq-webchat.env 2>/dev/null || true)"
fi
if [[ -z "$NAPCAT_TOKEN" ]]; then
  NAPCAT_TOKEN="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
fi
if [[ -z "$PUBLIC_BASE_URL_VALUE" && "$BIND" == "127.0.0.1" ]]; then
  PUBLIC_BASE_URL_VALUE="http://127.0.0.1:${PORT}"
fi

{
  printf 'WEB_PASSWORD=%s\n' "$WEB_PASSWORD_VALUE"
  printf 'SETUP_TOKEN=%s\n' "$SETUP_TOKEN"
  printf 'WEB_USERNAME=%s\n' "$WEB_USERNAME_VALUE"
  printf 'NAPCAT_ACCESS_TOKEN=%s\n' "$NAPCAT_TOKEN"
  printf 'PUBLIC_BASE_URL=%s\n' "$PUBLIC_BASE_URL_VALUE"
  printf 'ADMIN_QQ_ID=%s\n' "$ADMIN_QQ_ID_VALUE"
  printf 'TRUSTED_PROXIES=%s\n' "$TRUSTED_PROXIES_VALUE"
  printf 'DISCLOSE_CLIENT_IP=%s\n' "$DISCLOSE_CLIENT_IP_VALUE"
  printf 'WEBCHAT_PORT=%s\n' "$PORT"
  printf 'WEBCHAT_BIND=%s\n' "$BIND"
} > "$ENV_FILE"
chmod 600 "$ENV_FILE"

# Regenerated on every run; data and tokens stay in .env and ./data.
cat > "$DIR/compose.yaml" <<EOF
services:
  webchat:
    image: ${IMAGE}
    container_name: qq-webchat
    restart: unless-stopped
    ports:
      - "\${WEBCHAT_BIND}:\${WEBCHAT_PORT}:4001"
    environment:
      WEB_PASSWORD: \${WEB_PASSWORD:-}
      SETUP_TOKEN: \${SETUP_TOKEN:-}
      WEB_USERNAME: \${WEB_USERNAME:-}
      NAPCAT_ACCESS_TOKEN: \${NAPCAT_ACCESS_TOKEN:?}
      PUBLIC_BASE_URL: \${PUBLIC_BASE_URL:-}
      ADMIN_QQ_ID: \${ADMIN_QQ_ID:-}
      TRUSTED_PROXIES: \${TRUSTED_PROXIES:-}
      DISCLOSE_CLIENT_IP: \${DISCLOSE_CLIENT_IP:-true}
      CHAT_DATA_DIR: /data
    volumes:
      - ./data:/data
    read_only: true
    tmpfs:
      - /tmp:size=32m,mode=1777
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    mem_limit: 768m
    pids_limit: 128
    stop_grace_period: 45s
EOF
chmod 644 "$DIR/compose.yaml"

run_dc() { "${DC[@]}" -f "$DIR/compose.yaml" -p qq-webchat "$@"; }

say "拉取镜像 ${IMAGE} ..."
run_dc pull || fail "镜像拉取失败。国内网络可改用 install-cn.sh。"

say "启动容器 ..."
run_dc up -d || fail "容器启动失败，请查看: ${DC[*]} -f $DIR/compose.yaml logs"

PROBE_HOST="$BIND"
case "$BIND" in
  0.0.0.0|"") PROBE_HOST="127.0.0.1" ;;
  ::|::1) PROBE_HOST="[::1]" ;;
esac
PROBE="http://${PROBE_HOST}:${PORT}"

say "等待服务就绪 ..."
READY=0
for _ in $(seq 1 60); do
  if curl -fsS --max-time 2 "${PROBE}/healthz" >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 2
done

if [[ -n "$PUBLIC_BASE_URL_VALUE" ]]; then
  BASE_URL="${PUBLIC_BASE_URL_VALUE%/}"
elif [[ "$BIND" == "127.0.0.1" || "$BIND" == "localhost" ]]; then
  BASE_URL="http://127.0.0.1:${PORT}"
else
  IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  BASE_URL="http://${IP:-127.0.0.1}:${PORT}"
fi

say ""
if (( READY == 1 )); then
  say "✅ QQ Web Chat 已启动"
else
  say "⚠️  服务尚未响应，查看日志: ${DC[*]} -f $DIR/compose.yaml logs -f"
fi

SESSION_JSON="$(curl -sS --max-time 5 "${PROBE}/api/session" 2>/dev/null || true)"
if printf '%s' "$SESSION_JSON" | grep -q '"setup_required": *true'; then
  say "   首次设置 : ${BASE_URL}/web?setup=${SETUP_TOKEN}"
  say "   （一次性链接，设置登录账号和密码后自动登录；请勿分享）"
else
  say "   访问地址 : ${BASE_URL}/web"
  say "   该实例已初始化，请使用之前的账号密码登录。"
fi
say "   安装目录 : ${DIR}"
say "   数据目录 : ${DATA_DIR}"
say "   NapCat令牌: 已写入 ${ENV_FILE}（NAPCAT_ACCESS_TOKEN）"
if [[ "$BIND" == "127.0.0.1" || "$BIND" == "localhost" ]]; then
  say "   安全提示 : 默认仅本机可访问。公网使用请配 HTTPS 反代，并以 QQWEBCHAT_BIND=0.0.0.0 PUBLIC_BASE_URL=https://你的域名 TRUSTED_PROXIES=... 重新执行。"
else
  say "   安全提示 : 当前监听 ${BIND}:${PORT}，请确保前面有 HTTPS 反代和访问控制。"
fi
say ""
say "常用命令："
say "  日志 : ${DC[*]} -f $DIR/compose.yaml -p qq-webchat logs -f"
say "  停止 : ${DC[*]} -f $DIR/compose.yaml -p qq-webchat stop"
say "  更新 : ${DC[*]} -f $DIR/compose.yaml -p qq-webchat pull && ${DC[*]} -f $DIR/compose.yaml -p qq-webchat up -d"
say ""
say "NapCat 需要使用 .env 中的 NAPCAT_ACCESS_TOKEN 才能连接；本脚本不会修改 NapCat。"
say "不要删除 ${DATA_DIR}；聊天数据、附件和登录凭据都在这里。"
