#!/usr/bin/env python3
"""Read-only boot configuration checks; independent of raspi-config getter APIs."""
from pathlib import Path
import re
import sys

def verify(boot_dir):
    folder = Path(boot_dir)
    config = (folder/'config.txt').read_text(encoding='utf-8')
    cmdline = (folder/'cmdline.txt').read_text(encoding='utf-8')
    values = re.findall(r'^\s*enable_uart\s*=\s*([01])\s*(?:#.*)?$', config, re.MULTILINE)
    if not values or values[-1] != '1':
        raise ValueError('UART enable verification failed: enable_uart=1 not found as final assignment in config.txt')
    consoles = re.findall(r'(?:^|\s)console=([^\s,]+)', cmdline)
    if any(re.fullmatch(r'(?:serial\d+|ttyAMA\d+|ttyS\d+)', name) for name in consoles):
        raise ValueError('Serial login console remains enabled in cmdline.txt')
    return 'UART hardware enabled; serial login console disabled (boot files verified).'

if __name__ == '__main__':
    try:
        print(verify(sys.argv[1]))
    except (OSError, ValueError, IndexError) as exc:
        print('ERROR: '+str(exc), file=sys.stderr)
        sys.exit(1)
