#!/usr/bin/env python3
"""A/B system updates (see README "Updating a running frame").

Slot A is partition 1, slot B partition 3 of the system card.  U-Boot
(patches/uboot/0002, include/configs/pb624-ab.h) boots the first slot in
BOOT_ORDER that has attempts left and decrements its counter; Linux resets
the counter once the system is up.  An update is written to the slot that
is not running, read back and checked, and only then booted as a trial
(PB_TRIAL): the app confirms it once it is back on the network, otherwise
the trial runs out of attempts and U-Boot boots the old slot again.

  pb624-ota status               slots, versions, boot state (JSON)
  pb624-ota install FILE.gz      write a rootfs.ext2.gz to the other slot
                                 and boot it next as a trial (--no-reboot)
  pb624-ota switch               boot the other slot next as a trial
  pb624-ota confirm              mark the running slot good (the app does it)
  pb624-ota bootloader FILE      rewrite U-Boot if it differs (not A/B!)
  pb624-ota make-slot-b          add slot B to an old card (/data unmounted)

Standalone (stdlib only), also used by the app as pb624frame.ota.
"""
import gzip
import hashlib
import json
import os
import struct
import subprocess
import sys
import time
import zlib

SLOTS = {'A': 1, 'B': 3}
STATE_COPIES = (0x780 * 512, 0x7c0 * 512)     # see pb624-ab.h
STATE_SIZE = 0x1000
STATE_KEYS = ('BOOT_ORDER', 'BOOT_A_LEFT', 'BOOT_B_LEFT', 'PB_TRIAL')
ATTEMPTS = 3                 # per confirmed slot
TRIAL_ATTEMPTS = 2           # for a freshly installed one
UBOOT_OFFSET = 8192
UBOOT_MAX = STATE_COPIES[0] - UBOOT_OFFSET
DISK_SIGNATURE = 0x50623624
SLOT_SECTORS = 384 * 2048    # 384 MiB, = BR2_TARGET_ROOTFS_EXT2_SIZE
VERSION_FILE = '/etc/pb624-version'
STATE_DIR = '/data/state'
MNT = '/run/pb624-ota'
CHUNK = 1 << 20


class OtaError(Exception):
    pass


def other(slot):
    return 'B' if slot == 'A' else 'A'


# -- the card ---------------------------------------------------------------
def _root_partition():
    """(disk, partition number) of the mounted rootfs."""
    st = os.stat('/')
    dev = f'{os.major(st.st_dev)}:{os.minor(st.st_dev)}'
    sysdir = os.path.realpath(f'/sys/dev/block/{dev}')
    with open(os.path.join(sysdir, 'partition')) as f:
        part = int(f.read())
    disk = os.path.basename(os.path.dirname(sysdir))
    return '/dev/' + disk, part


def system_disk():
    return _root_partition()[0]


def running_slot():
    """From pb624.slot= (set by the A/B U-Boot), else the root partition."""
    try:
        with open('/proc/cmdline') as f:
            for arg in f.read().split():
                if arg.startswith('pb624.slot='):
                    return arg.split('=', 1)[1]
    except OSError:
        pass
    part = _root_partition()[1]
    for s, n in SLOTS.items():
        if n == part:
            return s
    raise OtaError(f'rootfs on partition {part}, not an A/B slot')


def part_dev(disk, slot):
    return f'{disk}p{SLOTS[slot]}'


def part_size(dev):
    with open(f'/sys/class/block/{os.path.basename(dev)}/size') as f:
        return int(f.read()) * 512


def mounted(dev):
    real = os.path.realpath(dev)
    with open('/proc/mounts') as f:
        return any(os.path.realpath(ln.split()[0]) == real for ln in f)


def has_ext4_rootfs(dev):
    """Superblock magic and label "rootfs", without mounting."""
    try:
        with open(dev, 'rb') as f:
            f.seek(1024)
            sb = f.read(1024)
    except OSError:
        return False
    return (struct.unpack_from('<H', sb, 56)[0] == 0xEF53
            and sb[120:136].rstrip(b'\0') == b'rootfs')


