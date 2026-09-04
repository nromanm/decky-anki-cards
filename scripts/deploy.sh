#!/usr/bin/env bash
set -euo pipefail

PLUGIN_NAME="Anki Cards"
DECK_HOST="steamdeck"

[ -f .env ] && { set -a; . ./.env; set +a; }
: "${DECK_PASS:?Set DECK_PASS in .env}"

REMOTE_TMP="/home/deck/.plugin-staging"
REMOTE_DIR="/home/deck/homebrew/plugins/${PLUGIN_NAME}"

pnpm run build

ssh "$DECK_HOST" "rm -rf '$REMOTE_TMP' && mkdir -p '$REMOTE_TMP'"
rsync -az --delete \
  dist main.py package.json plugin.json py_modules README.md LICENSE \
  "$DECK_HOST:$REMOTE_TMP/"

ssh "$DECK_HOST" "echo '$DECK_PASS' | sudo -S bash -c '
  rm -rf \"$REMOTE_DIR\" &&
  mkdir -p \"$REMOTE_DIR\" &&
  cp -r $REMOTE_TMP/. \"$REMOTE_DIR\" &&
  chown -R root:root \"$REMOTE_DIR\" &&
  systemctl restart plugin_loader
'"

echo "Deployed: $PLUGIN_NAME"
