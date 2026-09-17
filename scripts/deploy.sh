#!/usr/bin/env bash
# Update a git-based deployment: pull the latest revision and restart the service.
#
# Run this on the server, from anywhere:
#   ~/ielts-reading-studio/scripts/deploy.sh
#
# The first run on a fresh server needs the git remote to be reachable, i.e. a
# read-only deploy key added to the GitHub repository.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE="${IELTS_SERVICE:-ielts-reading-studio}"
cd "$APP_DIR"

echo "==> app:     $APP_DIR"
echo "==> before:  $(git rev-parse --short HEAD) $(git log -1 --pretty=%s)"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "!! tracked files have local changes; refusing to pull" >&2
  git status --short --untracked-files=no >&2
  echo "   commit or discard them first (data directories are ignored and safe)" >&2
  exit 1
fi

git pull --ff-only

echo "==> after:   $(git rev-parse --short HEAD) $(git log -1 --pretty=%s)"
sudo systemctl restart "$SERVICE"
sleep 4
systemctl is-active "$SERVICE"
echo "==> reloaded; check http://127.0.0.1:8766/healthz"
