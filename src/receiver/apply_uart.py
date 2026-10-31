#!/usr/bin/env python3
"""Idempotent UART configuration without interactive raspi-config dialogs."""
from pathlib import Path
import re
import sys
from verify_uart import verify

BEGIN = '# BEGIN DRONEVOICE UART'
END = '# END DRONEVOICE UART'

def transform(config, cmdline):
    if len([line for line in cmdline.splitlines() if line.strip()]) != 1:
        raise ValueError('cmdline.txt must contain one non-empty line; no files changed')
    if config.count(BEGIN) != config.count(END):
        raise ValueError('Unmatched DroneVoice UART markers; no files changed')
    config = re.sub(re.escape(BEGIN)+r'.*?'+re.escape(END)+r'\n?', '', config, flags=re.S)
    config = config.rstrip()+'\n\n'+BEGIN+'\n[all]\nenable_uart=1\n'+END+'\n'
    tokens = cmdline.split()
    tokens = [t for t in tokens if not re.fullmatch(r'console=(?:serial\d+|ttyAMA\d+|ttyS\d+)(?:,[^\s]*)?',t)]
    if not tokens or not any(t.startswith('root=') for t in tokens):
        raise ValueError('Root filesystem argument missing; no files changed')
    return config, ' '.join(tokens)+'\n'

def apply(folder):
    folder=Path(folder)
    paths=[folder/'config.txt',folder/'cmdline.txt']
    old=[p.read_text(encoding='utf-8') for p in paths]
    new=transform(*old)
    try:
        for p,text in zip(paths,new):
            if p.read_text(encoding='utf-8') != text:
                with p.open('w',encoding='utf-8',newline='\n') as stream:
                    stream.write(text); stream.flush()
                    __import__('os').fsync(stream.fileno())
        return verify(folder)
    except Exception:
        for p,text in zip(paths,old):
            p.write_text(text,encoding='utf-8',newline='\n')
        raise

if __name__=='__main__':
    try:
        print(apply(sys.argv[1]))
    except (OSError,ValueError,IndexError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr)
        sys.exit(1)
