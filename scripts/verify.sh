#!/usr/bin/env bash
# Offline verification: full test suite, syntax compile, Ruff and a CLI smoke
# test. Needs no API key and makes no network call.
#
# Run this on the server (or any POSIX checkout):
#   ~/ielts-reading-studio/scripts/verify.sh
#
# The project virtualenv is used when present, so the checks run against the
# same interpreter systemd starts. Set PYTHON to force another one.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

if [ -n "${PYTHON:-}" ]; then
  :
elif [ -x "$APP_DIR/.venv/bin/python" ]; then
  PYTHON="$APP_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  PYTHON="python"
fi

echo "==> app:    $APP_DIR"
echo "==> python: $PYTHON ($("$PYTHON" --version 2>&1))"

"$PYTHON" -m pytest -q
"$PYTHON" -m compileall -q app
"$PYTHON" -m ruff check app tests
"$PYTHON" -m app.cli --help >/dev/null

echo "IELTS Reading Studio offline verification passed."
