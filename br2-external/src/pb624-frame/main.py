#!/usr/bin/env python3
"""PocketBook 624 photo frame / weather station.

  main.py              run on the device (/data, epdshow, sysfs)
  main.py --sim DIR    host test: data in DIR/data, screens written to DIR,
                       web UI on :8080
"""
import os
import signal
import sys
import syslog

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(line_buffering=True)

from pb624frame.app import App  # noqa: E402


class SyslogWriter:
    """stdout/stderr as syslog lines (-> /data/log/messages on the device)."""
    def __init__(self, prio):
        self.prio, self.buf = prio, ''

    def write(self, s):
        self.buf += s
        while '\n' in self.buf:
            line, self.buf = self.buf.split('\n', 1)
            if line.strip():
                syslog.syslog(self.prio, line)
        return len(s)

    def flush(self):
        pass

    def reconfigure(self, **kw):
        pass


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--sim':
        d = os.path.abspath(sys.argv[2])
        os.makedirs(d, exist_ok=True)
        App(data=os.path.join(d, 'data'), sim=d).run()
    else:
        if not sys.stdout.isatty():
            syslog.openlog('frame', 0, syslog.LOG_DAEMON)
            sys.stdout = SyslogWriter(syslog.LOG_INFO)
            sys.stderr = SyslogWriter(syslog.LOG_ERR)
        app = App()

        def stop(signum, frame):
            app.wdt.stop()      # disarm, or the watchdog reboots in 16 s
            os._exit(0)
        signal.signal(signal.SIGTERM, stop)
        app.run()
