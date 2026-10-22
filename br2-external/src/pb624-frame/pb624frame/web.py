"""Small web UI and JSON API.

  GET  /                 status, screen preview, actions, settings, photos
  GET  /screen.png       what the panel currently shows
  GET  /thumb?p=<name>   small JPEG of a photo (cached in /data/state/thumbs)
  GET  /api/status       JSON status
  POST /action           form: action=refresh|next|prev|clean|weather|photos|graph|info
                         |show|delete  (+ photo=<name>)
  POST /settings         settings form
  POST /upload           multipart photo upload (field "photos", multiple)
  POST /api/update       body: rootfs.ext2.gz (or multipart field "rootfs")
                         -> installs it in the other system slot, reboots into it
  POST /api/show         body: image bytes (any Pillow format), or JSON
                         {"url": "..."} / {"text": "..."}
"""
import base64
import email.parser
import mmap
import os
import email.policy
import html
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import SECRET_KEYS

_thumb_lock = threading.Lock()   # one image decode at a time (RAM)

FIELDS = [   # (section, key, label, kind)
    (None, 'name', 'Name', 'text'),
    (None, 'mode', 'Mode', ['weather', 'photos', 'graph', 'mixed']),
    (None, 'interval_min', 'Refresh every (min)', 'int'),
    (None, 'live_min', 'Live clock on USB power, every (min, 0 = off)', 'int'),
    (None, 'rotation', 'Rotation', ['0', '90', '180', '270']),
    (None, 'lang', 'Language', ['en', 'bg']),
    (None, 'timezone', 'Time zone (empty = from HA)', 'text'),
    (None, 'photo_fit', 'Photo fit', ['contain', 'cover']),
    (None, 'photo_order', 'Photo order', ['sequential', 'random']),
    (None, 'photo_sources', 'More photos: HA camera./image. entities or image URLs (one per line)', 'list'),
    (None, 'photo_enhance', 'Photo tone curve + sharpening', ['on', 'off']),
    (None, 'full_refresh_every', 'Deghost (INIT) every N updates', 'int'),
    (None, 'sleep', 'Sleep between updates', ['auto', 'always', 'never']),
    (None, 'awake_after_key_s', 'Stay awake after a key (s)', 'int'),
    (None, 'low_battery_pct', 'Power off below (%)', 'int'),
    ('ha', 'url', 'Home Assistant URL', 'text'),
    ('ha', 'token', 'HA long-lived token', 'secret'),
    ('ha', 'weather_entity', 'Weather entity', 'text'),
    ('ha', 'sensors', 'Extra sensors (one entity per line)', 'list'),
    ('history', 'entities', 'History entities (one per line, empty = extra sensors)', 'list'),
    ('history', 'hours', 'History period (hours)', 'int'),
    ('history', 'show', 'Show history', ['strip', 'screen', 'both', 'off']),
    ('calendar', 'entities', 'Calendars (calendar.* entities, one per line)', 'list'),
    ('calendar', 'days', 'Calendar days ahead', 'int'),
    ('calendar', 'max_events', 'Calendar max events', 'int'),
    ('mqtt', 'host', 'MQTT broker', 'text'),
    ('mqtt', 'port', 'MQTT port', 'int'),
    ('mqtt', 'user', 'MQTT user', 'text'),
    ('mqtt', 'password', 'MQTT password', 'secret'),
    ('mqtt', 'discovery_prefix', 'Discovery prefix', 'text'),
    ('mqtt', 'base_topic', 'Base topic', 'text'),
    ('web', 'password', 'Web UI password (user admin)', 'secret'),
]

GROUPS = [   # settings shown as collapsible sections: (title, test(section, key))
    ('General', lambda sect, k: sect is None and k in (
        'name', 'mode', 'interval_min', 'live_min', 'rotation', 'lang', 'timezone')),
    ('Photos', lambda sect, k: sect is None and k.startswith('photo_')),
    ('Display and power', lambda sect, k: sect is None),
    ('Home Assistant', lambda sect, k: sect == 'ha'),
    ('History graph', lambda sect, k: sect == 'history'),
    ('Calendar', lambda sect, k: sect == 'calendar'),
    ('MQTT', lambda sect, k: sect == 'mqtt'),
    ('Web UI', lambda sect, k: sect == 'web'),
]
THUMB = 320                  # px, longest side

