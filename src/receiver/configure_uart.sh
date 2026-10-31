#!/usr/bin/env bash
# Enable the GPIO serial interface and disable the serial login console.
set -euo pipefail
if [[ $(id -u) == 0 ]]; then
    echo 'Run install.sh / update.sh as the normal installation user (without sudo).'; exit 1
fi
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
APP="$HOME/.local/share/dronevoice"
BACKUP="$APP/system-backups"
mkdir -p "$BACKUP"
# Keep the first pre-install boot configuration for reference / manual restore.
if [[ -f /boot/firmware/config.txt ]]; then
    BOOT_DIR=/boot/firmware
else
    BOOT_DIR=/boot
fi
for name in config.txt cmdline.txt; do
    if [[ -f "$BOOT_DIR/$name" && ! -e "$BACKUP/$name.before-uart" ]]; then
        sudo cp -- "$BOOT_DIR/$name" "$BACKUP/$name.before-uart"
    fi
done
echo 'Enabling UART hardware and disabling the serial login console...'
# Apply and verify boot configuration without any serial wizard / OK dialogs.
sudo python3 "$SOURCE/apply_uart.py" "$BOOT_DIR"
if getent group dialout >/dev/null && ! id -nG | tr ' ' '\n' | grep -qx dialout; then
    sudo usermod -aG dialout "$(id -un)"
fi
echo 'UART configuration saved. Reboot once after installation: sudo reboot'
if [[ ! -e /dev/serial0 ]]; then
    echo 'The receiver will wait for /dev/serial0. Reboot is required before use.'
fi
