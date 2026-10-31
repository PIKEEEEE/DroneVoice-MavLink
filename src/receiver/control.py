#!/usr/bin/env python3
import os
import time
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parent
SERVICE = 'dronevoice.service'
def command(test=False):
    args=[str(ROOT/'.venv/bin/python'), str(ROOT/'receiver.py'), '--all-devices', '--max-volume']
    return args+(['--test-audio'] if test else [])
def gui():
    import tkinter as tk
    from tkinter import ttk, messagebox
    window=tk.Tk(); window.title('Drone Voice - All Speakers Direct'); window.geometry('690x230')
    ttk.Label(window,text='Automatic DIRECT output to all connected speakers').pack(pady=20)
    ttk.Label(window,text='Bluetooth / AUX / USB / HDMI - no device selection needed').pack()
    status=tk.StringVar(value='Devices are discovered automatically every 2 seconds.')
    def service(action):
        try:
            subprocess.run(['systemctl','--user',action,SERVICE],check=True,timeout=10)
            status.set('Receiver '+action+' requested')
        except Exception as exc: messagebox.showerror('Drone Voice',str(exc))
    def test():
        subprocess.Popen(command(True)); status.set('Direct test tone requested on all speakers')
    bar=ttk.Frame(window); bar.pack(pady=20)
    for label,callback in [('Start',lambda:service('start')),('Stop',lambda:service('stop')),
                           ('Restart',lambda:service('restart')),('Test all speakers',test)]:
        ttk.Button(bar,text=label,command=callback).pack(side='left',padx=5)
    ttk.Label(window,textvariable=status).pack()
    window.mainloop()
if __name__=='__main__':
    if '--run' in sys.argv:
        # Replace the controller: systemd now supervises the actual receiver.
        port='/dev/serial0'
        deadline=time.monotonic()+120
        while not os.path.exists(port) or not os.access(port, os.R_OK|os.W_OK):
            print('Waiting for UART /dev/serial0. Reboot after installation; check UART and dialout permissions.', flush=True)
            if time.monotonic() >= deadline:
                sys.exit(1)
            time.sleep(10)
        os.execv(command()[0],command())
    elif '--test-audio' in sys.argv: sys.exit(subprocess.call(command(True)))
    else: gui()
