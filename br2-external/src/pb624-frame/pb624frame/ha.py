"""Home Assistant REST API: weather entity, forecasts, extra sensors."""
import datetime
import json
import urllib.error
import urllib.parse
import urllib.request


class HAError(Exception):
    pass


class HomeAssistant:
    def __init__(self, url, token, timeout=15):
        self.url = url.rstrip('/')
        self.token = token
        self.timeout = timeout

    @property
    def configured(self):
        return bool(self.url and self.token)

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.url + path, data=data, method=method,
            headers={'Authorization': 'Bearer ' + self.token,
                     'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise HAError(f'{path}: HTTP {e.code}') from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise HAError(f'{path}: {e}') from e

    def image(self, entity, limit=30 * 1024 * 1024):
        """Current picture of a camera.* or image.* entity (raw bytes)."""
        kind = 'camera' if entity.startswith('camera.') else 'image'
        req = urllib.request.Request(
            f'{self.url}/api/{kind}_proxy/{entity}',
            headers={'Authorization': 'Bearer ' + self.token})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout * 2) as r:
                return r.read(limit)
        except urllib.error.HTTPError as e:
            raise HAError(f'{entity}: HTTP {e.code}') from e
        except (urllib.error.URLError, OSError) as e:
            raise HAError(f'{entity}: {e}') from e

    def state(self, entity):
        return self._req('GET', f'/api/states/{entity}')

    def config(self):
        return self._req('GET', '/api/config')

    def forecast(self, entity, kind='daily'):
        """weather.get_forecasts (HA 2023.12+), falling back to the old
        'forecast' state attribute."""
        try:
            r = self._req('POST', '/api/services/weather/get_forecasts'
                          '?return_response',
                          {'entity_id': entity, 'type': kind})
            return r['service_response'][entity]['forecast']
        except (HAError, KeyError, TypeError):
            return self.state(entity)['attributes'].get('forecast', [])

    def weather(self, entity, sensors=()):
        """Everything the weather screen needs, as plain dicts."""
        st = self.state(entity)
        a = st.get('attributes', {})
        out = {
            'condition': st.get('state'),
            'temperature': a.get('temperature'),
            'apparent': a.get('apparent_temperature'),
            'humidity': a.get('humidity'),
            'pressure': a.get('pressure'),
            'wind_speed': a.get('wind_speed'),
            'wind_bearing': a.get('wind_bearing'),
            'temp_unit': a.get('temperature_unit', '°C'),
            'wind_unit': a.get('wind_speed_unit', 'km/h'),
            'pressure_unit': a.get('pressure_unit', 'hPa'),
            'name': a.get('friendly_name', entity),
            'daily': [],
            'hourly': [],
            'sensors': [],
        }
        try:
            out['daily'] = self.forecast(entity, 'daily')
        except HAError:
            pass
        try:
            out['hourly'] = self.forecast(entity, 'hourly')
        except HAError:
            pass
        for s in sensors:
            try:
                ss = self.state(s)
                sa = ss.get('attributes', {})
                out['sensors'].append({
                    'name': sa.get('friendly_name', s),
                    'state': ss.get('state'),
                    'unit': sa.get('unit_of_measurement', ''),
                })
            except HAError:
                out['sensors'].append({'name': s, 'state': '?', 'unit': ''})
        return out

    def history(self, entities, hours):
        """Numeric history per entity: {entity: {'name', 'unit', 'points':
        [(epoch, value)]}}.  Non-numeric states (unavailable...) are gaps."""
        if not entities:
            return {}
        end = datetime.datetime.now(datetime.timezone.utc)
        start = end - datetime.timedelta(hours=hours)
        q = ('/api/history/period/' + urllib.parse.quote(start.isoformat()) +
             '?filter_entity_id=' + ','.join(entities) +
             '&end_time=' + urllib.parse.quote(end.isoformat()) +
             '&minimal_response&no_attributes')
        out = {}
        for rows in self._req('GET', q):
            if not rows:
                continue
            ent = rows[0]['entity_id']
            pts = []
            for r in rows:
                try:
                    v = float(r['state'])
                except (TypeError, ValueError):
                    v = None
                t = datetime.datetime.fromisoformat(
                    r['last_changed'].replace('Z', '+00:00')).timestamp()
                pts.append((t, v))
            out[ent] = {'points': pts, 'name': ent, 'unit': ''}
        for ent in out:                      # names and units
            try:
                a = self.state(ent).get('attributes', {})
                out[ent]['name'] = a.get('friendly_name', ent)
                out[ent]['unit'] = a.get('unit_of_measurement', '')
                pts = out[ent]['points']       # extend to now with the last value
                if pts and pts[-1][1] is not None:
                    pts.append((end.timestamp(), pts[-1][1]))
            except HAError:
                pass
        return out

    def events(self, calendars, days, limit):
        """Upcoming calendar events, sorted: [{'start', 'all_day',
        'summary'}] with start as an aware datetime."""
        start = datetime.datetime.now(datetime.timezone.utc)
        end = start + datetime.timedelta(days=days)
        out = []
        for cal in calendars:
            q = (f'/api/calendars/{cal}?start=' + urllib.parse.quote(start.isoformat()) +
                 '&end=' + urllib.parse.quote(end.isoformat()))
            try:
                rows = self._req('GET', q)
            except HAError:
                continue
            for e in rows:
                st = e.get('start', {})
                if 'dateTime' in st:
                    t = datetime.datetime.fromisoformat(st['dateTime'].replace('Z', '+00:00'))
                    all_day = False
                elif 'date' in st:
                    t = datetime.datetime.fromisoformat(st['date']).replace(
                        tzinfo=datetime.timezone.utc)
                    all_day = True
                else:
                    continue
                out.append({'start': t, 'all_day': all_day,
                            'summary': e.get('summary') or ''})
        out.sort(key=lambda e: e['start'])
        return out[:limit]
