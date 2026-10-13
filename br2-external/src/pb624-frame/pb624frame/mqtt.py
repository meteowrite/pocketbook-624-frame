"""MQTT link to Home Assistant (discovery, state, buttons, commands).

Topics under <base> (default "pb624"):
  status          online / offline (retained, LWT)
  state           JSON with battery, temperature, Wi-Fi, mode, ... (retained)
  screen          PNG of what the panel shows (retained, HA image entity)
  event/button    {"event_type": "left|right|home|back|power"}
  cmd/<command>   refresh, next, prev, clean, mode, interval, message,
                  show_url (http(s) URL of an image), show_image (raw bytes)
"""
import json
import threading

try:
    import paho.mqtt.client as paho
except ImportError:        # host simulation without paho
    paho = None

COMMANDS = ('refresh', 'next', 'prev', 'clean', 'mode', 'interval', 'message',
            'show_url', 'show_image')


class MQTT:
    def __init__(self, cfg, on_command, version='1.0'):
        self.cfg = cfg
        self.on_command = on_command
        self.version = version
        self.base = cfg['base_topic'].strip('/') or 'pb624'
        self.uid = self.base.replace('/', '_')
        self.client = None
        self.connected = threading.Event()

    @property
    def enabled(self):
        return bool(paho and self.cfg.get('host'))

    # -- connection -------------------------------------------------------
    def start(self):
        if not self.enabled or self.client:
            return
        c = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=self.uid)
        if self.cfg.get('user'):
            c.username_pw_set(self.cfg['user'], self.cfg.get('password') or None)
        c.will_set(f'{self.base}/status', 'offline', qos=1, retain=True)
        c.on_connect = self._on_connect
        c.on_disconnect = lambda *a: self.connected.clear()
        c.on_message = self._on_message
        c.reconnect_delay_set(2, 60)
        self.client = c
        try:
            c.connect_async(self.cfg['host'], int(self.cfg.get('port') or 1883), 60)
        except (OSError, ValueError) as e:
            print('mqtt:', e)
        c.loop_start()

    def stop(self, clean=True):
        """Disconnect; a clean disconnect keeps HA's last values (no LWT)."""
        if not self.client:
            return
        try:
            if clean:
                self.client.disconnect()
            self.client.loop_stop()
        except Exception as e:      # paho can raise odd errors on teardown
            print('mqtt stop:', e)
        self.client = None
        self.connected.clear()

    def wait(self, timeout=10):
        return self.connected.wait(timeout)

    def _on_connect(self, client, userdata, flags, rc, props=None):
        if rc != 0:
            print('mqtt: connect failed', rc)
            return
        client.subscribe(f'{self.base}/cmd/#', qos=1)
        self._discovery()
        client.publish(f'{self.base}/status', 'online', qos=1, retain=True)
        self.connected.set()

    def _on_message(self, client, userdata, msg):
        cmd = msg.topic.rsplit('/', 1)[-1]
        if cmd not in COMMANDS:
            return
        payload = msg.payload if cmd == 'show_image' else msg.payload.decode('utf-8', 'replace')
        try:
            self.on_command(cmd, payload)
        except Exception as e:
            print(f'mqtt command {cmd}: {e}')

    # -- publishing -------------------------------------------------------
    def publish_state(self, state):
        if self.client and self.connected.is_set():
            self.client.publish(f'{self.base}/state', json.dumps(state), qos=1, retain=True)

    def publish_screen(self, png_bytes):
        if self.client and self.connected.is_set():
            self.client.publish(f'{self.base}/screen', png_bytes, qos=1, retain=True)

    def publish_button(self, name):
        if self.client and self.connected.is_set():
            self.client.publish(f'{self.base}/event/button',
                                json.dumps({'event_type': name}), qos=1)

    def flush(self, timeout=5):
        """Wait for queued messages before sleeping."""
        if self.client:
            info = self.client.publish(f'{self.base}/status', 'online', qos=1, retain=True)
            try:
                info.wait_for_publish(timeout)
            except (RuntimeError, ValueError):
                pass

    # -- Home Assistant discovery -------------------------------------------
    def _discovery(self):
        b, uid = self.base, self.uid
        prefix = self.cfg.get('discovery_prefix') or 'homeassistant'
        device = {'identifiers': [uid], 'name': 'PocketBook 624',
                  'manufacturer': 'PocketBook', 'model': '624 (pb624-D-ww)',
                  'sw_version': self.version}
        common = {'availability_topic': f'{b}/status', 'device': device}
        st = f'{b}/state'

        def sensor(obj, name, key, **kw):
            return ('sensor', obj, {'name': name, 'state_topic': st,
                                    'value_template': '{{ value_json.%s }}' % key, **kw})

        ents = [
            sensor('battery', 'Battery', 'battery_pct', device_class='battery',
                   unit_of_measurement='%', state_class='measurement'),
            sensor('battery_voltage', 'Battery voltage', 'battery_v',
                   device_class='voltage', unit_of_measurement='V',
                   entity_category='diagnostic'),
            sensor('temperature', 'Temperature', 'temperature',
                   device_class='temperature', unit_of_measurement='°C',
                   state_class='measurement'),
            sensor('wifi_signal', 'Wi-Fi signal', 'rssi',
                   device_class='signal_strength', unit_of_measurement='dBm',
                   entity_category='diagnostic'),
            sensor('ip', 'IP address', 'ip', entity_category='diagnostic'),
            sensor('last_update', 'Last screen update', 'last_update',
                   device_class='timestamp'),
            sensor('screen', 'Showing', 'showing'),
            ('binary_sensor', 'power', {
                'name': 'External power', 'state_topic': st, 'device_class': 'plug',
                'value_template': "{{ 'ON' if value_json.external else 'OFF' }}"}),
            ('binary_sensor', 'charging', {
                'name': 'Charging', 'state_topic': st,
                'device_class': 'battery_charging',
                'value_template': "{{ 'ON' if value_json.charging else 'OFF' }}"}),
            ('select', 'mode', {
                'name': 'Mode', 'state_topic': st, 'value_template': '{{ value_json.mode }}',
                'command_topic': f'{b}/cmd/mode', 'options': ['weather', 'photos', 'graph', 'mixed']}),
            ('number', 'interval', {
                'name': 'Refresh interval', 'state_topic': st,
                'value_template': '{{ value_json.interval_min }}',
                'command_topic': f'{b}/cmd/interval', 'min': 1, 'max': 1440,
                'step': 1, 'mode': 'box', 'unit_of_measurement': 'min'}),
            ('text', 'message', {
                'name': 'Message', 'command_topic': f'{b}/cmd/message',
                'state_topic': st, 'value_template': '{{ value_json.message }}',
                'max': 255}),
            ('button', 'refresh', {'name': 'Refresh', 'command_topic': f'{b}/cmd/refresh'}),
            ('button', 'next', {'name': 'Next photo', 'command_topic': f'{b}/cmd/next'}),
            ('button', 'prev', {'name': 'Previous photo', 'command_topic': f'{b}/cmd/prev'}),
            ('button', 'clean', {'name': 'Clean screen', 'command_topic': f'{b}/cmd/clean',
                                 'entity_category': 'config'}),
            ('event', 'button', {
                'name': 'Button', 'state_topic': f'{b}/event/button',
                'event_types': ['left', 'right', 'home', 'back', 'power',
                                'swipe_left', 'swipe_right', 'tap_left',
                                'tap_center', 'tap_right'],
                'device_class': 'button'}),
            ('image', 'screen', {'name': 'Screen', 'image_topic': f'{b}/screen',
                                 'content_type': 'image/png'}),
        ]
        for comp, obj, conf in ents:
            conf = {**common, **conf, 'unique_id': f'{uid}_{obj}',
                    'object_id': f'{uid}_{obj}'}
            self.client.publish(f'{prefix}/{comp}/{uid}/{obj}/config',
                                json.dumps(conf), qos=1, retain=True)
