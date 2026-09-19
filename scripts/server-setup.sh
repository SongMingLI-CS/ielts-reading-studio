#!/usr/bin/env bash
# Prepare a checkout for a public deployment: the security environment variables and the
# systemd units (web, worker, HTTPS front).
#
# Why this exists: since the security hardening, `serve --host 0.0.0.0` refuses to start
# when IELTS_WEB_SESSION_SECRET / IELTS_WEB_FORCE_HTTPS / IELTS_WEB_TRUSTED_PROXIES are
# missing, and the web unit now binds 127.0.0.1 behind a Caddy TLS front instead. A deploy
# that restarts without these pieces in place fails and rolls back, so they must exist
# first. This script reports what is missing and, with --apply, fixes it without ever
# printing a secret value.
#
# Usage:
#   scripts/server-setup.sh            # dry-run: report only, change nothing
#   scripts/server-setup.sh --apply    # write the missing variables and install the units
#   scripts/server-setup.sh --apply --no-worker
#   scripts/server-setup.sh --apply --no-tls
#
# Overridable environment: IELTS_ENV_FILE, IELTS_WEB_UNIT, IELTS_WORKER_UNIT, IELTS_TLS_UNIT,
# IELTS_SYSTEMD_DIR, IELTS_CADDY_CONFIG_DIR, IELTS_CADDY_CONFIG_NAME, IELTS_CADDY_DATA_DIR,
# IELTS_TLS_SITE, IELTS_SUDO, IELTS_TRUSTED_PROXIES_DEFAULT
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE_NAME="${IELTS_ENV_FILE:-.env.web}"
ENV_FILE="$APP_DIR/$ENV_FILE_NAME"
WEB_UNIT="${IELTS_WEB_UNIT:-ielts-reading-studio}"
WEB_UNIT_SOURCE="$APP_DIR/deploy/ielts-reading-studio.service"
WORKER_UNIT="${IELTS_WORKER_UNIT:-ielts-reading-studio-worker}"
WORKER_UNIT_SOURCE="$APP_DIR/deploy/ielts-reading-studio-worker.service"
TLS_UNIT="${IELTS_TLS_UNIT:-ielts-reading-studio-tls}"
TLS_UNIT_SOURCE="$APP_DIR/deploy/ielts-reading-studio-tls.service"
CADDYFILE_SOURCE="$APP_DIR/deploy/Caddyfile"
CADDY_CONFIG_DIR="${IELTS_CADDY_CONFIG_DIR:-/etc/caddy}"
CADDY_CONFIG_NAME="${IELTS_CADDY_CONFIG_NAME:-ielts-reading-studio.caddyfile}"
CADDY_DATA_DIR="${IELTS_CADDY_DATA_DIR:-/var/lib/caddy-ielts-reading-studio}"
#: How users reach the site. A bare IP is the normal case here: unregistered domains are
#: blocked by SNI in this region, and the internal CA issues a SAN=IP certificate.
TLS_SITE="${IELTS_TLS_SITE:-https://192.144.160.196:8766}"
SUDO="${IELTS_SUDO:-sudo}"
SYSTEMD_DIR="${IELTS_SYSTEMD_DIR:-/etc/systemd/system}"
TRUSTED_PROXIES_DEFAULT="${IELTS_TRUSTED_PROXIES_DEFAULT:-127.0.0.1}"

APPLY=0
WITH_WORKER=1
WITH_TLS=1
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

  --apply       写入缺失的安全环境变量并安装 systemd 单元（默认只报告）
  --no-worker   不检查/安装 worker systemd 服务
  --no-tls      不检查/安装 HTTPS 入口（Caddy）单元
  -h, --help    显示本帮助

退出码: 0 前置条件满足（或已修复为满足）；1 仍有需要人工处理的项。
EOF
}

parse_args() {
  while (($#)); do
    case "$1" in
      --apply) APPLY=1 ;;
      --no-worker) WITH_WORKER=0 ;;
      --no-tls) WITH_TLS=0 ;;
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
  unit_installed "$WORKER_UNIT"
}

