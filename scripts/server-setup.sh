#!/usr/bin/env bash
# Prepare a checkout for a public deployment: security environment variables and the
# worker systemd unit.
#
# Why this exists: since the security hardening, `serve --host 0.0.0.0` refuses to start
# when IELTS_WEB_SESSION_SECRET / IELTS_WEB_FORCE_HTTPS / IELTS_WEB_TRUSTED_PROXIES are
# missing. A deploy that restarts without them fails and rolls back, so the variables must
# be in place first. This script reports what is missing and, with --apply, fixes it
# without ever printing a secret value.
#
# Usage:
#   scripts/server-setup.sh            # dry-run: report only, change nothing
#   scripts/server-setup.sh --apply    # write the missing variables and install the unit
#   scripts/server-setup.sh --apply --no-worker
#
# Overridable environment: IELTS_ENV_FILE, IELTS_WORKER_UNIT, IELTS_SUDO, IELTS_SYSTEMD_DIR,
# IELTS_TRUSTED_PROXIES_DEFAULT
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE_NAME="${IELTS_ENV_FILE:-.env.web}"
ENV_FILE="$APP_DIR/$ENV_FILE_NAME"
WORKER_UNIT="${IELTS_WORKER_UNIT:-ielts-reading-studio-worker}"
WORKER_UNIT_SOURCE="$APP_DIR/deploy/ielts-reading-studio-worker.service"
SUDO="${IELTS_SUDO:-sudo}"
SYSTEMD_DIR="${IELTS_SYSTEMD_DIR:-/etc/systemd/system}"
TRUSTED_PROXIES_DEFAULT="${IELTS_TRUSTED_PROXIES_DEFAULT:-127.0.0.1}"

APPLY=0
WITH_WORKER=1
FAILURES=0

REQUIRED_KEYS=(
  IELTS_WEB_USERNAME
  IELTS_WEB_PASSWORD
  IELTS_WEB_SESSION_SECRET
  IELTS_WEB_FORCE_HTTPS
  IELTS_WEB_TRUSTED_PROXIES
)

log() { printf '\n==> %s\n' "$*"; }
warn() { printf '!! %s\n' "$*" >&2; }
die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit "${2:-1}"
}

usage() {
  cat <<'EOF'
用法: scripts/server-setup.sh [选项]

  --apply       写入缺失的安全环境变量并安装 worker 服务（默认只报告）
  --no-worker   不检查/安装 worker systemd 服务
  -h, --help    显示本帮助

退出码: 0 前置条件满足（或已修复为满足）；1 仍有需要人工处理的项。
EOF
}

parse_args() {
  while (($#)); do
    case "$1" in
      --apply) APPLY=1 ;;
      --no-worker) WITH_WORKER=0 ;;
      -h | --help)
        usage
        exit 0
        ;;
      *) die "未知参数: $1（试试 --help）" ;;
    esac
    shift
  done
  return 0
}

has_key() { grep -qE "^${1}=" "$ENV_FILE" 2>/dev/null; }

generate_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import secrets; print(secrets.token_hex(32))'
  else
    die '需要 openssl 或 python3 来生成会话密钥'
  fi
}

env_file_permissions() {
  stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE" 2>/dev/null || echo ''
}

check_env_file() {
  log "检查 $ENV_FILE_NAME"
  if [[ ! -f "$ENV_FILE" ]]; then
    warn "$ENV_FILE_NAME 不存在；systemd 的 EnvironmentFile 指向它"
    FAILURES=1
    return 0
  fi
  printf '文件:   %s\n' "$ENV_FILE"
  local mode
  mode="$(env_file_permissions)"
  if [[ -n "$mode" && "$mode" != "600" && "$mode" != "400" && "$mode" != "640" ]]; then
    warn "权限是 ${mode}，建议收紧为 600（内含网站口令与会话密钥）"
  else
    printf '权限:   %s\n' "${mode:-未知}"
  fi
  local key
  for key in "${REQUIRED_KEYS[@]}"; do
    if has_key "$key"; then
      printf '  已设置  %s\n' "$key"
    else
      printf '  缺失    %s\n' "$key"
    fi
  done
  return 0
}

