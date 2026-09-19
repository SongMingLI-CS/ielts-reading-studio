#!/usr/bin/env bash
# Reliable, reversible deployment for IELTS Learning Studio.
#
#   scripts/deploy.sh --check      # read-only preflight, exits non-zero when not deployable
#   scripts/deploy.sh --dry-run    # print every mutating step, change nothing
#   scripts/deploy.sh              # pull, sync deps, migrate, verify, restart, health-check
#
# The script never prints secrets: configuration files are only tested for existence,
# and the service log tail is passed through a redaction filter.
#
# Two checks exist because this topology can hide failures:
#   * the preflight re-runs the application's own public-deployment gate (credentials,
#     session secret, HTTPS declaration, trusted proxies). The shipped unit binds
#     127.0.0.1 behind the Caddy TLS front, so `serve` never evaluates that gate, and a
#     missing IELTS_WEB_SESSION_SECRET would silently fall back to the development key;
#   * the post-restart check requests "/" with the proxy's semantics
#     (X-Forwarded-Proto from a trusted address), because /healthz is public and still
#     answers 200 when every other request is rejected with 403 https_required.
#
# Overridable environment:
#   IELTS_SERVICE, IELTS_WORKER_SERVICE, IELTS_HEALTH_URL, IELTS_BRANCH, IELTS_CONFIG,
#   IELTS_ENV_FILE, IELTS_BACKUP_DIR, IELTS_SUDO, IELTS_PYTHON, IELTS_EXTRAS,
#   IELTS_KEEP_SNAPSHOTS, IELTS_SKIP_PULL, IELTS_SKIP_TESTS
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="${IELTS_SERVICE:-ielts-reading-studio}"
WORKER_SERVICE="${IELTS_WORKER_SERVICE:-ielts-reading-studio-worker}"
HEALTH_URL="${IELTS_HEALTH_URL:-}"
#: Used when the service unit cannot be inspected (no systemd, or a hand-run server).
#: Matches deploy/ielts-reading-studio.service: loopback behind the Caddy TLS front.
APP_PORT_FALLBACK="${IELTS_APP_PORT:-8768}"
BRANCH="${IELTS_BRANCH:-main}"
CONFIG_FILE="${IELTS_CONFIG:-config.yaml}"
ENV_FILE="${IELTS_ENV_FILE:-.env.web}"
BACKUP_DIR="${IELTS_BACKUP_DIR:-$APP_DIR/backups}"
SUDO="${IELTS_SUDO:-sudo}"
VENV_PY="${IELTS_PYTHON:-$APP_DIR/.venv/bin/python}"
HEALTH_ATTEMPTS="${IELTS_HEALTH_ATTEMPTS:-20}"
HEALTH_DELAY="${IELTS_HEALTH_DELAY:-1}"
KEEP_SNAPSHOTS="${IELTS_KEEP_SNAPSHOTS:-10}"

MODE="deploy"
DRY_RUN=0
DO_PULL=1
DO_TESTS=1
SNAPSHOT=""
BEFORE_COMMIT=""
AFTER_COMMIT=""
MIGRATED=0
DB_PATH=""
DB_REVISION_BEFORE="none"

log() { printf '\n==> %s\n' "$*"; }
warn() { printf '!! %s\n' "$*" >&2; }
die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit "${2:-1}"
}

# Run a mutating command, or print it in --dry-run mode.
run() {
  if ((DRY_RUN)); then
    printf '[dry-run] %s\n' "$*"
    return 0
  fi
  "$@"
}

usage() {
  cat <<'EOF'
用法: scripts/deploy.sh [选项]

  --check        只做部署前检查（只读），不拉取、不迁移、不重启
  --dry-run      打印所有会修改状态的步骤，但不执行
  --no-pull      跳过 git pull（用于离线 bundle 或手工更新后重启）
  --skip-tests   跳过部署后的离线测试
  -h, --help     显示本帮助

退出码: 0 成功；1 部署前检查失败；2 部署失败且已尝试回滚。
EOF
}

parse_args() {
  while (($#)); do
    case "$1" in
      --check) MODE="check" ;;
      --dry-run) DRY_RUN=1 ;;
      --no-pull) DO_PULL=0 ;;
      --skip-tests) DO_TESTS=0 ;;
      -h | --help)
        usage
        exit 0
        ;;
      *) die "未知参数: $1（试试 --help）" ;;
    esac
    shift
  done
  [[ "${IELTS_SKIP_PULL:-0}" == "1" ]] && DO_PULL=0
  [[ "${IELTS_SKIP_TESTS:-0}" == "1" ]] && DO_TESTS=0
  return 0
}

