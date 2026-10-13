"""Weather icons for Home Assistant conditions, drawn with Pillow.

Each icon is drawn on a 4x supersampled 100x100 grid and scaled down, black
and grey on white, so it pastes cleanly onto the white e-ink canvas."""
import math

from PIL import Image, ImageDraw

K = 4                       # supersampling
BLACK, DARK, MID, LIGHT, WHITE = 0, 68, 136, 204, 255


class _Pen:
    def __init__(self, d):
        self.d = d

    def _p(self, *xy):
        return [v * K for v in xy]

    def circle(self, cx, cy, r, fill=None, outline=None, width=0):
        self.d.ellipse(self._p(cx - r, cy - r, cx + r, cy + r), fill=fill,
                       outline=outline, width=width * K)

    def line(self, pts, fill=BLACK, width=4):
        self.d.line([v * K for p in pts for v in p], fill=fill, width=width * K)
        for x, y in (pts[0], pts[-1]):     # round caps
            self.circle(x, y, width / 2, fill=fill)

    def poly(self, pts, fill=BLACK):
        self.d.polygon([v * K for p in pts for v in p], fill=fill)


def _sun(p, cx, cy, r, rays=True):
    p.circle(cx, cy, r, fill=WHITE, outline=BLACK, width=5)
    p.circle(cx, cy, r - 9, fill=LIGHT)
    if rays:
        for i in range(8):
            a = i * math.pi / 4
            p.line([(cx + (r + 7) * math.cos(a), cy + (r + 7) * math.sin(a)),
                    (cx + (r + 17) * math.cos(a), cy + (r + 17) * math.sin(a))],
                   width=5)


def _moon(p, cx, cy, r):
    p.circle(cx, cy, r, fill=MID, outline=BLACK, width=5)
    p.circle(cx + r * 0.55, cy - r * 0.35, r * 0.85, fill=WHITE)


def _cloud(p, x, y, w, fill=WHITE):
    """Cloud with its base line at y, left edge x, width w."""
    h = w * 0.55
    parts = [(x + w * 0.27, y - h * 0.38, w * 0.24),
             (x + w * 0.55, y - h * 0.58, w * 0.30),
             (x + w * 0.80, y - h * 0.33, w * 0.20)]
    for cx, cy, r in parts:                 # outline pass
        p.circle(cx, cy, r + 5, fill=BLACK)
    p.d.rounded_rectangle([v * K for v in (x - 5, y - h * 0.45 - 5, x + w + 5, y + 5)],
                          radius=h * 0.45 * K, fill=BLACK)
    for cx, cy, r in parts:                 # fill pass
        p.circle(cx, cy, r, fill=fill)
    p.d.rounded_rectangle([v * K for v in (x, y - h * 0.45, x + w, y)],
                          radius=h * 0.4 * K, fill=fill)


def _drops(p, n, y0, kind):
    xs = [32 + i * 18 for i in range(n)]
    for i, x in enumerate(xs):
        yy = y0 + (6 if i % 2 else 0)
        if kind == 'rain':
            p.line([(x + 4, yy), (x - 3, yy + 16)], width=5)
        elif kind == 'snow':
            for a in range(3):
                t = a * math.pi / 3
                p.line([(x - 6 * math.cos(t), yy + 8 - 6 * math.sin(t)),
                        (x + 6 * math.cos(t), yy + 8 + 6 * math.sin(t))],
                       width=3)
        elif kind == 'hail':
            p.circle(x, yy + 8, 5, fill=BLACK)


def _bolt(p, x, y):
    p.poly([(x + 10, y), (x - 4, y + 22), (x + 5, y + 22), (x - 2, y + 40),
            (x + 18, y + 14), (x + 8, y + 14), (x + 16, y)], fill=BLACK)


def _wind(p, y0):
    for i, (y, l) in enumerate(((y0, 62), (y0 + 16, 74), (y0 + 32, 50))):
        x0 = 14 + i * 4
        p.line([(x0, y), (x0 + l, y)], width=5)
        p.d.arc([v * K for v in (x0 + l - 8, y - 16, x0 + l + 8, y)],
                start=90, end=330, fill=BLACK, width=5 * K)


def _fog(p, y0):
    for i in range(4):
        x = 16 if i % 2 else 22
        p.line([(x, y0 + i * 12), (x + 62, y0 + i * 12)], fill=DARK, width=5)


def draw(condition):
    """100x100 icon (4x supersampled) for a HA weather condition."""
    im = Image.new('L', (100 * K, 100 * K), WHITE)
    p = _Pen(ImageDraw.Draw(im))
    c = condition or 'exceptional'
    if c == 'sunny':
        _sun(p, 50, 50, 24)
    elif c == 'clear-night':
        _moon(p, 50, 50, 30)
    elif c == 'partlycloudy':
        _sun(p, 62, 36, 18)
        _cloud(p, 10, 82, 66)
    elif c == 'cloudy':
        _cloud(p, 30, 54, 56, fill=LIGHT)
        _cloud(p, 8, 82, 70)
    elif c in ('rainy', 'pouring', 'lightning-rainy', 'snowy-rainy', 'hail'):
        _cloud(p, 10, 60, 80, fill=LIGHT if c == 'pouring' else WHITE)
        if c == 'lightning-rainy':
            _drops(p, 2, 68, 'rain')
            _bolt(p, 66, 58)
        elif c == 'snowy-rainy':
            _drops(p, 2, 68, 'rain')
            for a in range(3):              # one flake next to the drops
                t = a * math.pi / 3
                p.line([(72 - 6 * math.cos(t), 76 - 6 * math.sin(t)),
                        (72 + 6 * math.cos(t), 76 + 6 * math.sin(t))], width=3)
        elif c == 'hail':
            _drops(p, 3, 68, 'hail')
        else:
            _drops(p, 4 if c == 'pouring' else 3, 68, 'rain')
    elif c == 'snowy':
        _cloud(p, 10, 60, 80)
        _drops(p, 3, 66, 'snow')
    elif c == 'lightning':
        _cloud(p, 10, 60, 80, fill=LIGHT)
        _bolt(p, 42, 56)
    elif c == 'fog':
        _cloud(p, 14, 50, 72)
        _fog(p, 60)
    elif c in ('windy', 'windy-variant'):
        if c == 'windy-variant':
            _cloud(p, 30, 40, 56)
        _wind(p, 52 if c == 'windy-variant' else 34)
    else:                                   # exceptional / unknown
        p.circle(50, 50, 34, fill=WHITE, outline=BLACK, width=6)
        p.line([(50, 30), (50, 56)], width=8)
        p.circle(50, 70, 5, fill=BLACK)
    return im


def icon(condition, size):
    return draw(condition).resize((size, size), Image.LANCZOS)
