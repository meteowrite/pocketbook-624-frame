"""PB624 hardware: AXP209 power/battery, temperatures, Wi-Fi, LED, buttons,
suspend (s2idle + PCF8563 alarm on the NMI line)."""
import fcntl
import glob
import os
import socket
import select
import struct
import subprocess
import threading
import time

PS = '/sys/class/power_supply'
LED = '/sys/class/leds/pb624:green:status/brightness'
WAKEALARM = '/sys/class/rtc/rtc0/wakealarm'

# linux/input-event-codes.h
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
KEYS = {105: 'left', 106: 'right', 102: 'home', 158: 'back', 116: 'power'}
BTN_TOUCH, ABS_X, ABS_Y = 330, 0, 1
TOUCH_MAX = 2047             # msg2133 range after the DT axis transform
SWIPE_MIN = 300              # raw units (~1/7 of the width)
# struct input_event on a 32-bit kernel: timeval (2 x u32), type, code, value
EVENT = struct.Struct('=IIHHi')


def _read(path, conv=str, default=None):
    try:
        with open(path) as f:
            return conv(f.read().strip())
    except (OSError, ValueError):
        return default


def power_info():
    """Battery and supply state from the AXP209 drivers."""
    bat = f'{PS}/axp20x-battery'
    present = _read(f'{bat}/present', int, 0) == 1
    info = {
        'ac': _read(f'{PS}/axp20x-ac/online', int, 0) == 1,
        'usb': _read(f'{PS}/axp20x-usb/online', int, 0) == 1,
        'battery_present': present,
        'battery_pct': None,
        'battery_v': None,
        'battery_status': None,
    }
    if present:
        uv = _read(f'{bat}/voltage_now', int) or 0
        # with the battery removed the AXP can still flag one at ~2.6 V
        if uv < 3000000:
            info['battery_present'] = present = False
    if present:
        info['battery_pct'] = _read(f'{bat}/capacity', int)
        info['battery_v'] = round(uv / 1e6, 3)
        info['battery_status'] = _read(f'{bat}/status')
    info['external'] = info['ac'] or info['usb']
    info['charging'] = info['battery_status'] == 'Charging'
    return info


def axp_temp():
    """AXP209 die temperature in C (idles close to ambient)."""
    base = '/sys/bus/iio/devices/iio:device0/in_temp_'
    raw = _read(base + 'raw', float)
    off = _read(base + 'offset', float)
    scale = _read(base + 'scale', float)
    if None in (raw, off, scale):
        return None
    return round((raw + off) * scale / 1000.0, 1)


def soc_temp():
    for h in glob.glob('/sys/class/hwmon/hwmon*'):
        if _read(f'{h}/name') == 'sun4i_ts':
            t = _read(f'{h}/temp1_input', int)
            return round(t / 1000.0, 1) if t is not None else None
    return None