CSS = """
:root{--bg:#f3f4f6;--card:#fff;--fg:#1f2328;--mute:#6b7280;--line:#e5e7eb;
--acc:#2563eb;--acc-fg:#fff;--ok:#15803d;--warn:#b45309;--bad:#b91c1c;--chip:#eef1f5;
--shadow:0 1px 2px rgba(0,0,0,.06),0 2px 8px rgba(0,0,0,.05)}
@media(prefers-color-scheme:dark){:root{--bg:#111418;--card:#1b2027;--fg:#e6e8eb;
--mute:#9aa3ae;--line:#2c333c;--acc:#4f8cff;--chip:#252c35;--ok:#4ade80;--warn:#fbbf24;
--bad:#f87171;--shadow:none}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{background:var(--card);border-bottom:1px solid var(--line);padding:12px 16px}
header .in,main{max-width:1100px;margin:auto}
header h1{margin:0 12px 0 0;font-size:1.3em;display:inline-block;vertical-align:middle}
.chips{display:inline-flex;flex-wrap:wrap;gap:6px;vertical-align:middle}
.chip{background:var(--chip);border-radius:999px;padding:2px 10px;font-size:.85em;
white-space:nowrap}
.chip.ok{color:var(--ok)}.chip.warn{color:var(--warn)}.chip.bad{color:var(--bad)}
main{padding:16px;display:grid;gap:16px;grid-template-columns:minmax(0,3fr) minmax(0,2fr)}
main>.wide{grid-column:1/-1}
@media(max-width:820px){main{grid-template-columns:minmax(0,1fr)}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:16px;box-shadow:var(--shadow);min-width:0}
.card h2{margin:0 0 12px;font-size:1.05em}
.card h2 small{color:var(--mute);font-weight:normal}
.screen{width:100%;display:block;border-radius:6px;border:10px solid #d9d6cf;
background:#eeeae2;image-rendering:auto}
.bar{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.group{display:inline-flex}form.group{display:inline-flex}.group button{border-radius:0;margin-left:-1px}
.group button:first-child{border-radius:8px 0 0 8px;margin-left:0}
.group button:last-child{border-radius:0 8px 8px 0}
button,.file span{font:inherit;cursor:pointer;border:1px solid var(--line);
background:var(--card);color:var(--fg);border-radius:8px;padding:6px 14px}
button:hover,.file span:hover{border-color:var(--acc)}
button.primary{background:var(--acc);border-color:var(--acc);color:var(--acc-fg)}
button.danger{color:var(--bad)}
form.inline{display:inline}
dl{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:4px 14px;margin:0}
dt{color:var(--mute)}dd{margin:0;overflow-wrap:anywhere}
.msg{max-width:1100px;margin:16px auto 0;padding:10px 14px;border-radius:8px;
background:#fef9c3;color:#713f12;border:1px solid #fde68a}
.msg.err{background:#fee2e2;color:#7f1d1d;border-color:#fecaca}
.grid{display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));
margin-top:12px}
.ph{border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--chip)}
.ph img{width:100%;aspect-ratio:4/3;object-fit:cover;display:block}
.ph .n{font-size:.8em;padding:4px 6px;white-space:nowrap;overflow:hidden;
text-overflow:ellipsis;color:var(--mute)}
.ph form{display:flex;gap:4px;padding:0 6px 6px}.ph button{flex:1;padding:3px 0;font-size:.85em}
.file input{display:none}.file span{display:inline-block}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
details{border-top:1px solid var(--line);padding:8px 0}
details:first-of-type{border-top:0}
summary{cursor:pointer;font-weight:600;padding:4px 0}
.fields{display:grid;gap:10px 16px;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));
padding:8px 0}
label{display:block;font-size:.88em;color:var(--mute)}
input[type=text],input[type=password],input[type=number],select,textarea{width:100%;
margin-top:3px;font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);
border-radius:6px;padding:6px 8px}
textarea{min-height:4.5em;resize:vertical}
input:focus,select:focus,textarea:focus{outline:2px solid var(--acc);outline-offset:-1px}
.note{color:var(--mute);font-size:.85em;margin:8px 0 0}
footer{max-width:1100px;margin:0 auto;padding:0 16px 24px;color:var(--mute);font-size:.85em}
code{background:var(--chip);padding:1px 5px;border-radius:4px}
"""


