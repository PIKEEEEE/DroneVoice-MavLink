#!/usr/bin/env bash
set -euo pipefail
systemctl --user disable --now dronevoice.service || true
rm -f -- "$HOME/.config/systemd/user/dronevoice.service" "$HOME/.local/share/applications/dronevoice.desktop"
DESKTOP=$(xdg-user-dir DESKTOP 2>/dev/null || printf '%s' "$HOME/Desktop")
if [[ -d "$DESKTOP" && "$DESKTOP" != "$HOME" ]]; then
    rm -f -- "$DESKTOP/dronevoice.desktop"
fi
systemctl --user daemon-reload
echo 'Auto-start and shortcuts removed. Program and settings retained in ~/.local/share/dronevoice.'