def ipv4(iface='wlan0'):
    """IPv4 address of iface or None (SIOCGIFADDR, no subprocess)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            r = fcntl.ioctl(s.fileno(), 0x8915, struct.pack('256s', iface.encode()))
        return socket.inet_ntoa(r[20:24])
    except OSError:
        return None


def system_disk():
    """The SD card holding this system (the one with the "data" partition)."""
    for d in ('/dev/mmcblk0', '/dev/mmcblk1'):
        try:
            out = subprocess.run(['blkid', d + 'p2'], capture_output=True,
                                 text=True).stdout
        except OSError:
            continue
        if 'LABEL="data"' in out:
            return d
    return None


def wifi_info(iface='wlan0'):
    info = {'ip': None, 'rssi': None, 'ssid': None}
    try:
        out = subprocess.run(['ip', '-4', '-o', 'addr', 'show', 'dev', iface],
                             capture_output=True, text=True, timeout=5).stdout
        for tok in out.split():
            if '/' in tok and tok[0].isdigit():
                info['ip'] = tok.split('/')[0]
                break
    except (OSError, subprocess.SubprocessError):
        pass
    for line in (_read('/proc/net/wireless') or '').splitlines():
        if line.strip().startswith(iface + ':'):
            try:
                level = float(line.split()[3].rstrip('.'))
                info['rssi'] = int(level - 256 if level > 63 else level)
            except (IndexError, ValueError):
                pass
    try:
        out = subprocess.run(['iwgetid', '-r', iface], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        info['ssid'] = out or None
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def wifi_up(timeout=40):
    """Start Wi-Fi (S30wifi) and wait for an IPv4 address."""
    if wifi_info()['ip']:
        return True
    subprocess.run(['/etc/init.d/S30wifi', 'start'], capture_output=True)
    end = time.time() + timeout
    while time.time() < end:
        if wifi_info()['ip']:
            return True
        time.sleep(1)
    return False


def wifi_down():
    subprocess.run(['/etc/init.d/S30wifi', 'stop'], capture_output=True)


def led(on):
    try:
        with open(LED, 'w') as f:
            f.write('1' if on else '0')
    except OSError:
        pass


def suspend(seconds):
    """s2idle until the RTC alarm (minute resolution) or a key; returns the
    time slept, or None if suspend failed."""
    when = int(time.time()) + max(60, int(seconds))
    try:
        with open(WAKEALARM, 'w') as f:
            f.write('0')
        with open(WAKEALARM, 'w') as f:
            f.write(str(when))
    except OSError as e:
        print(f'suspend: wakealarm: {e}')
        return None
    t0 = time.time()
    try:
        with open('/sys/power/state', 'w') as f:
            f.write('mem')
    except OSError as e:
        print(f'suspend: {e}')
        return None
    return time.time() - t0


def cpu_max(khz):
    """Cap cpufreq.  Without a battery, a PC USB port (~500-600 mA) cannot
    cover a full-speed CPU plus an e-ink update: measured 464 mA at 432 MHz,
    591 mA at 624 MHz, and a brown-out hang at 1008 MHz."""
    try:
        with open('/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq', 'w') as f:
            f.write(str(khz))
    except OSError:
        pass


def poweroff():
    subprocess.run(['poweroff'])


class Buttons(threading.Thread):
    """Reads the key and touch input devices and calls cb(name, pressed).

    Touch is reduced to gestures, reported as a press of a pseudo key:
    swipe_left / swipe_right, tap_left / tap_center / tap_right (thirds of
    the panel's long side, panel orientation).  rotate(x, y) maps panel to
    logical coordinates for rotated displays."""

    def __init__(self, cb, rotation=0):
        super().__init__(daemon=True)
        self.cb = cb
        self.rotation = rotation
        self.pos = [0, 0]
        self.down = None             # (x, y) where the finger went down
        self.pending = False         # BTN_TOUCH=1 seen, position follows
        self.fds = []
        for dev in sorted(glob.glob('/dev/input/event*')):
            try:
                self.fds.append(os.open(dev, os.O_RDONLY | os.O_NONBLOCK))
            except OSError:
                pass

    def run(self):
        while True:
            r, _, _ = select.select(self.fds, [], [], 60)
            for fd in r:
                try:
                    data = os.read(fd, EVENT.size * 16)
                except BlockingIOError:
                    continue
                for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    _, _, typ, code, value = EVENT.unpack_from(data, i)
                    if typ == EV_KEY and code in KEYS and value in (0, 1):
                        self.cb(KEYS[code], value == 1)
                    elif typ == EV_ABS and code in (ABS_X, ABS_Y):
                        self.pos[code] = value
                    elif typ == EV_KEY and code == BTN_TOUCH:
                        self._touch(value == 1)
                    elif typ == EV_SYN and self.pending:
                        self.down = tuple(self.pos)
                        self.pending = False

    def _logical(self, x, y):
        r, m = self.rotation, TOUCH_MAX
        if r == 180:
            return m - x, m - y
        if r == 90:
            return y, m - x
        if r == 270:
            return m - y, x
        return x, y

    def _touch(self, down):
        if down:
            # the driver sends BTN_TOUCH before ABS_X/Y in the same frame,
            # so take the start position at the following SYN_REPORT
            self.pending = True
            return
        if self.down is None:
            return
        x0, y0 = self._logical(*self.down)
        x1, y1 = self._logical(*self.pos)
        self.down = None
        dx = x1 - x0
        if abs(dx) > SWIPE_MIN and abs(dx) > 2 * abs(y1 - y0):
            g = 'swipe_left' if dx < 0 else 'swipe_right'
        else:
            g = ('tap_left', 'tap_center', 'tap_right')[min(2, x1 * 3 // (TOUCH_MAX + 1))]
        self.cb(g, True)


class Watchdog:
    """Feeds /dev/watchdog (sunxi, max 16 s) from a thread, so a kernel or
    driver hang resets the board.  The sunxi driver keeps counting in
    s2idle, so stop() (magic close) must be called before suspending."""

    def __init__(self, path='/dev/watchdog', period=4):
        self.path = path
        self.period = period
        self.fd = None
        self.lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def start(self):
        with self.lock:
            if self.fd is None:
                try:
                    self.fd = os.open(self.path, os.O_WRONLY)
                except OSError as e:
                    print(f'watchdog: {e}')

    def stop(self):
        with self.lock:
            if self.fd is not None:
                try:
                    os.write(self.fd, b'V')
                finally:
                    os.close(self.fd)
                    self.fd = None

    def expire(self):
        """Stop feeding without disarming: the board resets in <= 16 s."""
        with self.lock:
            self.period = 3600

    def _run(self):
        while True:
            with self.lock:
                if self.fd is not None:
                    try:
                        os.write(self.fd, b'\0')
                    except OSError:
                        pass
            time.sleep(self.period)