def _stream_to(rfile, n, path):
    """Copy n bytes of request body to path without holding them in RAM."""
    with open(path, 'wb') as f:
        while n > 0:
            chunk = rfile.read(min(n, 1 << 20))
            if not chunk:
                raise ValueError('upload truncated')
            f.write(chunk)
            n -= len(chunk)


def _extract_part(path, ctype, field):
    """Cut one file field out of a multipart body stored at path, in place
    (mmap, no RAM copy)."""
    boundary = ctype.split('boundary=', 1)[1].strip().strip('"').encode()
    tmp = path + '.part'
    with open(path, 'rb') as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
        name = b'name="' + field.encode() + b'"'
        h = m.find(name)
        if h < 0:
            raise ValueError(f'no "{field}" field in the upload')
        start = m.find(b'\r\n\r\n', h) + 4
        end = m.find(b'\r\n--' + boundary, start)
        if start < 4 or end < 0:
            raise ValueError('malformed multipart upload')
        with open(tmp, 'wb') as out:
            for i in range(start, end, 1 << 20):
                out.write(m[i:min(end, i + (1 << 20))])
    os.replace(tmp, path)


def _multipart(ctype, body):
    """[(field, filename, bytes)] from a multipart/form-data body."""
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b'Content-Type: ' + ctype.encode() + b'\r\n\r\n' + body)
    out = []
    for part in msg.iter_parts():
        out.append((part.get_param('name', header='content-disposition'),
                    part.get_filename(), part.get_payload(decode=True) or b''))
    return out


