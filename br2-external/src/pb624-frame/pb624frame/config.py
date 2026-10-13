"""Settings in /data/config/frame.json, merged over the defaults below."""
import copy
import json
import os
import threading

DEFAULTS = {
    'name': 'PocketBook 624',
    # weather | photos | graph | mixed (rotates weather, photos, graph)
    'mode': 'weather',
    'rotation': 0,               # 0/180 landscape, 90/270 portrait
    'interval_min': 30,          # screen refresh period
    'full_refresh_every': 12,    # INIT (deghost) every N updates
    # while awake on external power: redraw the weather screen (clock,
    # current values) every N minutes with a fast partial update; 0 = off
    'live_min': 1,
    'photo_fit': 'contain',      # contain (letterbox) | cover (crop)
    'photo_order': 'sequential',  # sequential | random
    # besides /data/photos: HA camera.* / image.* entities and image URLs,
    # fetched each time they come up
    'photo_sources': [],
    'photo_enhance': 'on',       # on: e-ink tone curve + sharpening | off
    'lang': 'en',                # en | bg
    'timezone': '',              # '' = from Home Assistant, else e.g. Europe/Sofia
    # auto: suspend between refreshes when running on battery only
    'sleep': 'auto',             # auto | always | never
    'awake_after_key_s': 60,     # stay awake this long after a button press
    'low_battery_pct': 5,
    # CPU cap without a battery (USB-only supply cannot cover full speed)
    'cpu_max_khz_no_battery': 432000,
    'mqtt': {
        'host': '',
        'port': 1883,
        'user': '',
        'password': '',
        'discovery_prefix': 'homeassistant',
        'base_topic': 'pb624',
    },
    'ha': {
        'url': '',               # e.g. http://homeassistant.local:8123
        'token': '',             # long-lived access token
        'weather_entity': 'weather.home',
        'sensors': [],           # extra entity ids shown on the weather screen
    },
    'history': {
        'entities': [],          # empty = the ha.sensors list
        'hours': 168,
        'show': 'strip',         # off | strip (instead of forecast) | screen | both
    },
    'calendar': {
        'entities': [],          # calendar.* entity ids
        'days': 7,
        'max_events': 4,
    },
    'web': {
        'port': 80,
        'password': '',          # HTTP basic auth (user "admin") if set
    },
}

SECRET_KEYS = {('mqtt', 'password'), ('ha', 'token'), ('web', 'password')}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = copy.deepcopy(DEFAULTS)
        try:
            with open(path) as f:
                self.data = _merge(DEFAULTS, json.load(f))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            print(f'config: {path}: {e}, using defaults')

    def __getitem__(self, key):
        return self.data[key]

    def get(self, *keys):
        v = self.data
        for k in keys:
            v = v[k]
        return v

    def update(self, changes):
        """Merge a (partial, nested) dict and save atomically."""
        with self.lock:
            self.data = _merge(self.data, changes)
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(self.data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)

    def public(self):
        """Copy with secrets blanked, for the web UI."""
        d = copy.deepcopy(self.data)
        for sect, key in SECRET_KEYS:
            if d.get(sect, {}).get(key):
                d[sect][key] = ''
        return d
