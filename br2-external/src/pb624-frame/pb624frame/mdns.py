"""Minimal mDNS responder: answers A queries for <hostname>.local, so the
web UI is reachable as http://pb624.local/ whatever DHCP hands out."""
import socket
import struct
import threading
import time

GROUP, PORT = '224.0.0.251', 5353
TYPE_A, TYPE_ANY, CLASS_IN = 1, 255, 1


def _name(buf, off):
    """Decode a (possibly compressed) DNS name; returns (name, next_off)."""
    labels, end, hops = [], None, 0
    while True:
        n = buf[off]
        if n & 0xC0 == 0xC0:
            if end is None:
                end = off + 2
            off = ((n & 0x3F) << 8) | buf[off + 1]
            hops += 1
            if hops > 16:
                raise ValueError('loop')
            continue
        off += 1
        if n == 0:
            break
        labels.append(buf[off:off + n].decode('utf-8', 'replace'))
        off += n
    return '.'.join(labels).lower(), end if end is not None else off


def _encode(name):
    return b''.join(bytes([len(p)]) + p.encode() for p in name.split('.')) + b'\0'


class Responder(threading.Thread):
    def __init__(self, ip_fn, hostname=None):
        super().__init__(daemon=True)
        self.ip_fn = ip_fn                    # returns the current IPv4 or None
        self.host = (hostname or socket.gethostname()).lower() + '.local'
        self.sock = None
        self.ip = None

    def _open(self, ip):
        if self.sock:
            self.sock.close()
            self.sock = None
        if not ip:
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('', PORT))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                     socket.inet_aton(GROUP) + socket.inet_aton(ip))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        s.settimeout(30)
        self.sock = s
        self._answer(None)                    # announce the (new) address

    def _answer(self, addr):
        rr = (_encode(self.host) + struct.pack('>HHIH', TYPE_A, 0x8000 | CLASS_IN, 120, 4)
              + socket.inet_aton(self.ip))
        pkt = struct.pack('>HHHHHH', 0, 0x8400, 0, 1, 0, 0) + rr
        self.sock.sendto(pkt, addr or (GROUP, PORT))

    def _handle(self, buf, addr):
        qid, flags, qd = struct.unpack_from('>HHH', buf)
        if flags & 0x8000:                    # a response, not a query
            return
        off = 12
        for _ in range(qd):
            name, off = _name(buf, off)
            qtype, qclass = struct.unpack_from('>HH', buf, off)
            off += 4
            if name == self.host and qtype in (TYPE_A, TYPE_ANY):
                # unicast reply if asked (QU bit) or from a non-5353 port
                uni = qclass & 0x8000 or addr[1] != PORT
                self._answer(addr if uni else None)
                return

    def run(self):
        while True:
            try:
                ip = self.ip_fn()
                if ip != self.ip:
                    self.ip = ip
                    self._open(ip)
                if not self.sock:
                    time.sleep(10)
                    continue
                try:
                    buf, addr = self.sock.recvfrom(1500)
                except socket.timeout:
                    continue
                self._handle(buf, addr)
            except (OSError, ValueError, IndexError, struct.error) as e:
                print('mdns:', e)
                self.ip = None
                time.sleep(5)