# Print "<revision>\t<database path>" for the configured database.
database_info() {
  if [[ ! -x "$VENV_PY" || ! -f "$APP_DIR/$CONFIG_FILE" ]]; then
    printf 'none\t\n'
    return 0
  fi
  (cd "$APP_DIR" && "$VENV_PY" - "$CONFIG_FILE" <<'PY'
import sys
from pathlib import Path

from app.config import AppConfig
from app.storage.migrations import schema_status_for_path

settings = AppConfig.load(Path(sys.argv[1]))
current, _head = schema_status_for_path(settings.database_path)
print(f"{current or 'none'}\t{settings.database_path}")
PY
  ) 2>/dev/null || printf 'none\t\n'
}

# The address the post-restart checks talk to. Derived from the installed unit so a port
# change cannot silently leave the check pointing at the wrong port, unless overridden.
resolve_health_url() {
  if [[ -n "$HEALTH_URL" ]]; then
    return 0
  fi
  local port=""
  if command -v systemctl >/dev/null 2>&1; then
    port="$(systemctl cat "$SERVICE" 2>/dev/null | grep -oE -- '--port [0-9]+' | head -1 | grep -oE '[0-9]+$')" || port=""
  fi
  HEALTH_URL="http://127.0.0.1:${port:-$APP_PORT_FALLBACK}/healthz"
}

# Run the application's own public-deployment gate. `serve` only evaluates it for a
# non-loopback bind; behind the TLS front the bind is loopback, so the same rules have to
# be enforced here or a weak/missing secret would go unnoticed. Values come from the
# EnvironmentFile that systemd passes to the service, never from the caller's shell.
public_config_issues() {
  if [[ ! -x "$VENV_PY" || ! -f "$APP_DIR/$CONFIG_FILE" || ! -f "$APP_DIR/$ENV_FILE" ]]; then
    printf '缺少 %s 或 %s，无法评估公网配置\n' "$CONFIG_FILE" "$ENV_FILE"
    return 1
  fi
  (
    cd "$APP_DIR" || exit 1
    set -a
    # shellcheck disable=SC1090
    . "$APP_DIR/$ENV_FILE" >/dev/null 2>&1 || true
    set +a
    # Issue messages only name variables; production_issues never returns a value.
    "$VENV_PY" - "$CONFIG_FILE" <<'PY'
import sys
from pathlib import Path

from app.config import AppConfig

try:
    settings = AppConfig.load(Path(sys.argv[1]))
except Exception as exc:  # noqa: BLE001 - the message is shown to the operator
    print(f"无法读取配置: {exc}")
    raise SystemExit(1)
for issue in settings.production_issues(host="0.0.0.0"):
    print(issue)
PY
  ) 2>/dev/null
}

# Strip anything that looks like a credential before printing service logs.
redact() {
  sed -E \
    -e 's/(sk-[A-Za-z0-9_-]{6,})/[REDACTED]/g' \
    -e 's/((?i)(api[_-]?key|password|token)[":= ]+)[^",; ]+/\1[REDACTED]/g'
}