# -- boot state (U-Boot "env export -c" format, two copies) ------------------
def _pack(state):
    data = b''.join(f'{k}={state[k]}'.encode() + b'\0' for k in STATE_KEYS) + b'\0'
    data = data.ljust(STATE_SIZE - 4, b'\0')
    return struct.pack('<I', zlib.crc32(data)) + data


def _unpack(blob):
    if len(blob) != STATE_SIZE:
        return None
    crc, data = struct.unpack_from('<I', blob)[0], blob[4:]
    if zlib.crc32(data) != crc:
        return None
    out = {}
    for item in data.split(b'\0'):
        if not item:
            break
        k, _, v = item.decode(errors='replace').partition('=')
        out[k] = v
    return out


def _defaults(state):
    """The same defaults as pb_load in pb624-ab.h."""
    s = dict(state)
    if s.get('BOOT_ORDER') != 'B A':
        s['BOOT_ORDER'] = 'A B'
    s.setdefault('BOOT_A_LEFT', str(ATTEMPTS))
    s.setdefault('BOOT_B_LEFT', '0')
    s.setdefault('PB_TRIAL', 'none')
    return {k: s[k] for k in STATE_KEYS}


def read_state(disk=None):
    disk = disk or system_disk()
    with open(disk, 'rb') as f:
        for off in STATE_COPIES:
            f.seek(off)
            s = _unpack(f.read(STATE_SIZE))
            if s is not None:
                return _defaults(s)
    return _defaults({})


def write_state(state, disk=None):
    disk = disk or system_disk()
    blob = _pack(_defaults(state))
    fd = os.open(disk, os.O_RDWR | os.O_SYNC)
    try:
        for off in STATE_COPIES:        # copy 1 first, as U-Boot does
            os.pwrite(fd, blob, off)
            os.fsync(fd)
    finally:
        os.close(fd)


def _left(state, slot):
    try:
        return int(state[f'BOOT_{slot}_LEFT'])
    except ValueError:
        return 0


def _set(state, slot, left, first=None):
    state[f'BOOT_{slot}_LEFT'] = str(left)
    if first:
        state['BOOT_ORDER'] = f'{first} {other(first)}'


# -- history (/data/state/ota.json, for the status page) --------------------
def _record(**kw):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        p = os.path.join(STATE_DIR, 'ota.json')
        kw['time'] = time.strftime('%Y-%m-%d %H:%M:%S')
        with open(p + '.tmp', 'w') as f:
            json.dump(kw, f)
        os.replace(p + '.tmp', p)
        os.sync()
    except OSError as e:
        print(f'ota: cannot record {kw}: {e}')


def last_result():
    try:
        with open(os.path.join(STATE_DIR, 'ota.json')) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# -- versions ---------------------------------------------------------------
def _read_version(root='/'):
    try:
        with open(os.path.join(root, VERSION_FILE.lstrip('/'))) as f:
            return f.read().strip()
    except OSError:
        return None


class _Mounted:
    """Mount a slot read-only (noload: no journal replay, nothing written)."""
    def __init__(self, dev):
        self.dev = dev

    def __enter__(self):
        os.makedirs(MNT, exist_ok=True)
        r = subprocess.run(['mount', '-t', 'ext4', '-o', 'ro,noload', self.dev, MNT],
                           capture_output=True, text=True)
        if r.returncode:
            raise OtaError(f'cannot mount {self.dev}: {r.stderr.strip()}')
        return MNT

    def __exit__(self, *exc):
        subprocess.run(['umount', MNT])


def slot_version(disk, slot):
    """Version of a slot, None if it holds no PB624 rootfs."""
    if slot == running_slot():
        return _read_version()
    dev = part_dev(disk, slot)
    if not os.path.exists(dev) or not has_ext4_rootfs(dev) or mounted(dev):
        return None
    try:
        with _Mounted(dev) as m:
            return _read_version(m) or 'unknown'
    except OtaError:
        return None