class Handler(BaseHTTPRequestHandler):
    app = None
    server_version = 'pb624-frame'

    def log_message(self, fmt, *args):
        pass

    # -- helpers ------------------------------------------------------------
    def _auth(self):
        pw = self.app.cfg.get('web', 'password')
        if not pw:
            return True
        want = base64.b64encode(f'admin:{pw}'.encode()).decode()
        if self.headers.get('Authorization') == 'Basic ' + want:
            return True
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="pb624"')
        self.end_headers()
        return False

    def _send(self, code, body, ctype='text/html; charset=utf-8'):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, msg=''):
        self.send_response(303)
        self.send_header('Location', '/?msg=' + urllib.parse.quote(msg) if msg else '/')
        self.end_headers()

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > 60 * 1024 * 1024:
            raise ValueError('upload too large')
        return self.rfile.read(n)

    def _form(self):
        return {k: v[-1] for k, v in urllib.parse.parse_qs(
            self._body().decode(), keep_blank_values=True).items()}

    # -- GET ----------------------------------------------------------------
    def do_GET(self):
        if not self._auth():
            return
        path, _, query = self.path.partition('?')
        if path == '/':
            msg = urllib.parse.parse_qs(query).get('msg', [''])[0]
            self._send(200, self._page(msg))
        elif path == '/screen.png':
            data = self.app.screen_png()
            self._send(200 if data else 404, data or b'', 'image/png')
        elif path == '/api/status':
            self._send(200, json.dumps(self.app.status()), 'application/json')
        elif path == '/thumb':
            name = urllib.parse.parse_qs(query).get('p', [''])[0]
            data = self._thumb(name)
            if data:
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'max-age=86400')
                self.end_headers()
                self.wfile.write(data)
            else:
                self._send(404, 'not found', 'text/plain')
        else:
            self._send(404, 'not found', 'text/plain')

    # -- POST ---------------------------------------------------------------
    def do_POST(self):
        if not self._auth():
            return
        try:
            if self.path == '/action':
                f = self._form()
                msg = self.app.web_action(f.get('action', ''), f.get('photo', ''))
                self._redirect(msg)
            elif self.path == '/settings':
                self.app.cfg.update(self._settings(self._form()))
                self.app.config_changed()
                self._redirect('Settings saved')
            elif self.path == '/upload':
                n = 0
                for field, fname, data in _multipart(self.headers['Content-Type'],
                                                     self._body()):
                    if field == 'photos' and fname and data:
                        self.app.add_photo(fname, data)
                        n += 1
                self._redirect(f'{n} photo(s) uploaded')
            elif self.path == '/api/update':
                n = int(self.headers.get('Content-Length') or 0)
                if not n or n > 200 * 1024 * 1024:
                    raise ValueError('bad upload size')
                ctype = self.headers.get('Content-Type', '')
                path = self.app.update_path()
                _stream_to(self.rfile, n, path)
                if ctype.startswith('multipart/'):
                    _extract_part(path, ctype, 'rootfs')
                size = self.app.firmware_update(path)
                if ctype.startswith('multipart/'):
                    self._redirect(f'Update accepted ({size} bytes), rebooting')
                else:
                    self._send(200, json.dumps({'ok': True, 'size': size}),
                               'application/json')
            elif self.path == '/api/show':
                body = self._body()
                ctype = self.headers.get('Content-Type', '')
                if ctype.startswith('application/json'):
                    j = json.loads(body)
                    if 'url' in j:
                        self.app.request('show_url', j['url'])
                    elif 'text' in j:
                        self.app.request('message', j['text'])
                    else:
                        raise ValueError('need "url" or "text"')
                else:
                    self.app.request('show_image', body)
                self._send(200, '{"ok": true}', 'application/json')
            else:
                self._send(404, 'not found', 'text/plain')
        except (ValueError, KeyError, OSError) as e:
            if self.path.startswith('/api/'):
                self._send(400, json.dumps({'error': str(e)}), 'application/json')
            else:
                self._redirect(f'Error: {e}')

    def _settings(self, form):
        out = {}
        for sect, key, _, kind in FIELDS:
            name = f'{sect}.{key}' if sect else key
            if name not in form:
                continue
            v = form[name].strip()
            if kind == 'secret' and not v:
                continue                     # empty = keep the stored secret
            if kind == 'int' or key == 'rotation':
                v = int(v)
            elif kind == 'list':
                v = [s.strip() for s in v.splitlines() if s.strip()]
            if sect:
                out.setdefault(sect, {})[key] = v
            else:
                out[key] = v
        return out

    # -- thumbnails ---------------------------------------------------------
    def _thumb(self, name):
        """Small JPEG of a local photo, made once and kept in
        /data/state/thumbs (JPEG draft mode: decoded at 1/8 size)."""
        a = self.app
        if name not in a.photo_list():          # also rules out ../ tricks
            return None
        src = os.path.join(a.photo_dir, name)
        d = os.path.join(a.data, 'state', 'thumbs')
        dst = os.path.join(d, name + '.jpg')
        try:
            if os.path.getmtime(dst) >= os.path.getmtime(src):
                with open(dst, 'rb') as f:
                    return f.read()
        except OSError:
            pass
        try:
            from PIL import Image, ImageOps
            with _thumb_lock, Image.open(src) as im:
                im.draft('RGB', (THUMB, THUMB))
                im = ImageOps.exif_transpose(im).convert('RGB')
                im.thumbnail((THUMB, THUMB))
                os.makedirs(d, exist_ok=True)
                im.save(dst + '.tmp', 'JPEG', quality=80)
                os.replace(dst + '.tmp', dst)
            with open(dst, 'rb') as f:
                return f.read()
        except Exception as e:                  # a broken photo: no thumbnail
            print(f'thumb {name}: {e}')
            return None

    def _prune_thumbs(self, photos):
        d = os.path.join(self.app.data, 'state', 'thumbs')
        try:
            for f in os.listdir(d):
                if f[:-4] not in photos:
                    os.remove(os.path.join(d, f))
        except OSError:
            pass

    # -- page ---------------------------------------------------------------
    def _page(self, msg):
        a, e = self.app, html.escape
        st = a.status()
        photos = a.photo_list()
        self._prune_thumbs(set(photos))

        # header chips
        def chip(text, cls=''):
            return f'<span class="chip {cls}">{e(text)}</span>'
        chips = []
        pct = st.get('battery_pct')
        if pct is not None:
            plug = ' ⚡' if st.get('charging') else (' 🔌' if st.get('external') else '')
            chips.append(chip(f'🔋 {pct}%{plug}',
                              'bad' if pct < 15 else 'warn' if pct < 30 else 'ok'))
        elif st.get('external'):
            chips.append(chip('🔌 USB power', 'ok'))
        rssi = st.get('rssi')
        if st.get('ip'):
            q = '' if rssi is None else ('ok' if rssi > -65 else 'warn' if rssi > -78 else 'bad')
            chips.append(chip(f'📶 {st.get("ssid") or "Wi-Fi"}'
                              + (f' {rssi} dBm' if rssi is not None else ''), q))
        else:
            chips.append(chip('📶 offline', 'bad'))
        if not a.ha.configured:
            chips.append(chip('HA not set', 'warn'))
        else:
            chips.append(chip('HA ✗', 'bad') if st.get('ha_error') else chip('HA ✓', 'ok'))
        if a.mqtt.enabled:
            chips.append(chip('MQTT ✓', 'ok') if st.get('mqtt') else chip('MQTT ✗', 'warn'))

        # actions
        def btn(k, lbl, cls=''):
            return (f'<form class="inline" method="post" action="/action">'
                    f'<button class="{cls}" name="action" value="{k}">{lbl}</button></form>')
        def group(*items):                  # one form, the button gives the action
            return ('<form class="inline group" method="post" action="/action">'
                    + ''.join(f'<button name="action" value="{k}">{lbl}</button>'
                              for k, lbl in items) + '</form>')
        bar = (btn('refresh', '⟳ Refresh', 'primary')
               + group(('weather', 'Weather'), ('photos', 'Photo'), ('graph', 'Graph'),
                       ('info', 'Info'))
               + group(('prev', '◀'), ('next', '▶'))
               + btn('clean', 'Clean screen'))

        # status list
        def fmt_time(iso):
            return iso[11:16] if iso and len(iso) >= 16 else (iso or '–')
        nxt = st.get('next_refresh_s')
        temps = ' / '.join(f'{t:.0f} °C' for t in (st.get('temperature'),
                                                   st.get('soc_temperature'))
                           if isinstance(t, (int, float)))
        rows = [
            ('Mode', st.get('mode')),
            ('Showing', st.get('showing') or '–'),
            ('Message', st.get('message') or None),
            ('Last update', fmt_time(st.get('last_update'))),
            ('Next refresh', f'in {nxt // 60} min {nxt % 60} s' if nxt is not None else None),
            ('Refresh every', f'{st.get("interval_min")} min'),
            ('Photos', st.get('photos')),
            ('Battery', f'{st.get("battery_v")} V' if st.get('battery_v') else None),
            ('Temperature (board / CPU)', temps or None),
            ('IP address', st.get('ip')),
            ('USB photo drive', st.get('usb_drive')),
            ('Sleep', st.get('sleep_policy')),
            ('Home Assistant', (st.get('ha_error') or 'ok') if a.ha.configured else 'not set'),
            ('App version', st.get('version')),
        ]
        dl = ''.join(f'<dt>{e(k)}</dt><dd>{e(str(v))}</dd>' for k, v in rows if v is not None)

        # photos
        grid = ''.join(
            f'<div class="ph"><img loading="lazy" alt="" '
            f'src="/thumb?p={urllib.parse.quote(p)}">'
            f'<div class="n" title="{e(p)}">{e(p)}</div>'
            f'<form method="post" action="/action">'
            f'<input type="hidden" name="photo" value="{e(p)}">'
            f'<button name="action" value="show">Show</button>'
            f'<button class="danger" name="action" value="delete" '
            f'onclick="return confirm(this.form.photo.value + \': delete?\')">Delete</button>'
            f'</form></div>'
            for p in photos)

        # settings
        cfg = a.cfg.public()
        done, sections = set(), ''
        for title, test in GROUPS:
            fields = ''
            for sect, key, label, kind in FIELDS:
                if (sect, key) in done or not test(sect, key):
                    continue
                done.add((sect, key))
                name = f'{sect}.{key}' if sect else key
                val = cfg[sect][key] if sect else cfg[key]
                if isinstance(kind, list):
                    opts = ''.join(f'<option{" selected" if str(val) == o else ""}>{o}</option>'
                                   for o in kind)
                    inp = f'<select name="{name}">{opts}</select>'
                elif kind == 'list':
                    inp = f'<textarea name="{name}" rows="3">{e(chr(10).join(val))}</textarea>'
                elif kind == 'secret':
                    have = (sect, key) in SECRET_KEYS and a.cfg.get(sect, key)
                    inp = (f'<input type="password" name="{name}" autocomplete="off" '
                           f'placeholder="{"set — leave empty to keep" if have else "not set"}">')
                elif kind == 'int':
                    inp = f'<input type="number" name="{name}" value="{e(str(val))}">'
                else:
                    inp = f'<input type="text" name="{name}" value="{e(str(val))}">'
                fields += f'<label>{e(label)}{inp}</label>'
            if fields:
                op = ' open' if title == 'General' else ''
                sections += (f'<details{op}><summary>{e(title)}</summary>'
                             f'<div class="fields">{fields}</div></details>')

        # system
        sysrows = [('Running', st.get('system')), ('Other slot', st.get('system_other')),
                   ('Last update', st.get('system_update'))]
        sysdl = ''.join(f'<dt>{e(k)}</dt><dd>{e(str(v))}</dd>' for k, v in sysrows if v)

        note = ''
        if msg:
            cls = 'msg err' if msg.lower().startswith('error') else 'msg'
            note = f'<div class="{cls}">{e(msg)}</div>'
        name = e(a.cfg['name'])
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{name}</title><style>{CSS}</style></head><body>
<header><div class="in"><h1>{name}</h1><span class="chips">{''.join(chips)}</span></div></header>
{note}
<main>
<section class="card"><h2>Screen <small>· {e(st.get('showing') or '')}</small></h2>
<img class="screen" src="/screen.png?{e(st.get('last_update') or '')}" alt="current screen">
<div class="bar">{bar}</div></section>
<section class="card"><h2>Status</h2><dl>{dl}</dl></section>
<section class="card wide"><h2>Photos <small>· {len(photos)}</small></h2>
<form method="post" action="/upload" enctype="multipart/form-data" class="row">
<label class="file"><input type="file" name="photos" accept="image/*" multiple
 onchange="this.form.submit()"><span>＋ Add photos</span></label>