# Read-only preflight. Returns 0 only when the checkout is deployable.
preflight() {
  local failures=0
  log "部署前检查"
  printf 'app:        %s\n' "$APP_DIR"
  printf 'config:     %s\n' "$CONFIG_FILE"
  printf 'service:    %s\n' "$SERVICE"

  if ! command -v git >/dev/null 2>&1; then
    warn 'git 未安装'
    failures=1
  elif ! git -C "$APP_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    warn "$APP_DIR 不是 git 检出"
    failures=1
  else
    BEFORE_COMMIT="$(git -C "$APP_DIR" rev-parse HEAD)"
    printf 'commit:     %s %s\n' "${BEFORE_COMMIT:0:7}" "$(git -C "$APP_DIR" log -1 --pretty=%s)"
    local dirty
    dirty="$(git -C "$APP_DIR" status --porcelain --untracked-files=no)"
    if [[ -n "$dirty" ]]; then
      warn '工作树存在未提交的改动，拒绝部署（数据目录在 .gitignore 中，不受影响）'
      printf '%s\n' "$dirty" >&2
      failures=1
    fi
  fi

  if [[ ! -x "$VENV_PY" ]]; then
    warn "虚拟环境缺失: ${VENV_PY}（先运行 uv sync --extra dev）"
    failures=1
  fi

  if ! command -v curl >/dev/null 2>&1; then
    warn 'curl 未安装，无法执行 HTTP 健康检查'
    failures=1
  fi

  if [[ ! -f "$APP_DIR/$CONFIG_FILE" ]]; then
    warn "配置文件缺失: $APP_DIR/$CONFIG_FILE"
    failures=1
  fi

  if [[ ! -f "$APP_DIR/$ENV_FILE" ]]; then
    warn "EnvironmentFile 缺失: $APP_DIR/${ENV_FILE}（systemd 依赖它提供网站凭据）"
    failures=1
  else
    # The helper exits 0 with an empty stdout when the configuration is safe, so an
    # empty result - not the exit status - is what "passes" here.
    local issues="" evaluated=1
    if ! issues="$(public_config_issues)"; then
      evaluated=0
    fi
    if ((evaluated == 0)); then
      warn "无法评估公网配置：${issues:-未知错误}"
      failures=1
    elif [[ -n "$issues" ]]; then
      warn '安全配置不满足公网部署要求（与 serve 的启动闸门一致）:'
      printf '%s\n' "$issues" | while IFS= read -r line; do
        [[ -n "$line" ]] && printf '!!   - %s\n' "$line" >&2
      done
      failures=1
    else
      printf '安全配置:   满足公网闸门（凭据、会话密钥、HTTPS、可信代理）\n'
    fi
  fi

  printf 'health:     %s\n' "$HEALTH_URL"

  local dir
  for dir in input output; do
    if [[ -d "$APP_DIR/$dir" ]]; then
      [[ -r "$APP_DIR/$dir" ]] || warn "$dir/ 不可读（应用需要读取源文件与产物）"
      [[ -w "$APP_DIR/$dir" ]] || {
        warn "$dir/ 不可写（应用需要写入队列状态与产物）"
        failures=1
      }
    else
      warn "$dir/ 不存在；首次部署时可忽略"
    fi
  done

  local available_kb
  available_kb="$(df -Pk "$APP_DIR" | awk 'NR==2 {print $4}')"
  if [[ -n "${available_kb:-}" ]] && ((available_kb < 204800)); then
    warn "可用磁盘空间不足 200MB（当前 ${available_kb}KB），迁移和备份可能失败"
  fi

  if [[ ! -d "$APP_DIR/.git" ]]; then
    warn '这不是标准 git 工作区，--no-pull 之外的部署步骤仍可继续'
  fi

  local info
  info="$(database_info)"
  DB_REVISION_BEFORE="${info%%$'\t'*}"
  DB_PATH="${info#*$'\t'}"
  printf 'schema:     %s\n' "$DB_REVISION_BEFORE"
  printf 'database:   %s\n' "${DB_PATH:-未知}"

  if [[ -x "$VENV_PY" && -f "$APP_DIR/$CONFIG_FILE" ]]; then
    local pending
    if (cd "$APP_DIR" && "$VENV_PY" -m app.cli migrate --check --config "$CONFIG_FILE" >/dev/null 2>&1); then
      printf '迁移状态:   已是最新\n'
    else
      pending="$(cd "$APP_DIR" && "$VENV_PY" -m app.cli migrate --check --config "$CONFIG_FILE" 2>&1 | tail -1)"
      printf '迁移状态:   待迁移（%s）\n' "${pending:-版本落后}"
    fi
  fi

  if ((failures)); then
    warn '部署前检查未通过，未做任何修改'
    return 1
  fi
  printf '部署前检查通过\n'
  return 0
}

# Consistent SQLite snapshot: taken before the pull so a rollback can restore data.
snapshot_step() {
  local stamp
  stamp="$(date -u +%Y%m%d-%H%M%S)"
  SNAPSHOT="$BACKUP_DIR/deploy-$stamp.db"
  log "快照数据库 → $SNAPSHOT"
  if ((DRY_RUN)); then
    printf '[dry-run] %s -m app.cli snapshot --target %s\n' "$VENV_PY" "$SNAPSHOT"
    return 0
  fi
  mkdir -p "$BACKUP_DIR"
  if ! (cd "$APP_DIR" && "$VENV_PY" -m app.cli snapshot --target "$SNAPSHOT" --config "$CONFIG_FILE"); then
    warn '数据库快照失败；继续部署会失去可回滚的数据副本'
    return 1
  fi
  prune_snapshots
  return 0
}