def status(versions=True):
    disk = system_disk()
    cur = running_slot()
    st = read_state(disk)
    out = {
        'slot': cur,
        'version': _read_version(),
        'trial': st['PB_TRIAL'] == cur,
        'boot': st,
        'slot_b': os.path.exists(part_dev(disk, 'B')),
        'last': last_result(),
    }
    if versions and out['slot_b']:
        out['other'] = {'slot': other(cur), 'version': slot_version(disk, other(cur))}
    return out


# -- boot confirmation (called by the app) -----------------------------------
def boot_check():
    """Run once at start.  Returns 'trial' (an update waiting to be confirmed
    by confirm()), 'rolled_back' (an update did not come up and the old slot
    booted again) or 'ok'; the running slot's counter is reset unless in a
    trial."""
    disk = system_disk()
    cur = running_slot()
    st = read_state(disk)
    trial = st['PB_TRIAL']
    if trial == cur:
        return 'trial'
    if trial in SLOTS:                  # the trial slot gave up, we are back
        st['PB_TRIAL'] = 'none'
        _set(st, trial, 0)
        _set(st, cur, ATTEMPTS, first=cur)
        write_state(st, disk)
        prev = last_result() or {}
        _record(result='rolled back', slot=trial, version=prev.get('version'),
                running=cur)
        print(f'ota: update in slot {trial} did not come up, back on slot {cur}')
        return 'rolled_back'
    if _left(st, cur) != ATTEMPTS or not st['BOOT_ORDER'].startswith(cur):
        _set(st, cur, ATTEMPTS, first=cur)
        write_state(st, disk)
    return 'ok'


def confirm():
    """The running slot is good: keep booting it."""
    disk = system_disk()
    cur = running_slot()
    st = read_state(disk)
    was_trial = st['PB_TRIAL'] == cur
    st['PB_TRIAL'] = 'none'
    _set(st, cur, ATTEMPTS, first=cur)
    write_state(st, disk)
    if was_trial:
        _record(result='ok', slot=cur, version=_read_version())
        print(f'ota: slot {cur} confirmed ({_read_version()})')


def _trial(disk, st, slot):
    st['PB_TRIAL'] = slot
    _set(st, slot, TRIAL_ATTEMPTS, first=slot)
    _set(st, other(slot), ATTEMPTS)
    write_state(st, disk)


def switch():
    """Boot the other slot next, as a trial (falls back if it fails)."""
    disk = system_disk()
    cur = running_slot()
    tgt = other(cur)
    st = read_state(disk)
    if st['PB_TRIAL'] == cur:
        raise OtaError(f'slot {cur} is itself an unconfirmed trial')
    ver = slot_version(disk, tgt)
    if not ver:
        raise OtaError(f'slot {tgt} holds no system')
    _trial(disk, st, tgt)
    _record(result='trial', slot=tgt, version=ver, running=cur)
    return tgt, ver


# -- install ----------------------------------------------------------------
def check_image(path):
    """Quick checks before an upload is queued: gzip, size, ext4 rootfs.
    Returns the uncompressed size.  The full check is in install()."""
    with open(path, 'rb') as f:
        if f.read(2) != b'\x1f\x8b':
            raise OtaError('not a gzip file')
        f.seek(-4, os.SEEK_END)
        size = struct.unpack('<I', f.read(4))[0]   # ISIZE (images < 4 GiB)
    try:
        with gzip.open(path, 'rb') as g:
            head = g.read(2048)
    except (OSError, EOFError) as e:
        raise OtaError(f'not a valid gzip file: {e}')
    if (len(head) < 2048 or struct.unpack_from('<H', head, 1024 + 56)[0] != 0xEF53
            or head[1024 + 120:1024 + 136].rstrip(b'\0') != b'rootfs'):
        raise OtaError('not a PB624 rootfs image (ext4 labelled "rootfs")')
    if size % 4096 or size < (64 << 20):
        raise OtaError(f'odd image size {size}')
    return size


