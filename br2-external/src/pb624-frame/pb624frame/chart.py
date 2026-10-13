"""History charts for the e-ink panel (16 greys, no anti-aliasing needed)."""
import datetime
import math

from PIL import Image, ImageDraw

BLACK, DARK, MID, LIGHT, WHITE = 0, 68, 136, 204, 255
# line styles per series: (grey, width, dash length or 0)
STYLES = [(BLACK, 3, 0), (DARK, 2, 8), (MID, 2, 0)]


def group_by_unit(series, max_groups=4):
    """[[series, ...], ...] grouped by unit, in the given order."""
    groups = {}
    for s in series:
        groups.setdefault(s.get('unit', ''), []).append(s)
    return list(groups.values())[:max_groups]


def _columns(points, t0, t1, n):
    """Average of each of n time columns; None where there is no data.
    Values are held between state changes (HA records changes only)."""
    cols = [None] * n
    if not points:
        return cols
    span = (t1 - t0) / n
    sums = [0.0] * n
    wts = [0.0] * n
    pts = sorted(points)
    for i, (t, v) in enumerate(pts):
        if v is None:
            continue
        te = pts[i + 1][0] if i + 1 < len(pts) else t1
        a, b = max(t, t0), min(te, t1)
        if b <= a:
            continue
        c0, c1 = int((a - t0) / span), min(n - 1, int((b - t0) / span))
        for c in range(max(0, c0), c1 + 1):
            ca, cb = t0 + c * span, t0 + (c + 1) * span
            w = min(b, cb) - max(a, ca)
            if w > 0:
                sums[c] += v * w
                wts[c] += w
    for c in range(n):
        if wts[c] > 0:
            cols[c] = sums[c] / wts[c]
    return cols


def _nice_range(lo, hi, ticks=4):
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    step = (hi - lo) / ticks
    mag = 10 ** math.floor(math.log10(step))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= step)
    return math.floor(lo / step) * step, math.ceil(hi / step) * step, step


def _fmt(v, step):
    return f'{v:.0f}' if step >= 1 else f'{v:.1f}'


def _dashed(d, pts, fill, width, dash, gap=None):
    """Polyline drawn as dashes of `dash` px with `gap` px gaps, measured
    along the path; each dash is one polyline so joints stay clean."""
    gap = gap or dash * 0.6
    cur, left, on = [pts[0]], dash, True
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        pos = 0.0
        while seg - pos > left:
            pos += left
            p = (x0 + (x1 - x0) * pos / seg, y0 + (y1 - y0) * pos / seg)
            if on:
                cur.append(p)
                if len(cur) > 1:
                    d.line(cur, fill=fill, width=width, joint='curve')
            cur = [p]
            on = not on
            left = dash if on else gap
        left -= seg - pos
        cur.append((x1, y1))
    if on and len(cur) > 1:
        d.line(cur, fill=fill, width=width, joint='curve')


def short_names(series):
    """Drop words every series name shares (e.g. 'Temperature')."""
    words = [s['name'].split() for s in series]
    if len(words) < 2:
        return [s['name'] for s in series]
    common = set(words[0]).intersection(*words[1:])
    out = [' '.join(w for w in ws if w not in common) or ' '.join(ws) for ws in words]
    return out