# Keep the newest snapshots only; the freshly created one is never pruned.
prune_snapshots() {
  local keep="${1:-$KEEP_SNAPSHOTS}" old
  [[ "$keep" =~ ^[0-9]+$ ]] || keep="$KEEP_SNAPSHOTS"
  ls -1t "$BACKUP_DIR"/deploy-*.db 2>/dev/null | tail -n "+$((keep + 1))" | while read -r old; do
    rm -f "$old"
  done
  return 0
}

# Pull the newest revision. --ff-only keeps a diverged checkout from being merged blindly.
pull_step() {
  if ((DO_PULL == 0)); then
    log '按要求跳过 git pull（--no-pull）'
    return 0
  fi
  log "拉取 ${BRANCH}（git pull --ff-only）"
  if ! run git -C "$APP_DIR" pull --ff-only origin "$BRANCH"; then
    warn 'git pull 失败：可能是本地提交分叉或远端不可达'
    return 1
  fi
  AFTER_COMMIT="$(git -C "$APP_DIR" rev-parse HEAD)"
  printf 'after:      %s %s\n' "${AFTER_COMMIT:0:7}" "$(git -C "$APP_DIR" log -1 --pretty=%s)"
  return 0
}

# Install exactly the locked dependencies; fall back to pip when uv is unavailable.
sync_dependencies() {
  log '同步依赖'
  # IELTS_EXTRAS: unset -> dev tools, explicitly empty -> runtime packages only,
  # any other value -> that extra. Locked installs go through uv when available.
  local extras="${IELTS_EXTRAS-dev}" target
  if [[ -f "$APP_DIR/uv.lock" ]] && command -v uv >/dev/null 2>&1; then
    if [[ -n "$extras" ]]; then
      run uv sync --frozen --extra "$extras" --directory "$APP_DIR"
    else
      run uv sync --frozen --directory "$APP_DIR"
    fi
    return $?
  fi
  target="$APP_DIR"
  if [[ -n "$extras" ]]; then
    target="${APP_DIR}[${extras}]"
  fi
  warn "uv 或 uv.lock 缺失，回退到 pip install -e（版本不做精确锁定）: $target"
  run "$VENV_PY" -m pip install -e "$target"
  return $?
}

# Migrations must succeed before the service is restarted.
apply_migrations() {
  log '执行数据库迁移'
  if ! run "$VENV_PY" -m app.cli migrate --config "$CONFIG_FILE"; then
    warn '数据库迁移失败'
    return 1
  fi
  MIGRATED=1
  return 0
}

# Fast but real offline verification of the freshly deployed code.
quick_verification() {
  if ((DO_TESTS == 0)); then
    log '按要求跳过部署后测试（--skip-tests）'
    return 0
  fi
  log '运行离线验证（pytest，无网络、无 API Key）'
  if ! run "$VENV_PY" -m pytest -q; then
    warn '部署后测试失败'
    return 1
  fi
  return 0
}

restart_service() {
  log "重启服务 $SERVICE"
  run "$SUDO" systemctl restart "$SERVICE"
}

# The worker owns the long generations; a deploy that only restarts the web tier would
# leave stale code running the queue.
restart_worker_service() {
  if ((DRY_RUN)); then
    printf '[dry-run] %s systemctl restart %s\n' "$SUDO" "$WORKER_SERVICE"
    return 0
  fi
  if ! "$SUDO" systemctl cat "$WORKER_SERVICE" >/dev/null 2>&1; then
    log "未安装 worker 服务（${WORKER_SERVICE}），跳过重启"
    return 0
  fi
  log "重启 worker 服务 $WORKER_SERVICE"
  run "$SUDO" systemctl restart "$WORKER_SERVICE"
}

# A real HTTP request, not `systemctl is-active`: the process can be alive and broken.
# /healthz alone is not enough - it is exempt from the HTTPS rule, so it keeps answering
# 200 while every other request is rejected - hence the second, proxy-shaped check.
wait_for_health() {
  if ((DRY_RUN)); then
    printf '[dry-run] 健康检查 %s（最多 %s 次）\n' "$HEALTH_URL" "$HEALTH_ATTEMPTS"
    printf '[dry-run] 代理语义检查 %s（带 X-Forwarded-Proto: https，要求非 403）\n' "${HEALTH_URL%/healthz}"
    return 0
  fi
  local attempt healthy=0
  for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
    if curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
      log "健康检查通过（第 $attempt 次）: $HEALTH_URL"
      healthy=1
      break
    fi
    sleep "$HEALTH_DELAY"
  done
  if ((healthy == 0)); then
    warn "健康检查失败（已尝试 $HEALTH_ATTEMPTS 次）: $HEALTH_URL"
    return 1
  fi
  proxied_root_check
}