def install(path, progress=None):
    """Write the gzipped rootfs image at path to the slot that is not
    running, verify it and make it the next boot (as a trial).  Nothing
    that is booted is touched until the very last state write."""
    say = progress or (lambda msg: print(f'ota: {msg}'))
    disk = system_disk()
    cur = running_slot()
    tgt = other(cur)
    dev = part_dev(disk, tgt)
    st = read_state(disk)
    if st['PB_TRIAL'] == cur:
        raise OtaError(f'slot {cur} is still an unconfirmed trial')
    if not os.path.exists(dev):
        raise OtaError(f'no slot {tgt} ({dev}): card has the old layout, see README')
    if mounted(dev):
        raise OtaError(f'{dev} is mounted')
    size = check_image(path)
    psize = part_size(dev)
    if size > psize:
        raise OtaError(f'image is {size} bytes, slot {tgt} only {psize}')

    # the target is about to be overwritten: never boot it from now on
    if _left(st, tgt) or st['PB_TRIAL'] != 'none':
        st['PB_TRIAL'] = 'none'
        _set(st, tgt, 0, first=cur)
        _set(st, cur, ATTEMPTS)
        write_state(st, disk)

    say(f'writing slot {tgt} ({dev}, {size >> 20} MiB)')
    h = hashlib.sha256()
    n = 0
    t0 = time.time()
    try:
        with gzip.open(path, 'rb') as src:
            fd = os.open(dev, os.O_WRONLY)
            try:
                while True:
                    buf = src.read(CHUNK)
                    if not buf:
                        break
                    if n + len(buf) > psize:
                        raise OtaError('image larger than its header says')
                    os.write(fd, buf)
                    h.update(buf)
                    n += len(buf)
                os.fsync(fd)
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            finally:
                os.close(fd)
    except (OSError, EOFError, zlib.error) as e:
        raise OtaError(f'write failed: {e}')
    if n != size:
        raise OtaError(f'image is {n} bytes, header said {size}')
    say(f'written in {time.time() - t0:.0f} s, verifying')

    v = hashlib.sha256()
    with open(dev, 'rb', buffering=0) as f:
        left = n
        while left:
            buf = f.read(min(CHUNK, left))
            if not buf:
                break
            v.update(buf)
            left -= len(buf)
    if v.digest() != h.digest():
        raise OtaError(f'read-back of slot {tgt} does not match (bad card?)')

    with _Mounted(dev) as m:
        for need in ('boot/zImage', 'boot/sun5i-a13-pocketbook-624.dtb',
                     'usr/lib/pb624-frame/main.py'):
            if not os.path.isfile(os.path.join(m, need)):
                raise OtaError(f'image has no /{need}')
        try:
            with open(os.path.join(m, 'boot/extlinux/extlinux.conf')) as f:
                ext = f.read()
        except OSError:
            ext = ''
        if '${pb_root}' not in ext:
            raise OtaError('image is not A/B aware (extlinux.conf without ${pb_root})')
        ver = _read_version(m) or 'unknown'

    st = read_state(disk)
    _trial(disk, st, tgt)
    _record(result='trial', slot=tgt, version=ver, running=cur,
            sha256=h.hexdigest())
    say(f'slot {tgt} = {ver}, boots next as a trial')
    return {'slot': tgt, 'version': ver, 'sha256': h.hexdigest()}


# -- U-Boot -----------------------------------------------------------------
def bootloader(path):
    """Rewrite U-Boot (SPL at 8 KiB).  There is only one copy: if this is
    interrupted the board needs FEL (USB) to recover, so it is only done
    when the image actually differs."""
    with open(path, 'rb') as f:
        new = f.read()
    if new[4:12] != b'eGON.BT0':
        raise OtaError('not a sunxi SPL image (no eGON.BT0 header)')
    if len(new) > UBOOT_MAX:
        raise OtaError(f'U-Boot is {len(new)} bytes, would overwrite the boot state')
    disk = system_disk()
    with open(disk, 'rb') as f:
        f.seek(UBOOT_OFFSET)
        if f.read(len(new)) == new:
            return 'unchanged'
    fd = os.open(disk, os.O_RDWR)
    try:
        os.pwrite(fd, new, UBOOT_OFFSET)
        os.fsync(fd)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)
    with open(disk, 'rb', buffering=0) as f:
        f.seek(UBOOT_OFFSET)
        if f.read(len(new)) != new:
            raise OtaError('U-Boot read-back does not match: DO NOT power off, retry')
    return 'written'