<span class="note">JPEG, PNG, … — resized and stored on the frame. Or plug the frame
into a PC: its USB drive <b>PB624</b> takes photos too (eject it when done).</span></form>
<div class="grid">{grid}</div></section>
<section class="card wide"><h2>Settings</h2>
<form method="post" action="/settings">{sections}
<div class="bar"><button class="primary">Save settings</button></div></form></section>
<section class="card wide"><h2>System</h2><dl>{sysdl}</dl>
<form method="post" action="/api/update" enctype="multipart/form-data" class="row" style="margin-top:12px"
 onsubmit="return confirm('Install the new system in the other slot and reboot into it?')">
<label class="file"><input type="file" name="rootfs" accept=".gz"
 onchange="this.form.requestSubmit()"><span>⬆ Install update…</span></label></form>
<p class="note">Pick <code>pb624-rootfs.ext2.gz</code> from the build (<code>output/images/</code>).
It is written to the other system slot and checked; if the frame is not back online
within 5 minutes it returns to the current system by itself.</p></section>
</main>
<footer>API: <code>GET /api/status</code> · <code>POST /api/show</code> (image bytes, or JSON
<code>{{"url"|"text"}}</code>) · <code>POST /api/update</code></footer>
</body></html>"""


def start(app, port):
    Handler.app = app
    srv = ThreadingHTTPServer(('', port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
