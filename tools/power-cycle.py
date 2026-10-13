#!/usr/bin/env python3
"""Power-cycle the PB624 into FEL through the FTDI DTR line.

An NPN on DTR# sits across the power switch: DTR de-asserted (pin high)
= button pressed, DTR asserted (pin low) = released.  HUPCL is cleared so
closing the port leaves DTR asserted (button released).

usage: power-cycle.py [off|on|cycle]   (default: cycle)
  off    hold 8 s  -> AXP209 forced shutdown (~6.5 s)
  on     press 2 s -> power on; FEL enumerates ~4 s later
  cycle  off, 3 s pause, on, then wait for the FEL device; up to 3 tries
"""
import subprocess
import sys
import termios
import time

import serial


def fel_present():
    out = subprocess.run(['lsusb'], capture_output=True, text=True).stdout
    return '1f3a:efe8' in out


def open_port(dev='/dev/ttyUSB0'):
    port = serial.Serial()
    port.port = dev
    port.baudrate = 115200
    port.dtr = True     # released
    port.rts = False
    port.open()
    attrs = termios.tcgetattr(port.fd)
    attrs[2] &= ~termios.HUPCL
    termios.tcsetattr(port.fd, termios.TCSANOW, attrs)
    return port


def press(port, seconds):
    port.dtr = False
    time.sleep(seconds)
    port.dtr = True


def wait_fel(seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if fel_present():
            return True
        time.sleep(0.25)
    return False


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'cycle'
    port = open_port()
    if action == 'off':
        press(port, 8)
        print('off')
        return 0
    if action == 'on':
        press(port, 2)
        ok = wait_fel(45)
    else:
        # VMware sometimes fails to attach the re-enumerated FEL device;
        # another off/on usually gets it through.
        for attempt in range(1, 4):
            press(port, 8)
            time.sleep(3)
            press(port, 2)
            ok = wait_fel(20)
            print('attempt %d: %s' % (attempt, 'FEL ready' if ok else 'no FEL'))
            if ok:
                break
    port.close()
    print('FEL ready' if ok else 'FEL not attached')
    return 0 if ok else 1

if __name__ == '__main__':
    sys.exit(main())
