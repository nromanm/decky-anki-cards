#!/usr/bin/env bash
set -euo pipefail

# Run this ON YOUR STEAM DECK (SSH into it, or open a terminal in Desktop Mode) — not on your
# dev machine. It's a one-time setup step: it sets up a systemd --user service that starts Anki
# in the background, with no window, automatically every time you boot into Gaming Mode (before
# any game is running), so AnkiConnect is already up by the time you start playing. The "Anki
# Cards" Decky plugin only ever checks this service's status — it never installs or enables
# anything on its own, that's what this script is for.
#
# Safe to re-run.

ANKI_APP_ID="net.ankiweb.Anki"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_PATH="$UNIT_DIR/anki-background.service"

if ! flatpak info "$ANKI_APP_ID" >/dev/null 2>&1; then
  echo "Anki (Flatpak) not found — installing from Flathub..."
  if ! flatpak install -y flathub "$ANKI_APP_ID"; then
    echo "Could not install Anki automatically. Install it yourself from Discover" >&2
    echo "(search \"Anki\"), then re-run this script." >&2
    exit 1
  fi
fi

mkdir -p "$UNIT_DIR"
cat > "$UNIT_PATH" <<EOF
[Unit]
Description=Anki (background, for AnkiConnect)
PartOf=graphical-session.target
After=graphical-session.target

[Service]
EnvironmentFile=%t/gamescope-environment
ExecStart=/usr/bin/flatpak run $ANKI_APP_ID
Restart=on-failure
Slice=session.slice

[Install]
WantedBy=gamescope-session.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now anki-background.service

echo "Done. Anki will now start automatically, in the background, every time you enter Gaming Mode."
echo "Check its status any time with: systemctl --user status anki-background.service"
