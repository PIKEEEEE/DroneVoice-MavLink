#!/usr/bin/env bash
set -euo pipefail
if [[ $(id -u) == 0 ]]; then
    echo 'Run as your normal user: bash install.sh (without sudo).'; exit 1
fi
if ! command -v apt-get >/dev/null || ! command -v systemctl >/dev/null; then
    echo 'Requires Raspberry Pi OS / Debian with systemd.'; exit 1
fi
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
APP="$HOME/.local/share/dronevoice"
echo 'Close any manually started receiver.py with Ctrl+C first.'
sudo -v
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y -o Dpkg::Options::=--force-confold python3-venv python3-tk python3-numpy python3-scipy libopus0 pulseaudio-utils
mkdir -p "$APP" "$HOME/.config/systemd/user" "$HOME/.local/share/applications"
systemctl --user stop dronevoice.service 2>/dev/null || true
install -m 644 "$SOURCE/receiver.py" "$APP/receiver.py"
install -m 644 "$SOURCE/control.py" "$APP/control.py"
python3 -m venv --system-site-packages "$APP/.venv"
"$APP/.venv/bin/python" -m pip install pymavlink pyserial
"$APP/.venv/bin/python" "$APP/receiver.py" --self-test
bash "$SOURCE/configure_uart.sh"
# Quote paths in systemd and desktop files; escape field-code percent signs.
APP_ESC=${APP//\\/\\\\}
APP_ESC=${APP_ESC//\"/\\\"}
APP_ESC=${APP_ESC//%/%%}
cat > "$HOME/.config/systemd/user/dronevoice.service" <<EOF
[Unit]
Description=Drone Voice MAVLink audio receiver
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart="$APP_ESC/.venv/bin/python" "$APP_ESC/control.py" --run
Restart=always
RestartSec=5
KillMode=control-group
TimeoutStopSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF
cat > "$HOME/.local/share/applications/dronevoice.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Drone Voice Settings
Comment=Automatic audio to all connected speakers
Exec="$APP_ESC/.venv/bin/python" "$APP_ESC/control.py"
Icon=audio-speakers
Terminal=false
Categories=AudioVideo;Audio;
EOF
DESKTOP=$(xdg-user-dir DESKTOP 2>/dev/null || printf '%s' "$HOME/Desktop")
if [[ -d "$DESKTOP" && "$DESKTOP" != "$HOME" ]]; then
    install -m 755 "$HOME/.local/share/applications/dronevoice.desktop" "$DESKTOP/dronevoice.desktop"
fi
systemctl --user daemon-reload
systemctl --user enable --now dronevoice.service
bash "$SOURCE/enable_boot.sh"
echo 'Installed with automatic startup at power-on.'
echo 'All connected speakers are selected automatically. No GUI setup required.'
echo 'Logs: journalctl --user -u dronevoice.service -f'
echo 'IMPORTANT: Reboot once to apply UART and permission settings: sudo reboot'
