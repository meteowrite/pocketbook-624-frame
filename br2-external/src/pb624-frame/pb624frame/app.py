"""pb624-frame main loop: screens, buttons, Home Assistant, sleep."""
import datetime
import io
import json
import os
import queue
import random
import socket
import threading
import time
import urllib.parse
import urllib.request

from PIL import Image, ImageOps

import gzip
import subprocess

from . import epd, ha, hw, mdns, mqtt, render, web
from .config import Config

VERSION = '1.0'
PHOTO_EXT = ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp', '.tif', '.tiff', '.pgm')
LOW_PWR = '/usr/share/pb624/low_pwr.pgm'

try:
    import zoneinfo
except ImportError:
    zoneinfo = None


class App:
    def __init__(self, data='/data', sim=None):
        self.data = data
        self.sim = sim
        self.cfg = Config(os.path.join(data, 'config', 'frame.json'))
        self.photo_dir = os.path.join(data, 'photos')
        self.state_path = os.path.join(data, 'state', 'state.json')
        os.makedirs(self.photo_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        self.state = {'photo': -1, 'mixed': 0, 'tz': ''}
        try:
            with open(self.state_path) as f:
                self.state.update(json.load(f))
        except (OSError, ValueError):
            pass
        self.display = epd.Display(self.cfg['rotation'], self.cfg['full_refresh_every'],
                                   sim_dir=sim)
        self.q = queue.Queue()
        self.showing = ''
        self.message = ''
        self.last_update = None
        self.last_activity = time.time()
        self.next_refresh = 0
        self.weather_data = None
        self.weather_extra = (None, None, None)   # history, events, fetch time
        self.next_live = 0
        self.next_check = time.time() + 300
        self.net_fails = 0
        self.weather_error = ''
        self.net_lock = threading.Lock()
        self.wdt = None if sim else hw.Watchdog()
        self._setup_services()

    def _setup_services(self):
        h = self.cfg['ha']
        self.ha = ha.HomeAssistant(h['url'], h['token'])
        self.mqtt = mqtt.MQTT(self.cfg['mqtt'], self.request, VERSION)

    # -- helpers ------------------------------------------------------------
    def request(self, cmd, arg=None):
        """Thread-safe entry point for keys, MQTT and the web UI."""
        self.q.put((cmd, arg))

    def _save_state(self):
        try:
            tmp = self.state_path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(self.state, f)
            os.replace(tmp, self.state_path)
        except OSError as e:
            print('state:', e)

    def tz(self):
        name = self.cfg['timezone'] or self.state.get('tz')
        if name and zoneinfo:
            try:
                return zoneinfo.ZoneInfo(name)
            except (zoneinfo.ZoneInfoNotFoundError, ValueError):
                pass
        return datetime.datetime.now().astimezone().tzinfo

    def now(self):
        return datetime.datetime.now(self.tz())

    def power(self):
        if self.sim:
            return {'external': True, 'battery_present': True, 'battery_pct': 80,
                    'battery_v': 3.95, 'charging': False, 'ac': False, 'usb': True}
        return hw.power_info()

    def net(self):
        return {'ip': '127.0.0.1', 'rssi': -50, 'ssid': 'sim'} if self.sim else hw.wifi_info()

    def network_up(self, timeout=40):
        """Wi-Fi + MQTT; returns True when we have an IP."""
        with self.net_lock:
            ok = True if self.sim else hw.wifi_up(timeout)
            if ok and self.mqtt.enabled and not self.mqtt.client:
                self.mqtt.start()
                self.mqtt.wait(10)
            return ok

    def network_down(self):
        with self.net_lock:
            if self.mqtt.client:
                self.publish()
                self.mqtt.flush()
                self.mqtt.stop()
            if not self.sim:
                hw.wifi_down()

    def status(self):
        p, n = self.power(), self.net()
        return {
            'name': self.cfg['name'],
            'version': VERSION,
            'mode': self.cfg['mode'],
            'showing': self.showing,
            'message': self.message,
            'last_update': self.last_update.isoformat() if self.last_update else None,
            'next_refresh_s': max(0, int(self.next_refresh - time.time())),
            'interval_min': self.cfg['interval_min'],
            'photos': len(self.photo_pool()),
            'battery_pct': p.get('battery_pct'),
            'battery_v': p.get('battery_v'),
            'charging': p.get('charging'),
            'external': p.get('external'),
            'temperature': None if self.sim else hw.axp_temp(),
            'soc_temperature': None if self.sim else hw.soc_temp(),
            'ip': n['ip'],
            'ssid': n['ssid'],
            'rssi': n['rssi'],
            'mqtt': self.mqtt.connected.is_set(),
            'ha_error': self.weather_error,
            'sleep_policy': self.cfg['sleep'],
        }

    def publish(self):
        if self.mqtt.client:
            self.mqtt.publish_state(self.status())

    def screen_png(self):
        try:
            with open(self.display.preview, 'rb') as f:
                return f.read()
        except OSError:
            return None

    # -- photos -------------------------------------------------------------
    def photo_list(self):
        try:
            return sorted(f for f in os.listdir(self.photo_dir)
                          if f.lower().endswith(PHOTO_EXT) and not f.startswith('.'))
        except OSError:
            return []

    def photo_pool(self):
        """Local photos plus the configured remote sources."""
        return self.photo_list() + list(self.cfg['photo_sources'])

    def _fetch_source(self, src):
        if src.startswith(('http://', 'https://')):
            req = urllib.request.Request(src, headers={'User-Agent': 'pb624-frame'})
            if self.ha.configured and src.startswith(self.ha.url):
                req.add_header('Authorization', 'Bearer ' + self.ha.token)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read(30 * 1024 * 1024)
        return self.ha.image(src)

    def add_photo(self, fname, data):
        """Store an upload, pre-scaled (the A13 decodes big JPEGs slowly)."""
        base = os.path.splitext(os.path.basename(fname))[0]
        base = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in base)[:60] or 'photo'
        im = Image.open(io.BytesIO(data))
        im.draft('RGB', (1600, 1600))
        im = ImageOps.exif_transpose(im).convert('RGB')
        im.thumbnail((1600, 1600), Image.LANCZOS)
        path = os.path.join(self.photo_dir, base + '.jpg')
        i = 1
        while os.path.exists(path):
            path = os.path.join(self.photo_dir, f'{base}-{i}.jpg')
            i += 1
        im.save(path + '.tmp', 'JPEG', quality=90)
        os.replace(path + '.tmp', path)
        os.sync()
        return os.path.basename(path)

    def update_path(self):
        d = os.path.join(self.data, 'update')
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, 'rootfs.ext2.gz')

    def firmware_update(self, path):
        """Check an uploaded rootfs.ext2.gz (already on disk, see
        update_path) and queue the update.  Streams: the image is 384 MB
        uncompressed, far more than the RAM."""
        disk = None if self.sim else hw.system_disk()
        if not disk and not self.sim:
            raise ValueError('system SD card not found')
        size = 0
        try:
            with gzip.open(path, 'rb') as f:
                while True:
                    n = len(f.read(1 << 20))
                    if not n:
                        break
                    size += n
        except (OSError, EOFError, gzip.BadGzipFile) as e:
            os.remove(path)
            raise ValueError(f'not a valid gzip file: {e}')
        if not self.sim:
            part = os.path.basename(disk) + 'p1'
            with open(f'/sys/class/block/{part}/size') as f:
                psize = int(f.read()) * 512
            if size != psize:
                os.remove(path)
                raise ValueError(f'image is {size} bytes, rootfs partition {psize}')
        os.sync()
        self.request('firmware', (path, disk))
        return size

    def _firmware(self, path, disk):
        """Overwrite the (read-only) rootfs and reset through the watchdog.
        Everything that execs or imports happens before the write."""
        self._show(render.message(self.display.size, 'Updating...',
                                  ['Writing the new system,', 'the frame restarts by itself.']),
                   'update', full=False)
        if self.sim:
            print('sim: would write', path, 'to', disk)
            return
        self.network_down()
        cmd = f'zcat {path} | dd of={disk}p1 bs=1M conv=fsync 2>/dev/null; sync'
        subprocess.run(['sh', '-c', cmd])
        self.wdt.expire()
        while True:                          # rootfs is gone; just wait
            time.sleep(60)

    def delete_photo(self, name):
        if name in self.photo_list():
            os.remove(os.path.join(self.photo_dir, name))
            os.sync()

    # -- screens ------------------------------------------------------------
    def _show(self, img, kind, dither=False, full=False, fast=False):
        hw.led(True) if not self.sim else None
        self.display.show(img, dither=dither, full=full, fast=fast)
        hw.led(False) if not self.sim else None
        self.showing = kind
        self.last_update = self.now()
        if self.mqtt.client:
            self.publish()
            png = self.screen_png()
            if png:
                self.mqtt.publish_screen(png)

    def _status_bar(self):
        p, n = self.power(), self.net()
        return {'battery_pct': p.get('battery_pct'), 'external': p.get('external'),
                'rssi': n['rssi'], 'ip': n['ip']}

    def history_entities(self):
        return self.cfg['history']['entities'] or self.cfg['ha']['sensors']

    def fetch_history(self):
        h = self.cfg['history']
        try:
            return self.ha.history(self.history_entities(), int(h['hours']))
        except (ha.HAError, ValueError) as e:
            print('history:', e)
            return None

    def fetch_events(self):
        c = self.cfg['calendar']
        if not c['entities']:
            return None
        try:
            return self.ha.events(c['entities'], int(c['days']), int(c['max_events']))
        except (ha.HAError, ValueError) as e:
            print('calendar:', e)
            return None

    def graph_enabled(self):
        return (self.ha.configured and self.cfg['history']['show'] in ('screen', 'both')
                and bool(self.history_entities()))

    def show_graph(self):
        if not self.ha.configured:
            return self.show_info('Set Home Assistant URL and token')
        if not self.network_up():
            return self.show_info('Graph: no network')
        hist = self.fetch_history()
        img = render.history_screen(self.display.size, hist or {}, self.now(),
                                    int(self.cfg['history']['hours']),
                                    self.cfg['lang'], self._status_bar())
        self._show(img, 'graph')

    def live_enabled(self):
        """Minute updates only while the frame stays awake anyway."""
        if self.sim:
            return int(self.cfg['live_min']) > 0
        return (int(self.cfg['live_min']) > 0 and
                (self.cfg['sleep'] == 'never' or
                 (self.cfg['sleep'] == 'auto' and self.power()['external'])))

    def show_weather(self, live=False):
        """live: fast partial redraw with fresh states, but the history and
        calendar from the last full refresh (they change slowly)."""
        if not self.ha.configured:
            return self.show_info('Set Home Assistant URL and token')
        if self.network_up():
            try:
                if not self.cfg['timezone'] and not self.state.get('tz'):
                    self.state['tz'] = self.ha.config().get('time_zone', '')
                    self._save_state()
                h = self.cfg['ha']
                self.weather_data = self.ha.weather(h['weather_entity'], h['sensors'])
                self.weather_error = ''
            except ha.HAError as e:
                self.weather_error = str(e)
        else:
            self.weather_error = 'no network'
        if not self.weather_data:
            return self.show_info(f'Weather: {self.weather_error}')
        if live:
            hist, events, fetched = self.weather_extra
        else:
            hist = events = None
            fetched = self.now()
            if not self.weather_error:
                if self.cfg['history']['show'] in ('strip', 'both'):
                    hist = self.fetch_history()
                events = self.fetch_events()
            self.weather_extra = (hist, events, fetched)
        img = render.weather(self.display.size, self.weather_data, self.now(),
                             self.cfg['lang'], self._status_bar(), hist,
                             int(self.cfg['history']['hours']), events,
                             clock=self.live_enabled(), chart_now=fetched)
        self._show(img, 'weather', fast=live)

    def show_photo(self, step=1, name=None):
        photos = self.photo_pool()
        if not photos:
            ip = self.net()['ip'] or 'pb624.local'
            img = render.message(self.display.size, 'No photos yet',
                                 ['Upload photos at', f'http://{ip}/'])
            return self._show(img, 'info')
        if name in photos:
            i = photos.index(name)
        elif self.cfg['photo_order'] == 'random' and step:
            i = random.randrange(len(photos))
        else:
            i = (self.state.get('photo', -1) + step) % len(photos)
        self.state['photo'] = i
        self._save_state()
        p = photos[i]
        try:
            if i < len(self.photo_list()):
                src = os.path.join(self.photo_dir, p)
            else:
                if not self.network_up():
                    raise OSError('no network')
                src = io.BytesIO(self._fetch_source(p))
            img = render.photo(src, self.display.size, self.cfg['photo_fit'],
                               self.cfg['photo_enhance'] != 'off')
        except (OSError, ha.HAError, ValueError) as e:
            img = render.message(self.display.size, 'Bad photo', [p, str(e)])
        self._show(img, 'photo:' + p, dither=True)

    def show_info(self, note=''):
        p, n = self.power(), self.net()
        lines = []
        if n['ip']:
            lines.append(f"http://{n['ip']}/  or  http://{os.uname().nodename}.local/")
            lines.append(f"Wi-Fi {n['ssid'] or ''} {n['rssi'] or ''} dBm".strip())
        elif not os.path.exists(os.path.join(self.data, 'config', 'wpa_supplicant.conf')):
            lines.append('No Wi-Fi: add config/wpa_supplicant.conf to the data partition')
        else:
            lines.append('Wi-Fi not connected')
        if p.get('battery_present'):
            lines.append(f"Battery {p['battery_pct']}%  {p['battery_v']} V"
                         + ('  charging' if p.get('charging') else ''))
        else:
            lines.append('External power, no battery')
        lines.append(f"Home Assistant: {'ok' if self.ha.configured and not self.weather_error else self.weather_error or 'not set'}")
        lines.append(f"MQTT: {'connected' if self.mqtt.connected.is_set() else 'off' if not self.mqtt.enabled else 'not connected'}")
        if note:
            lines.append(note)
        img = render.message(self.display.size, self.cfg['name'], lines,
                             f'v{VERSION}  ·  ◀ ▶ photos  ·  home: weather  ·  back: this page')
        self._show(img, 'info')

    def show_image(self, data, kind='image'):
        im = Image.open(io.BytesIO(data))
        im.draft('RGB', (1600, 1600))
        im = ImageOps.exif_transpose(im).convert('L')
        if im.size != self.display.size:
            im = ImageOps.pad(im, self.display.size, Image.LANCZOS, color=255)
        self._show(im, kind, dither=True)

    def refresh(self):
        mode = self.cfg['mode']
        if self.message:
            self._show(render.wrap(self.message, self.display.size), 'message')
        elif mode == 'photos':
            self.show_photo(1)
        elif mode == 'graph':
            self.show_graph()
        elif mode == 'mixed':
            screens = ['weather', 'photos'] + (['graph'] if self.graph_enabled() else [])
            self.state['mixed'] = (self.state.get('mixed', 0) + 1) % len(screens)
            self._save_state()
            {'weather': self.show_weather, 'photos': lambda: self.show_photo(1),
             'graph': self.show_graph}[screens[self.state['mixed']]]()
        else:
            self.show_weather()
        self.schedule()

    def schedule(self):
        self.next_refresh = time.time() + max(1, int(self.cfg['interval_min'])) * 60
        self.schedule_live()

    def schedule_live(self):
        """Next live redraw: just after the next minute boundary."""
        step = max(1, int(self.cfg['live_min'])) * 60
        t = time.time()
        self.next_live = t - t % step + step + 1

    def live_due(self):
        return (self.showing == 'weather' and not self.message and
                time.time() >= self.next_live and
                self.next_refresh - time.time() > 30 and self.live_enabled())

    # -- commands -----------------------------------------------------------
    def handle(self, cmd, arg):
        if cmd == 'refresh':
            self.refresh()
        elif cmd == 'live':
            self.schedule_live()
            self.show_weather(live=True)
        elif cmd in ('next', 'prev'):
            self.show_photo(1 if cmd == 'next' else -1)
        elif cmd == 'weather':
            self.show_weather()
        elif cmd == 'photos':
            self.show_photo(0)
        elif cmd == 'graph':
            self.show_graph()
        elif cmd == 'show':
            self.show_photo(0, name=arg)
        elif cmd == 'info':
            self.show_info()
        elif cmd == 'clean':
            prev = Image.open(self.display.preview) if os.path.exists(self.display.preview) else None
            if prev:
                self.display.show(prev, full=True)
        elif cmd == 'mode':
            if arg in ('weather', 'photos', 'graph', 'mixed'):
                self.cfg.update({'mode': arg})
                self.refresh()
        elif cmd == 'interval':
            self.cfg.update({'interval_min': max(1, int(float(arg)))})
            self.schedule()
            self.publish()
        elif cmd == 'message':
            self.message = (arg or '').strip()
            self.refresh()
        elif cmd == 'show_url':
            req = urllib.request.Request(arg.strip(), headers={'User-Agent': 'pb624-frame'})
            if self.ha.configured and arg.strip().startswith(self.ha.url):
                req.add_header('Authorization', 'Bearer ' + self.ha.token)
            self.network_up()
            with urllib.request.urlopen(req, timeout=30) as r:
                self.show_image(r.read(30 * 1024 * 1024), 'url')
        elif cmd == 'show_image':
            self.show_image(arg)
        elif cmd == 'firmware':
            self._firmware(*arg)
        elif cmd == 'config':
            self.display.rotation = self.cfg['rotation']
            if not self.sim:
                self.buttons.rotation = self.cfg['rotation']
            self.display.full_every = self.cfg['full_refresh_every']
            self.mqtt.stop()
            self._setup_services()
            self.network_up(10)
            self.refresh()

    def web_action(self, action, photo):
        self.last_activity = time.time()
        if action == 'delete':
            self.delete_photo(photo)
            return f'Deleted {photo}'
        if action in ('refresh', 'next', 'prev', 'weather', 'photos', 'graph', 'info',
                      'clean', 'show'):
            self.request(action, photo)
            return f'{action}: queued'
        return 'unknown action'

    def config_changed(self):
        self.last_activity = time.time()
        self.request('config')

    def on_key(self, name, pressed):
        if not pressed:
            return
        self.last_activity = time.time()
        self.mqtt.publish_button(name)
        if name == 'tap_center':     # cycle weather -> graph -> photo
            if self.showing == 'weather':
                nxt = 'graph' if self.graph_enabled() else 'photos'
            elif self.showing == 'graph':
                nxt = 'photos'
            else:
                nxt = 'weather'
            self.request(nxt)
            return
        self.request({'left': 'prev', 'right': 'next', 'home': 'weather',
                      'back': 'info', 'power': 'refresh',
                      'swipe_left': 'next', 'swipe_right': 'prev',
                      'tap_left': 'prev', 'tap_right': 'next'}[name])

    # -- power --------------------------------------------------------------
    def check_battery(self):
        p = self.power()
        if not self.sim:
            cap = None if p['battery_present'] else self.cfg['cpu_max_khz_no_battery']
            if cap != getattr(self, '_cpu_cap', 0):
                hw.cpu_max(cap or 1008000)
                self._cpu_cap = cap
        if (p['battery_present'] and not p['external'] and p['battery_pct'] is not None
                and p['battery_pct'] <= self.cfg['low_battery_pct']):
            try:
                img = Image.open(LOW_PWR)
            except OSError:              # no stock picture in the image
                img = render.message(self.display.size, 'Battery low',
                                     ['Please charge the frame'])
            try:
                self.display.show(img, full=True)
            except OSError:
                pass
            if not self.sim:
                self.network_down()
                self.wdt.stop()
                hw.poweroff()

    def want_sleep(self):
        pol = self.cfg['sleep']
        if self.sim or pol == 'never':
            return False
        if pol == 'auto' and self.power()['external']:
            return False
        return (self.q.empty() and
                time.time() - self.last_activity > self.cfg['awake_after_key_s'])

    def sleep(self):
        wait = self.next_refresh - time.time()
        if wait < 90:
            return
        print(f'sleeping {int(wait)} s')
        self.network_down()
        self.wdt.stop()
        slept = hw.suspend(wait)
        self.wdt.start()
        if slept is None:                    # suspend failed: don't spin
            self.last_activity = time.time()
            return
        if time.time() < self.next_refresh - 60:
            # woken early by a key: stay up a while and bring the network back
            self.last_activity = time.time()
            threading.Thread(target=self.network_up, daemon=True).start()

    # -- health -------------------------------------------------------------
    def _ha_reachable(self):
        u = urllib.parse.urlsplit(self.ha.url)
        port = u.port or (443 if u.scheme == 'https' else 80)
        try:
            socket.create_connection((u.hostname, port), timeout=5).close()
            return True
        except OSError:
            return False

    def health(self):
        """Every 5 min while awake: log a heartbeat and restart Wi-Fi when
        Home Assistant has been unreachable twice in a row."""
        self.next_check = time.time() + 300
        n = self.net()
        try:
            with open('/proc/meminfo') as f:
                mem = next(ln.split()[1] for ln in f if ln.startswith('MemAvailable'))
        except (OSError, StopIteration):
            mem = '?'
        ok = bool(n['ip']) and (not self.ha.configured or self._ha_reachable())
        print(f"health: ip={n['ip']} rssi={n['rssi']} mem={mem}kB ha={'ok' if ok else 'FAIL'}"
              f" showing={self.showing}")
        self.net_fails = 0 if ok else self.net_fails + 1
        if self.net_fails >= 2:
            print('health: network lost, restarting Wi-Fi')
            self.net_fails = 0
            self.network_down()
            self.network_up(60)

    # -- main loop ----------------------------------------------------------
    def run(self):
        if not self.sim:
            self.wdt.start()
            self.buttons = hw.Buttons(self.on_key, self.cfg['rotation'])
            self.buttons.start()
        web.start(self, int(self.cfg['web']['port']) if not self.sim else 8080)
        if not self.sim:
            mdns.Responder(hw.ipv4).start()
        self.check_battery()               # CPU cap before the first update
        self.network_up(60)
        self.refresh()
        while True:
            self.check_battery()
            if self.want_sleep():
                self.sleep()
            if not self.sim and time.time() >= self.next_check:
                self.health()
            timeout = min(5, max(0, self.next_refresh - time.time()))
            try:
                cmd, arg = self.q.get(timeout=timeout)
            except queue.Empty:
                if time.time() >= self.next_refresh:
                    cmd, arg = 'refresh', None
                elif self.live_due():
                    cmd, arg = 'live', None
                else:
                    continue
            try:
                self.handle(cmd, arg)
            except Exception as e:           # keep the frame alive
                print(f'{cmd}: {type(e).__name__}: {e}')
                self.weather_error = self.weather_error or str(e)
