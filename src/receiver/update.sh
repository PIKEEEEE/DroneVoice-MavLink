#!/usr/bin/env bash
set -euo pipefail
if [[ $(id -u) == 0 ]]; then
    echo 'Run as your normal installation user: bash update.sh (without sudo).'; exit 1
fi
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
APP="$HOME/.local/share/dronevoice"
if [[ ! -x "$APP/.venv/bin/python" || ! -f "$HOME/.config/systemd/user/dronevoice.service" ]]; then
    echo 'Existing installation not found. Run bash install.sh instead.'; exit 1
fi
"$APP/.venv/bin/python" "$SOURCE/receiver.py" --self-test
"$APP/.venv/bin/python" -c 'import sys; compile(open(sys.argv[1],encoding="utf-8").read(),sys.argv[1],"exec")' "$SOURCE/control.py"
systemctl --user stop dronevoice.service
cp -- "$APP/control.py" "$APP/control.before-all-speakers.py"
cp -- "$APP/receiver.py" "$APP/receiver.before-direct.py"
install -m 644 "$SOURCE/control.py" "$APP/control.py"
install -m 644 "$SOURCE/receiver.py" "$APP/receiver.py"
bash "$SOURCE/configure_uart.sh"
systemctl --user enable dronevoice.service
systemctl --user restart dronevoice.service
echo 'Updated: automatic output to ALL connected speakers. No device selection needed.'
bash "$SOURCE/enable_boot.sh"
echo 'Logs: journalctl --user -u dronevoice.service -f'

echo 'IMPORTANT: Reboot once to apply UART settings: sudo reboot'
