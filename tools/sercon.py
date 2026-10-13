#!/usr/bin/env python3
"""Serial console bridge: board output -> log file, fifo -> board input."""
import os
import select
import sys
import termios

import serial

log, fifo = sys.argv[1], sys.argv[2]
dev = sys.argv[3] if len(sys.argv) > 3 else '/dev/ttyUSB0'
if not os.path.exists(fifo):
    os.mkfifo(fifo)
# DTR drives the power-button transistor: keep it asserted (released), also
# after close (no HUPCL), so a dying bridge never holds the button down.
port = serial.Serial()
port.port, port.baudrate, port.timeout = dev, 115200, 0.05
port.dtr, port.rts = True, False
port.open()
attrs = termios.tcgetattr(port.fd)
attrs[2] &= ~termios.HUPCL
termios.tcsetattr(port.fd, termios.TCSANOW, attrs)
rfd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
wfd = os.open(fifo, os.O_WRONLY)  # keep the fifo open between writers
with open(log, 'ab') as f:
    while True:
        data = port.read(4096)
        if data:
            f.write(data)
            f.flush()
        if select.select([rfd], [], [], 0)[0]:
            data = os.read(rfd, 4096)
            if data:
                port.write(data)