# -- old cards: add slot B --------------------------------------------------
def _mbr_entry(mbr, i):
    off = 446 + 16 * i
    return list(struct.unpack_from('<B3sB3sII', mbr, off))


def make_slot_b():
    """Old layout (p1 rootfs, p2 data to the end of the card): shrink the
    data filesystem and partition by SLOT_SECTORS and put slot B (p3) in
    the freed space at the end.  /data must not be mounted."""
    disk = system_disk()
    p2 = disk + 'p2'
    if os.path.exists(disk + 'p3'):
        raise OtaError('slot B already exists')
    if mounted(p2):
        raise OtaError(f'{p2} is mounted: stop the services and umount /data first')
    with open(disk, 'rb') as f:
        mbr = bytearray(f.read(512))
    if mbr[510:512] != b'\x55\xaa' or struct.unpack_from('<I', mbr, 440)[0] != DISK_SIGNATURE:
        raise OtaError('not a PB624 card')
    e2 = _mbr_entry(mbr, 1)
    if _mbr_entry(mbr, 2)[5] or _mbr_entry(mbr, 3)[5]:
        raise OtaError('partition 3 or 4 is in use')
    with open(f'/sys/block/{os.path.basename(disk)}/size') as f:
        total = int(f.read())
    start2, size2 = e2[4], e2[5]
    b_start = (total - SLOT_SECTORS) // 2048 * 2048
    new2 = b_start - start2
    if start2 + size2 > total or new2 < (256 << 11):
        raise OtaError(f'unexpected layout: p2 {start2}+{size2}, card {total}')

    def run(*cmd):
        print('+', ' '.join(cmd))
        r = subprocess.run(cmd)
        if r.returncode not in (0, 1) or (r.returncode and cmd[0] != 'e2fsck'):
            raise OtaError(f'{cmd[0]} failed ({r.returncode})')
    run('e2fsck', '-f', '-y', p2)
    run('resize2fs', p2, f'{new2}s')
    e2[5] = new2
    struct.pack_into('<B3sB3sII', mbr, 446 + 16, *e2)
    struct.pack_into('<B3sB3sII', mbr, 446 + 32,
                     0, b'\xfe\xff\xff', 0x83, b'\xfe\xff\xff', b_start, SLOT_SECTORS)
    fd = os.open(disk, os.O_RDWR | os.O_SYNC)
    try:
        os.pwrite(fd, bytes(mbr), 0)
        os.fsync(fd)
    finally:
        os.close(fd)
    st = read_state(disk)
    _set(st, 'B', 0)
    write_state(st, disk)
    print(f'p2 now {new2} sectors, p3 (slot B) at {b_start}+{SLOT_SECTORS}; reboot now')


# -- CLI --------------------------------------------------------------------
def main(argv):
    cmd = argv[1] if len(argv) > 1 else 'status'
    try:
        if cmd == 'status':
            print(json.dumps(status(), indent=1))
        elif cmd == 'install' and len(argv) > 2:
            install(argv[2])
            if '--no-reboot' not in argv:
                print('ota: rebooting')
                sys.stdout.flush()
                subprocess.run(['reboot'])
        elif cmd == 'switch':
            slot, ver = switch()
            print(f'ota: slot {slot} ({ver}) boots next as a trial; reboot to start it')
        elif cmd == 'confirm':
            confirm()
        elif cmd == 'bootloader' and len(argv) > 2:
            print(f'ota: U-Boot {bootloader(argv[2])}')
        elif cmd == 'make-slot-b':
            make_slot_b()
        else:
            print(__doc__.split('\n\n')[2])
            return 2
    except OtaError as e:
        print(f'ota: {e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
