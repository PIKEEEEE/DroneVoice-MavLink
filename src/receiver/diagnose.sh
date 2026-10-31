#!/usr/bin/env bash
set -euo pipefail
echo 'Keep Mission Planner PTT pressed and speak for 5 seconds.'
echo 'Send a photo of AUDIO / LINK / FILTER / LAST_TUNNEL. Ctrl+C exits logs.'
sudo journalctl -b _SYSTEMD_USER_UNIT=dronevoice.service -n 12 -f -o cat