def panel(im, box, series, now, hours, font, days_names, title=None):
    """Draw one chart panel for series sharing a unit into box."""
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = box
    f_small, f_legend = font(15), font(17, True)
    unit = series[0].get('unit', '') if series else ''
    t1 = now.timestamp()
    t0 = t1 - hours * 3600

    # legend: name and current value per series, wrapping onto more lines
    title_w = d.textlength(title, font=f_small) + 16 if title else 0
    lx, ly = x0 + 4, y0
    names = short_names(series[:len(STYLES)])
    for i, s in enumerate(series[:len(STYLES)]):
        grey, width, dash = STYLES[i]
        last = next((v for _, v in reversed(s['points']) if v is not None), None)
        label = names[i] + (f'  {last:.1f}{unit}' if last is not None else '')
        lw = 30 + d.textlength(label, font=f_legend)
        if lx + lw > x1 - (title_w if ly == y0 else 0) and lx > x0 + 4:
            lx, ly = x0 + 4, ly + 22
        if dash:
            _dashed(d, [(lx, ly + 10), (lx + 24, ly + 10)], grey, width, 7)
        else:
            d.line((lx, ly + 10, lx + 24, ly + 10), fill=grey, width=width)
        d.text((lx + 30, ly), label, font=f_legend, fill=BLACK)
        lx += lw + 22
    if title:
        d.text((x1, y0), title, font=f_small, fill=MID, anchor='ra')
    py0 = ly + 26

    # plot area leaves room for the y labels (left) and day labels (bottom)
    px0, px1, py1 = x0 + 48, x1 - 4, y1 - 20
    n = max(2, px1 - px0)
    cols = [_columns(s['points'], t0, t1, n) for s in series[:len(STYLES)]]
    vals = [v for c in cols for v in c if v is not None]
    if not vals:
        d.text(((x0 + x1) / 2, (py0 + py1) / 2), 'no data', font=font(20),
               fill=MID, anchor='mm')
        return
    lo, hi, step = _nice_range(min(vals), max(vals))

    def ypos(v):
        return py1 - (v - lo) / (hi - lo) * (py1 - py0)

    # horizontal grid + y labels
    v = lo
    while v <= hi + step / 2:
        y = ypos(v)
        d.line((px0, y, px1, y), fill=LIGHT, width=1)
        d.text((px0 - 6, y), _fmt(v, step) + (unit if v == hi else ''),
               font=f_small, fill=DARK, anchor='rm')
        v += step

    # vertical day (or 6-hour) separators with labels
    tz = now.tzinfo
    t = datetime.datetime.fromtimestamp(t0, tz).replace(minute=0, second=0, microsecond=0)
    hour_step = 24 if hours > 48 else 6
    t = t.replace(hour=0) if hour_step == 24 else t.replace(hour=t.hour - t.hour % 6)
    while t.timestamp() <= t1:
        if t.timestamp() >= t0:
            x = px0 + (t.timestamp() - t0) / (t1 - t0) * (px1 - px0)
            d.line((x, py0, x, py1), fill=LIGHT, width=1)
            if hour_step == 24:
                lab = days_names[t.weekday()]
                nxt = x + 24 * 3600 / (t1 - t0) * (px1 - px0)
                if nxt - x > 30:        # label in the middle of its day
                    d.text(((x + min(nxt, px1)) / 2, py1 + 3), lab, font=f_small,
                           fill=DARK, anchor='ma')
            else:
                d.text((x, py1 + 3), f'{t.hour:02d}', font=f_small, fill=DARK, anchor='ma')
        t += datetime.timedelta(hours=hour_step)
    d.rectangle((px0, py0, px1, py1), outline=MID, width=1)

    # series, drawn back to front so the first one is on top
    for i in reversed(range(len(cols))):
        grey, width, dash = STYLES[i]
        run = []
        for c, v in enumerate(cols[i] + [None]):
            if v is None:
                if len(run) > 1:
                    if dash:
                        _dashed(d, run, grey, width, dash)
                    else:
                        d.line(run, fill=grey, width=width, joint='curve')
                run = []
            else:
                run.append((px0 + c, ypos(v)))

    # min / max markers of the first series
    c0 = [(v, c) for c, v in enumerate(cols[0]) if v is not None]
    if c0:
        (vmin, cmin), (vmax, cmax) = min(c0), max(c0)
        for v, c, anchor, dy in ((vmax, cmax, 'mb', -4), (vmin, cmin, 'mt', 4)):
            x = min(max(px0 + c, px0 + 20), px1 - 20)
            d.text((x, ypos(v) + dy), f'{v:.1f}', font=f_small, fill=BLACK, anchor=anchor)


def screen(size, history, now, hours, font, days_names, title, status_fn=None):
    """Full-screen history: one panel per unit group (up to 4)."""
    W, H = size
    im = Image.new('L', size, WHITE)
    d = ImageDraw.Draw(im)
    d.text((16, 8), title, font=font(26, True), fill=BLACK)
    if status_fn:
        status_fn(d, W)
    d.line((12, 46, W - 12, 46), fill=BLACK, width=2)
    groups = group_by_unit(list(history.values()))
    if not groups:
        d.text((W / 2, H / 2), 'no history entities set', font=font(28),
               fill=MID, anchor='mm')
        return im
    top, gap = 56, 12
    ph = (H - top - 26 - gap * (len(groups) - 1)) / len(groups)
    for i, g in enumerate(groups):
        y = top + i * (ph + gap)
        panel(im, (12, int(y), W - 12, int(y + ph)), g, now, hours, font, days_names)
    d.text((W - 12, H - 6), f'{now:%H:%M}', font=font(15), fill=MID, anchor='rd')
    return im
