#!/usr/bin/env bash
# Offline verification for IELTS Learning Studio (macOS / Linux).
#
# Runs everything a change must pass before it is deployed:
#   1. both pytest suites (tests/ and components/context-novel/tests/)
#   2. byte-compilation of app/ and the context-novel component
#   3. Ruff over app, tests and the component (source + tests)
#   4. CLI help smoke tests
#
# No API key is required and the script never contacts the network: it clears proxy
# variables, and the suites stub every provider call (see tests/integration and the
# component's fake provider tests).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy || true
export PYTHONDONTWRITEBYTECODE=1

if [[ -n "${IELTS_VERIFY_PYTHON:-}" ]]; then
  PYTHON="$IELTS_VERIFY_PYTHON"
elif [[ -x "$REPO_DIR/.venv/bin/python" ]]; then
  PYTHON="$REPO_DIR/.venv/bin/python"
else
  PYTHON="python3"
fi

if [[ -x "$REPO_DIR/.venv/bin/ruff" ]]; then
  RUFF="$REPO_DIR/.venv/bin/ruff"
else
  RUFF=(-m ruff)
fi

ruff_run() {
  if [[ "${RUFF[0]}" == "-m" ]]; then
    "$PYTHON" -m ruff "$@"
  else
    "$RUFF" "$@"
  fi
}

step() {
  printf '\n==> %s\n' "$1"
}

step "pytest: tests/ + components/context-novel/tests/ (offline, no API key)"
"$PYTHON" -m pytest -q

step "compileall: app/ + components/context-novel/src/"
"$PYTHON" -m compileall -q app components/context-novel/src

step "ruff: app tests migrations components/context-novel/src components/context-novel/tests"
ruff_run check \
  app \
  tests \
  migrations \
  components/context-novel/src \
  components/context-novel/tests

step "CLI smoke: app.cli --help, migrate --help, worker --help"
"$PYTHON" -m app.cli --help >/dev/null
"$PYTHON" -m app.cli migrate --help >/dev/null
"$PYTHON" -m app.cli worker --help >/dev/null

printf '\nIELTS Reading Studio offline verification passed.\n'
