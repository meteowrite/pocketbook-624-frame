"""E-ink output through epdshow (800x600 ED060SCE, 16 grey levels)."""
import os
import subprocess
import threading

from PIL import Image, ImageChops

W, H = 800, 600
MODE_INIT, MODE_DU, MODE_GC16, MODE_GL16 = 0, 1, 2, 3

_PAL = Image.new('P', (1, 1))
_PAL.putpalette([v for i in range(16) for v in (i * 17,) * 3] + [0] * 720)


def to16(img, dither):
    """Quantise to the panel's 16 grey levels (0, 17, ..., 255)."""
    d = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
    return img.convert('RGB').quantize(palette=_PAL, dither=d).convert('L')


class Display:
    def __init__(self, rotation=0, full_every=12, sim_dir=None):
        self.rotation = rotation
        self.full_every = full_every
        self.sim_dir = sim_dir
        self.updates = 0           # since the last INIT
        self.partials = 0          # fast updates since the last full one
        self.partial_full_every = 60
        self.lock = threading.Lock()
        tmp = sim_dir or '/tmp'
        self.pgm = os.path.join(tmp, 'frame.pgm')
        self.preview = os.path.join(tmp, 'screen.png')
        self.state = os.path.join(tmp, 'epd.state')
        # no state file = panel content unknown (fresh boot): clean first
        self.need_init = not os.path.exists(self.state)
        self.last = None           # panel-orientation image on the panel
        try:
            with open(self.state, 'rb') as f:
                self.last = Image.frombytes('L', (W, H), f.read(W * H))
        except (OSError, ValueError):
            pass

    @property
    def size(self):
        """Logical canvas size for the current rotation."""
        return (H, W) if self.rotation in (90, 270) else (W, H)

    def _epdshow(self, *args):
        if self.sim_dir:
            return True
        r = subprocess.run(['epdshow', '-s', self.state, *args],
                           capture_output=True, text=True)
        if r.returncode:
            print('epdshow failed:', r.stdout.strip(), r.stderr.strip())
        return r.returncode == 0

    def show(self, img, dither=False, full=False, fast=False):
        """Show a logical-orientation image; returns the panel image.
        fast: update only the pixels that change, without the full-screen
        flash (DU when they all end black/white, else GL16).  Ghosting adds
        up, so every partial_full_every fast updates a normal one is done."""
        with self.lock:
            img = to16(img.convert('L').resize(self.size)
                       if img.size != self.size else img.convert('L'), dither)
            img.save(self.preview)
            panel = img.rotate(-self.rotation, expand=True) if self.rotation else img
            if fast and self.last is not None and not (
                    full or self.need_init or self.partials >= self.partial_full_every):
                mask = ImageChops.difference(panel, self.last).point(
                    lambda v: 255 if v else 0)
                if not mask.getbbox():
                    return True
                greys = sum(panel.histogram(mask)[1:255])
                panel.save(self.pgm)
                mode = MODE_GL16 if greys else MODE_DU
                ok = self._epdshow('-p', '-m', str(mode), self.pgm)
                self.partials += 1
                self.last = panel if ok else None
                return ok
            panel.save(self.pgm)   # 'L' -> binary PGM (P5)
            if full or self.need_init or self.updates >= self.full_every:
                self._epdshow('-m', str(MODE_INIT), 'white')
                self.need_init = False
                self.updates = 0
            ok = self._epdshow('-m', str(MODE_GC16), self.pgm)
            self.updates += 1
            self.partials = 0
            self.last = panel if ok else None
            return ok
