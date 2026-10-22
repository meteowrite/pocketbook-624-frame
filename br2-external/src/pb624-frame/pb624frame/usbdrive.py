"""USB photo drive: the FAT32 image a PC sees over USB (set up by
/etc/init.d/S04usb).  While a PC has it, the frame keeps its hands off;
when the PC ejects it or the cable is unplugged the image is mounted
read-only and its pictures are mirrored into the photo folder: new and
changed ones imported (scaled, like web uploads), deleted ones removed.
Photos uploaded through the web UI are never touched."""
import glob
import json
import os
import subprocess
import time

IMG = '/data/usb/photos.img'
LUN = '/sys/kernel/config/usb_gadget/pb624/functions/mass_storage.usb0/lun.0/file'
MNT = '/run/pb624-usb'
BATCH = 10                   # imports per poll, so the frame stays responsive


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ''


class UsbDrive:
    def __init__(self, app, exts):
        self.app = app
        self.exts = exts
        self.state_path = os.path.join(app.data, 'state', 'usb-drive.json')
        self.dirty = True                # import once at start
        self.summary = ''
        try:
            with open(self.state_path) as f:
                self.known = json.load(f)   # drive path -> {size, mtime, local}
        except (OSError, ValueError):
            self.known = {}

    @property
    def available(self):
        return os.path.exists(IMG) and os.path.exists(LUN)

    def host_connected(self):
        return any(_read(p) == 'configured' for p in glob.glob('/sys/class/udc/*/state'))

    def status(self):
        if not self.available:
            return None
        if self.host_connected() and _read(LUN):
            return 'in use by the PC'
        return self.summary or 'ready'

    def poll(self):
        """From the main loop (every few s)."""
        if not self.available:
            return
        exported = bool(_read(LUN))
        if self.host_connected() and exported:
            self.dirty = True            # the PC may be changing it
            return
        if self.dirty:
            done = self._import()
            if done is None:
                return                   # more to do, next poll
            self.dirty = False
        if not exported:                 # ejected by the PC: offer it again
            try:
                with open(LUN, 'w') as f:
                    f.write(IMG)
            except OSError as e:
                print(f'usb drive: re-attach: {e}')

    # -- mirror -----------------------------------------------------------------
    def _save(self):
        tmp = self.state_path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(self.known, f)
        os.replace(tmp, self.state_path)

    def _import(self):
        """One batch; returns None while there is more to do."""
        os.makedirs(MNT, exist_ok=True)
        r = subprocess.run(['mount', '-t', 'vfat', '-o', 'ro,loop,utf8', IMG, MNT],
                           capture_output=True, text=True)
        if r.returncode:
            print(f'usb drive: mount: {r.stderr.strip()}')
            return True                  # nothing to do until it changes
        try:
            return self._mirror()
        finally:
            subprocess.run(['umount', MNT])

    def _mirror(self):
        found = {}
        for root, dirs, files in os.walk(MNT):
            dirs[:] = [d for d in dirs if not d.startswith('.')
                       and d != 'System Volume Information']
            for fn in files:
                if fn.startswith('.') or not fn.lower().endswith(self.exts):
                    continue
                p = os.path.join(root, fn)
                st = os.stat(p)
                found[os.path.relpath(p, MNT)] = (st.st_size, int(st.st_mtime))
        added = removed = 0
        for rel in [r for r in self.known if r not in found]:
            self._remove_local(self.known.pop(rel).get('local'))
            removed += 1
        todo = [r for r, (size, mtime) in sorted(found.items())
                if r not in self.known or self.known[r]['size'] != size
                or self.known[r]['mtime'] != mtime]
        for rel in todo[:BATCH]:
            size, mtime = found[rel]
            self._remove_local(self.known.get(rel, {}).get('local'))   # changed
            try:
                with open(os.path.join(MNT, rel), 'rb') as f:
                    local = self.app.add_photo(os.path.basename(rel), f.read())
            except Exception as e:       # not a picture after all, truncated...
                print(f'usb drive: {rel}: {type(e).__name__}: {e}')
                local = None
            self.known[rel] = {'size': size, 'mtime': mtime, 'local': local}
            added += 1
        if added or removed:
            self._save()
            os.sync()
            print(f'usb drive: {added} imported, {removed} removed,'
                  f' {max(0, len(todo) - BATCH)} to go')
        n = sum(1 for v in self.known.values() if v.get('local'))
        self.summary = f'{n} photos from the drive, last checked {time.strftime("%H:%M")}'
        return None if len(todo) > BATCH else True

    def _remove_local(self, name):
        if name and name in self.app.photo_list():
            self.app.delete_photo(name)