# Returns 0 when nothing needed doing, 1 when something is (still) missing.
apply_env_file() {
  local missing=() key
  if [[ -f "$ENV_FILE" ]]; then
    for key in "${REQUIRED_KEYS[@]}"; do
      has_key "$key" || missing+=("$key")
    done
  else
    missing=("${REQUIRED_KEYS[@]}")
  fi
  if ((${#missing[@]} == 0)); then
    log '环境变量已齐备，无需写入'
    return 0
  fi
  log "缺失 ${#missing[@]} 项环境变量"
  if ((APPLY == 0)); then
    printf '  [dry-run] 会追加: %s\n' "${missing[*]}"
    printf '  [dry-run] IELTS_WEB_SESSION_SECRET 会在本机新生成（>=32 字符，不打印）\n'
    printf '  [dry-run] 会先备份为 %s.bak-<时间戳> 并 chmod 600\n' "$ENV_FILE_NAME"
    return 1
  fi
  if [[ -f "$ENV_FILE" ]]; then
    cp -p "$ENV_FILE" "$ENV_FILE.bak-$(date -u +%Y%m%d-%H%M%S)"
  else
    : >"$ENV_FILE"
  fi
  for key in "${missing[@]}"; do
    case "$key" in
      IELTS_WEB_SESSION_SECRET)
        printf 'IELTS_WEB_SESSION_SECRET=%s\n' "$(generate_secret)" >>"$ENV_FILE"
        printf '  已写入  %s（新生成，未显示值）\n' "$key"
        ;;
      IELTS_WEB_FORCE_HTTPS)
        printf 'IELTS_WEB_FORCE_HTTPS=1\n' >>"$ENV_FILE"
        printf '  已写入  %s=1\n' "$key"
        ;;
      IELTS_WEB_TRUSTED_PROXIES)
        printf 'IELTS_WEB_TRUSTED_PROXIES=%s\n' "$TRUSTED_PROXIES_DEFAULT" >>"$ENV_FILE"
        printf '  已写入  %s=%s（代理不在本机时请手改）\n' "$key" "$TRUSTED_PROXIES_DEFAULT"
        ;;
      *)
        printf '# %s=请填写\n' "$key" >>"$ENV_FILE"
        warn "$key 需要你手工填写（脚本不生成账号口令）"
        ;;
    esac
  done
  chmod 600 "$ENV_FILE" 2>/dev/null || warn 'chmod 600 失败，请手工收紧权限'
  return 1
}

verify_env_complete() {
  local key
  for key in "${REQUIRED_KEYS[@]}"; do
    if ! has_key "$key"; then
      warn "$key 仍为空或未填写"
      FAILURES=1
    fi
  done
  return 0
}

worker_unit_installed() {
  # A read-only file check keeps dry-run free of sudo (which would prompt and hang when
  # the script is run non-interactively).
  [[ -f "$SYSTEMD_DIR/${WORKER_UNIT}.service" || -f "/lib/systemd/system/${WORKER_UNIT}.service" ]]
}

check_worker_unit() {
  log "检查 worker 服务 $WORKER_UNIT"
  if [[ ! -f "$WORKER_UNIT_SOURCE" ]]; then
    warn "缺少 $WORKER_UNIT_SOURCE"
    FAILURES=1
    return 0
  fi
  if worker_unit_installed; then
    printf '已安装: %s\n' "$WORKER_UNIT"
    return 0
  fi
  printf '未安装: %s（未安装时生成任务会停在「等待 worker」）\n' "$WORKER_UNIT"
  if grep -q "WorkingDirectory=$APP_DIR" "$WORKER_UNIT_SOURCE"; then
    printf 'unit 的 WorkingDirectory 与本目录一致\n'
  else
    warn "unit 里的 WorkingDirectory 与 $APP_DIR 不一致，安装前需要先改"
    FAILURES=1
  fi
  return 0
}

install_worker_unit() {
  if ((WITH_WORKER == 0)); then
    log '按要求跳过 worker 服务'
    return 0
  fi
  if worker_unit_installed; then
    log "worker 服务已安装（${WORKER_UNIT}），跳过"
    return 0
  fi
  if ((APPLY == 0)); then
    printf '  [dry-run] %s cp %s %s/\n' "$SUDO" "$WORKER_UNIT_SOURCE" "$SYSTEMD_DIR"
    printf '  [dry-run] %s systemctl daemon-reload\n' "$SUDO"
    printf '  [dry-run] %s systemctl enable --now %s\n' "$SUDO" "$WORKER_UNIT"
    return 0
  fi
  log "安装 worker 服务 $WORKER_UNIT"
  "$SUDO" cp "$WORKER_UNIT_SOURCE" "$SYSTEMD_DIR/"
  "$SUDO" systemctl daemon-reload
  "$SUDO" systemctl enable --now "$WORKER_UNIT"
  return 0
}

next_steps() {
  log '下一步'
  cat <<'EOF'
  1. scripts/deploy.sh --check    # 只读预检（工作树、venv、schema 版本）
  2. scripts/deploy.sh            # 快照 → pull → 依赖 → 迁移 0003 → pytest → 重启 web+worker → /healthz
  3. curl -fsS http://127.0.0.1:8766/healthz
  任一步失败会自动回滚代码与数据库快照，服务不会停在半升级状态。
EOF
}

main() {
  parse_args "$@"
  log '部署前置检查'
  printf 'app:    %s\n' "$APP_DIR"
  if ((APPLY == 1)); then
    printf '模式:   --apply（会写入环境变量并安装服务）\n'
  else
    printf '模式:   dry-run（只报告，不修改任何文件）\n'
  fi

  check_env_file
  apply_env_file || true
  if ((APPLY == 1)); then
    verify_env_complete
  fi
  if ((WITH_WORKER == 1)); then
    check_worker_unit
    install_worker_unit
  fi
  next_steps

  if ((FAILURES)); then
    warn '仍有需要人工处理的项（见上面的 !! 行）'
    return 1
  fi
  log '前置检查通过'
  return 0
}

main "$@"


