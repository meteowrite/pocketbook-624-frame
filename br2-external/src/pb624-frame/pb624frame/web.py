"""Small web UI and JSON API.

  GET  /                 status, screen preview, actions, settings, photos
  GET  /screen.png       what the panel currently shows
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

CSS = """body{font-family:sans-serif;max-width:860px;margin:auto;padding:0 12px;color:#222}
h1{font-size:1.5em}img.screen{width:100%;border:1px solid #888;background:#eee}
table{border-collapse:collapse}td{padding:3px 10px 3px 0;vertical-align:top}
form.inline{display:inline}button{margin:2px;padding:6px 12px}
fieldset{margin:12px 0}label{display:block;margin-top:6px;font-size:.9em}
input[type=text],input[type=password],input[type=number],select,textarea{width:100%;
box-sizing:border-box;padding:4px}.photos td{border-bottom:1px solid #ddd}
.msg{background:#ffd;padding:6px;border:1px solid #cc8}"""


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

    # -- page ---------------------------------------------------------------
    def _page(self, msg):
        a, e = self.app, html.escape
        st = a.status()
        rows = ''.join(f'<tr><td>{e(k)}</td><td>{e(str(v))}</td></tr>'
                       for k, v in st.items() if k not in ('message',))
        acts = ''.join(
            f'<form class="inline" method="post" action="/action">'
            f'<button name="action" value="{k}">{lbl}</button></form>'
            for k, lbl in (('refresh', 'Refresh'), ('weather', 'Weather'),
                           ('photos', 'Photo'), ('graph', 'Graph'),
                           ('prev', '◀ Prev'), ('next', 'Next ▶'),
                           ('info', 'Info'), ('clean', 'Clean screen')))
        cfg = a.cfg.public()
        fields = ''
        for sect, key, label, kind in FIELDS:
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
                inp = (f'<input type="password" name="{name}" '
                       f'placeholder="{"(set, leave empty to keep)" if have else ""}">')
            elif kind == 'int':
                inp = f'<input type="number" name="{name}" value="{e(str(val))}">'
            else:
                inp = f'<input type="text" name="{name}" value="{e(str(val))}">'
            fields += f'<label>{e(label)}{inp}</label>'
        photos = ''.join(
            f'<tr><td>{e(p)}</td><td>'
            f'<form class="inline" method="post" action="/action">'
            f'<input type="hidden" name="photo" value="{e(p)}">'
            f'<button name="action" value="show">Show</button>'
            f'<button name="action" value="delete" '
            f'onclick="return confirm(\'Delete {e(p)}?\')">Delete</button></form></td></tr>'
            for p in a.photo_list())
        note = f'<p class="msg">{e(msg)}</p>' if msg else ''
        return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(a.cfg['name'])}</title><style>{CSS}</style></head><body>
<h1>{e(a.cfg['name'])}</h1>{note}
<img class="screen" src="/screen.png?{st.get('last_update', '')}" alt="screen">
<p>{acts}</p>
<table>{rows}</table>
<h2>Photos ({len(a.photo_list())})</h2>
<form method="post" action="/upload" enctype="multipart/form-data">
<input type="file" name="photos" accept="image/*" multiple> <button>Upload</button></form>
<table class="photos">{photos}</table>
<h2>System update</h2>
<form method="post" action="/api/update" enctype="multipart/form-data"
 onsubmit="return confirm('Install the new system in the other slot and reboot into it?')">
<input type="file" name="rootfs" accept=".gz"> <button>Update</button>
<small>pb624-rootfs.ext2.gz from the build (images/). Written to the other
system slot and checked; if it does not come back online within 5 minutes
the frame returns to the current system by itself.</small></form>
<h2>Settings</h2>
<form method="post" action="/settings"><fieldset>{fields}</fieldset>
<button>Save</button></form>
<p><small>API: POST /api/show with image bytes or JSON {{"url"|"text"}};
GET /api/status</small></p></body></html>"""


def start(app, port):
    Handler.app = app
    srv = ThreadingHTTPServer(('', port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