# Where systemd finds a unit, or non-zero when it is not installed at all.
unit_path() {
  local name="$1" candidate
  for candidate in \
    "$SYSTEMD_DIR/${name}.service" \
    "/etc/systemd/system/${name}.service" \
    "/lib/systemd/system/${name}.service" \
    "/usr/lib/systemd/system/${name}.service"; do
    if [[ -f "$candidate" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

unit_installed() { unit_path "$1" >/dev/null; }

# Print the command install_file would run, so --dry-run shows the real invocation.
install_file_preview() {
  local dir
  dir="$(dirname "$2")"
  if [[ -w "$dir" ]]; then
    printf '  [dry-run] install -m 644 %s %s\n' "$1" "$2"
  else
    printf '  [dry-run] %s install -m 644 %s %s\n' "$SUDO" "$1" "$2"
  fi
}

# Write a file, using sudo only when the target directory is not writable: a checkout under
# the operator's home needs no privilege, /etc and /var/lib do.
install_file() {
  local src="$1" dst="$2" dir
  dir="$(dirname "$dst")"
  if [[ -w "$dir" ]]; then
    install -m 644 "$src" "$dst"
  else
    "$SUDO" install -m 644 "$src" "$dst"
  fi
}

# The TLS front proxies to the port the web unit binds. Drift between the two files means
# the proxy answers 502 for everything while the application looks perfectly healthy.
caddy_upstream_port() {
  grep -oE 'reverse_proxy[[:space:]]+127\.0\.0\.1:[0-9]+' "$CADDYFILE_SOURCE" 2>/dev/null |
    head -1 | grep -oE '[0-9]+$' || true
}

web_unit_port() {
  grep -oE -- '--port [0-9]+' "$WEB_UNIT_SOURCE" 2>/dev/null |
    head -1 | grep -oE '[0-9]+$' || true
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
    install_file_preview "$WORKER_UNIT_SOURCE" "$SYSTEMD_DIR/${WORKER_UNIT}.service"
    printf '  [dry-run] %s systemctl daemon-reload\n' "$SUDO"
    printf '  [dry-run] %s systemctl enable --now %s\n' "$SUDO" "$WORKER_UNIT"
    return 0
  fi
  log "安装 worker 服务 $WORKER_UNIT"
  install_file "$WORKER_UNIT_SOURCE" "$SYSTEMD_DIR/${WORKER_UNIT}.service"
  "$SUDO" systemctl daemon-reload
  "$SUDO" systemctl enable --now "$WORKER_UNIT"
  return 0
}

# The web unit decides the port the TLS front proxies to, and it must not bind publicly
# once Caddy owns the public port.
check_web_unit() {
  log "检查 web 服务 $WEB_UNIT"
  if [[ ! -f "$WEB_UNIT_SOURCE" ]]; then
    warn "缺少 $WEB_UNIT_SOURCE"
    FAILURES=1
    return 0
  fi
  local expected installed=""
  expected="$(grep -m1 '^ExecStart=' "$WEB_UNIT_SOURCE" | cut -c1-160)"
  installed="$(unit_path "$WEB_UNIT")" || installed=""
  if [[ -z "$installed" ]]; then
    printf '未安装: %s\n' "$WEB_UNIT"
    printf '  将安装: %s\n' "$expected"
    return 0
  fi
  printf '已安装: %s\n' "$installed"
  if diff -q "$WEB_UNIT_SOURCE" "$installed" >/dev/null 2>&1; then
    printf '  与本仓库版本一致\n'
    return 0
  fi
  printf '  与本仓库版本不同\n'
  printf '  本仓库: %s\n' "$expected"
  printf '  已安装: %s\n' "$(grep -m1 '^ExecStart=' "$installed" | cut -c1-160)"
  printf '  --apply 会覆盖为仓库版本（旧版本监听 0.0.0.0，会与 HTTPS 入口抢端口）\n'
  return 0
}

install_web_unit() {
  if [[ ! -f "$WEB_UNIT_SOURCE" ]]; then
    return 0
  fi
  local installed=""
  installed="$(unit_path "$WEB_UNIT")" || installed=""
  if [[ -n "$installed" ]] && diff -q "$WEB_UNIT_SOURCE" "$installed" >/dev/null 2>&1; then
    log "web 服务已是最新（${WEB_UNIT}），跳过"
    return 0
  fi
  if ((APPLY == 0)); then
    install_file_preview "$WEB_UNIT_SOURCE" "$SYSTEMD_DIR/${WEB_UNIT}.service"
    printf '  [dry-run] %s systemctl daemon-reload\n' "$SUDO"
    return 0
  fi
  log "安装/更新 web 服务 $WEB_UNIT"
  install_file "$WEB_UNIT_SOURCE" "$SYSTEMD_DIR/${WEB_UNIT}.service"
  "$SUDO" systemctl daemon-reload
  return 0
}

check_tls_unit() {
  log "检查 HTTPS 入口 $TLS_UNIT"
  if [[ ! -f "$TLS_UNIT_SOURCE" ]]; then
    warn "缺少 $TLS_UNIT_SOURCE"
    FAILURES=1
    return 0
  fi
  if [[ ! -f "$CADDYFILE_SOURCE" ]]; then
    warn "缺少 $CADDYFILE_SOURCE"
    FAILURES=1
    return 0
  fi
  if ! command -v caddy >/dev/null 2>&1; then
    warn '未安装 caddy，HTTPS 入口无法启动（用户只能访问明文端口）'
    FAILURES=1
  fi
  local upstream unit_port
  upstream="$(caddy_upstream_port)"
  unit_port="$(web_unit_port)"
  if [[ -n "$upstream" && -n "$unit_port" && "$upstream" != "$unit_port" ]]; then
    warn "Caddy 上游端口（${upstream}）与 web 单元的 --port（${unit_port}）不一致"
    FAILURES=1
  elif [[ -n "$upstream" ]]; then
    printf '上游端口: 127.0.0.1:%s（与 web 单元一致）\n' "$upstream"
  fi
  if unit_installed "$TLS_UNIT"; then
    printf '已安装: %s\n' "$TLS_UNIT"
  else
    printf '未安装: %s（未安装时用户只能用明文 http，新版本会按 HTTPS 策略拒绝明文请求）\n' "$TLS_UNIT"
  fi
  printf '站点地址: %s\n' "$TLS_SITE"
  printf '配置文件: %s/%s\n' "$CADDY_CONFIG_DIR" "$CADDY_CONFIG_NAME"
  return 0
}

install_tls_unit() {
  if ((WITH_TLS == 0)); then
    log '按要求跳过 HTTPS 入口'
    return 0
  fi
  if [[ ! -f "$TLS_UNIT_SOURCE" || ! -f "$CADDYFILE_SOURCE" ]]; then
    return 0
  fi
  local config_path="$CADDY_CONFIG_DIR/$CADDY_CONFIG_NAME"
  if unit_installed "$TLS_UNIT" && [[ -f "$config_path" ]]; then
    log "HTTPS 入口已安装（${TLS_UNIT}），跳过"
    return 0
  fi
  if ((APPLY == 0)); then
    printf '  [dry-run] 渲染 %s → %s（站点地址 %s）\n' "$CADDYFILE_SOURCE" "$config_path" "$TLS_SITE"
    install_file_preview "$TLS_UNIT_SOURCE" "$SYSTEMD_DIR/${TLS_UNIT}.service"
    printf '  [dry-run] %s install -d -o caddy -g caddy -m 750 %s\n' "$SUDO" "$CADDY_DATA_DIR"
    printf '  [dry-run] %s systemctl daemon-reload\n' "$SUDO"
    printf '  [dry-run] %s systemctl enable --now %s\n' "$SUDO" "$TLS_UNIT"
    return 0
  fi
  log "安装 HTTPS 入口 $TLS_UNIT"
  local tmp
  tmp="$(mktemp)"
  sed "s|{{SITE_ADDRESS}}|${TLS_SITE}|g" "$CADDYFILE_SOURCE" >"$tmp"
  if [[ ! -d "$CADDY_CONFIG_DIR" ]]; then
    "$SUDO" install -d -m 755 "$CADDY_CONFIG_DIR"
  fi
  install_file "$tmp" "$config_path"
  rm -f "$tmp"
  if id -u caddy >/dev/null 2>&1; then
    "$SUDO" install -d -o caddy -g caddy -m 750 "$CADDY_DATA_DIR" ||
      warn "创建 $CADDY_DATA_DIR 失败，请确认 caddy 用户对该目录可写"
  fi
  install_file "$TLS_UNIT_SOURCE" "$SYSTEMD_DIR/${TLS_UNIT}.service"
  "$SUDO" systemctl daemon-reload
  "$SUDO" systemctl enable --now "$TLS_UNIT"
  return 0
}

next_steps() {
  log '下一步'
  cat <<EOF
  1. scripts/deploy.sh --check    # 只读预检（工作树、venv、公网闸门、schema 版本）
  2. scripts/deploy.sh --no-pull  # 离线取代码后：快照 → 依赖 → 迁移 0003 → pytest → 重启 web+worker
  3. curl -fsS http://127.0.0.1:8768/healthz          # 应用（回环，明文即可）
  4. 用户访问 $TLS_SITE                  # Caddy 自签证书，浏览器需点一次「继续前往」
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
  check_web_unit
  install_web_unit
  if ((WITH_WORKER == 1)); then
    check_worker_unit
    install_worker_unit
  fi
  if ((WITH_TLS == 1)); then
    check_tls_unit
    install_tls_unit
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


