#!/usr/bin/env bash
# Called by install.sh / update.sh as the normal installation user.
set -euo pipefail
if [[ $(id -u) == 0 ]]; then
    echo 'Run as your normal installation user: bash enable_boot.sh (without sudo).'; exit 1
fi
if ! command -v raspi-config >/dev/null; then
    echo 'Cannot configure automatic login: raspi-config is missing.'
    echo 'Use this package on Raspberry Pi OS. Boot setup has NOT completed.'; exit 1
fi
if [[ ! -f "$HOME/.config/systemd/user/dronevoice.service" ]]; then
    echo 'Install Drone Voice before enabling boot startup.'; exit 1
fi
APP="$HOME/.local/share/dronevoice"
mkdir -p "$APP"
# Save original boot mode and original login configuration before first change.
if [[ ! -f "$APP/boot-mode-before.txt" ]]; then
    systemctl get-default > "$APP/boot-mode-before.txt"
    if [[ -f /etc/lightdm/lightdm.conf ]]; then
        sudo cp -- /etc/lightdm/lightdm.conf "$APP/lightdm.before-autostart.conf"
    fi
    if [[ -f /etc/systemd/system/getty@tty1.service.d/autologin.conf ]]; then
        sudo cp -- /etc/systemd/system/getty@tty1.service.d/autologin.conf "$APP/getty.before-autostart.conf"
    fi
fi
# Desktop auto-login preserves the working desktop audio setup without needing
# a monitor or any interaction. Lite installations use console auto-login.
if [[ -f /etc/init.d/lightdm || -f /etc/lightdm/lightdm.conf ]]; then
    echo 'Enabling desktop auto-login for the installation user...'
    sudo raspi-config nonint do_boot_behaviour B4
else
    echo 'Enabling console auto-login for the installation user...'
    sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
    sudo raspi-config nonint do_boot_behaviour B2
fi
# Start the user service manager at boot and retain it after logout.
sudo loginctl enable-linger "$(id -un)"
systemctl --user daemon-reload
systemctl --user enable dronevoice.service
systemctl --user restart dronevoice.service
if ! systemctl --user is-enabled --quiet dronevoice.service; then
    echo 'ERROR: receiver auto-start could not be enabled.'; exit 1
fi
if [[ $(loginctl show-user "$(id -un)" -p Linger --value) != yes ]]; then
    echo 'ERROR: boot-time user services could not be enabled.'; exit 1
fi
echo 'Boot startup configured. Reboot once when ready: sudo reboot'
echo 'No monitor, manual login, or GUI operation is required after boot.'
echo 'Bluetooth speakers still need OS pairing and connection; other active outputs are detected automatically.'
