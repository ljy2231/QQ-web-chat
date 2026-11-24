#!/usr/bin/env bash
# 兼容旧入口：统一调用 deploy/install.sh。
set -euo pipefail
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install.sh" "$@"