# Ask for the root page the way the TLS front does: a loopback peer (which must be in
# IELTS_WEB_TRUSTED_PROXIES) plus X-Forwarded-Proto: https. A 403 here means the app does
# not consider proxied traffic secure - every page would be unreachable even though
# /healthz says the service is up.
proxied_root_check() {
  local root="${HEALTH_URL%/healthz}" code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
    -H 'X-Forwarded-Proto: https' "$root" 2>/dev/null || true)"
  if [[ "$code" == "403" ]]; then
    warn "$root 返回 403：HTTPS 判定失败，请确认 IELTS_WEB_TRUSTED_PROXIES 包含代理地址（127.0.0.1）"
    return 1
  fi
  if [[ ! "$code" =~ ^[1-5][0-9][0-9]$ || "$code" == "000" ]]; then
    warn "代理语义检查失败：$root 无有效响应（curl 状态 ${code:-无}）"
    return 1
  fi
  log "代理语义检查通过: $root -> HTTP $code"
  return 0
}

diagnose() {
  warn '服务最近日志（已脱敏）：'
  "$SUDO" journalctl -u "$SERVICE" -n 30 --no-pager 2>&1 | redact || true
}

# Roll back code and data, then bring the previous release back up.
rollback() {
  local reason="$1"
  warn "部署失败，开始回滚：$reason"
  if ((DRY_RUN)); then
    printf '[dry-run] 回滚到 %s，恢复快照 %s\n' "${BEFORE_COMMIT:-未知}" "${SNAPSHOT:-无}"
    return 1
  fi
  "$SUDO" systemctl stop "$SERVICE" || warn '停止服务失败，继续尝试恢复'
  if [[ -n "$BEFORE_COMMIT" ]]; then
    if ! git -C "$APP_DIR" switch --detach "$BEFORE_COMMIT"; then
      warn "代码回退失败，请手工执行: git -C $APP_DIR switch --detach $BEFORE_COMMIT"
    fi
  fi
  if ((MIGRATED == 1)) && [[ -n "$SNAPSHOT" && -f "$SNAPSHOT" && -n "$DB_PATH" ]]; then
    if cp "$SNAPSHOT" "$DB_PATH"; then
      rm -f "$DB_PATH-wal" "$DB_PATH-shm"
      log "已恢复数据库快照 ${SNAPSHOT}（快照之后写入的数据会丢失）"
    else
      warn "恢复数据库快照失败，请手工复制: $SNAPSHOT -> $DB_PATH"
    fi
  fi
  "$SUDO" systemctl restart "$SERVICE" || warn '重启旧版本失败'
  restart_worker_service
  if wait_for_health; then
    log "回滚完成：服务已回到 ${BEFORE_COMMIT:0:7}"
  else
    warn '回滚后服务仍不健康，请手工介入；服务仍在运行，可继续查看日志'
    diagnose
  fi
  return 1
}

main() {
  parse_args "$@"
  cd "$APP_DIR"
  resolve_health_url
  if ! preflight; then
    exit 1
  fi
  if [[ "$MODE" == "check" ]]; then
    log '检查模式：未拉取、未同步依赖、未迁移、未重启'
    exit 0
  fi
  if ! snapshot_step && ((DRY_RUN == 0)); then
    warn '无法创建数据库快照，为避免不可回滚的升级而中止'
    exit 1
  fi
  pull_step || rollback '拉取代码失败' || exit 2
  sync_dependencies || rollback '依赖同步失败' || exit 2
  apply_migrations || rollback '数据库迁移失败' || exit 2
  quick_verification || rollback '部署后测试失败' || exit 2
  restart_service
  restart_worker_service
  if ! wait_for_health; then
    diagnose
    rollback '健康检查未通过' || exit 2
  fi
  local info after_revision
  info="$(database_info)"
  after_revision="${info%%$'\t'*}"
  log '部署完成'
  printf 'commit:     %s\n' "${AFTER_COMMIT:-$BEFORE_COMMIT}"
  printf 'schema:     %s\n' "$after_revision"
  printf 'snapshot:   %s\n' "${SNAPSHOT:-无}"
  printf 'health:     %s\n' "$HEALTH_URL"
  exit 0
}

main "$@"



