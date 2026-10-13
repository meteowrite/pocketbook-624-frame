"""Screens: weather dashboard, photo, info/setup, message."""
import datetime
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from . import chart, icons

FONT_DIRS = ['/usr/share/fonts/dejavu', '/usr/share/fonts/truetype/dejavu']
BLACK, DARK, MID, LIGHT, WHITE = 0, 68, 136, 204, 255

TEXT = {
    'en': {
        'days': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
        'days_long': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday',
                      'Saturday', 'Sunday'],
        'months': ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                   'August', 'September', 'October', 'November', 'December'],
        'feels': 'feels like', 'humidity': 'humidity', 'wind': 'wind',
        'pressure': 'pressure', 'updated': 'updated', 'today': 'Today',
        'upcoming': 'Upcoming', 'history': 'History', 'all_day': 'all day',
        'tomorrow': 'Tomorrow', 'last_days': 'last {} days', 'last_hours': 'last {} h',
        'conditions': {
            'clear-night': 'Clear', 'cloudy': 'Cloudy', 'exceptional': 'Exceptional',
            'fog': 'Fog', 'hail': 'Hail', 'lightning': 'Thunderstorm',
            'lightning-rainy': 'Thunderstorm, rain', 'partlycloudy': 'Partly cloudy',
            'pouring': 'Heavy rain', 'rainy': 'Rain', 'snowy': 'Snow',
            'snowy-rainy': 'Sleet', 'sunny': 'Sunny', 'windy': 'Windy',
            'windy-variant': 'Windy, cloudy'},
    },
    'bg': {
        'days': ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Нд'],
        'days_long': ['Понеделник', 'Вторник', 'Сряда', 'Четвъртък', 'Петък',
                      'Събота', 'Неделя'],
        'months': ['януари', 'февруари', 'март', 'април', 'май', 'юни', 'юли',
                   'август', 'септември', 'октомври', 'ноември', 'декември'],
        'feels': 'усеща се', 'humidity': 'влажност', 'wind': 'вятър',
        'pressure': 'налягане', 'updated': 'обновено', 'today': 'Днес',
        'upcoming': 'Предстоящо', 'history': 'История', 'all_day': 'цял ден',
        'tomorrow': 'Утре', 'last_days': 'последни {} дни', 'last_hours': 'последни {} ч',
        'conditions': {
            'clear-night': 'Ясно', 'cloudy': 'Облачно', 'exceptional': 'Особено',
            'fog': 'Мъгла', 'hail': 'Градушка', 'lightning': 'Гръмотевици',
            'lightning-rainy': 'Гръмотевици, дъжд', 'partlycloudy': 'Разкъсана облачност',
            'pouring': 'Силен дъжд', 'rainy': 'Дъжд', 'snowy': 'Сняг',
            'snowy-rainy': 'Сняг с дъжд', 'sunny': 'Слънчево', 'windy': 'Ветровито',
            'windy-variant': 'Ветровито, облачно'},
    },
}

_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        name = 'DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'
        for d in FONT_DIRS:
            p = os.path.join(d, name)
            if os.path.exists(p):
                _fonts[key] = ImageFont.truetype(p, size)
                break
        else:
            _fonts[key] = ImageFont.load_default()
    return _fonts[key]


def _num(v, nd=0):
    try:
        return f'{float(v):.{nd}f}'
    except (TypeError, ValueError):
        return '–'


def _fit(d, text, size, maxw, bold=False, minsize=12):
    """Largest font <= size that fits text into maxw."""
    while size > minsize and d.textlength(text, font=font(size, bold)) > maxw:
        size -= 2
    return font(size, bold)


def _status(d, w, status):
    """Small battery / Wi-Fi / power indicator, top right."""
    x = w - 12
    bat = status.get('battery_pct')
    if bat is not None:
        bw, bh, top = 34, 16, 13
        x -= bw + 4
        d.rectangle((x, top, x + bw, top + bh), outline=BLACK, width=2)
        d.rectangle((x + bw, top + 5, x + bw + 3, top + bh - 5), fill=BLACK)
        d.rectangle((x + 3, top + 3, x + 3 + (bw - 6) * min(bat, 100) // 100,
                     top + bh - 3), fill=DARK)
        txt = f'{bat}%'
        x -= d.textlength(txt, font=font(16)) + 6
        d.text((x, 12), txt, font=font(16), fill=BLACK)
    if status.get('external'):
        x -= 20
        d.polygon([(x + 9, 10), (x + 2, 22), (x + 8, 22), (x + 5, 32),
                   (x + 14, 18), (x + 8, 18), (x + 11, 10)], fill=BLACK)
    rssi = status.get('rssi')
    if rssi is not None:
        x -= 28
        bars = 4 if rssi > -55 else 3 if rssi > -65 else 2 if rssi > -75 else 1
        for i in range(4):
            h = 5 + i * 5
            d.rectangle((x + i * 6, 30 - h, x + i * 6 + 4, 30),
                        fill=BLACK if i < bars else LIGHT)


def period_label(t, hours):
    return t['last_days'].format(hours // 24) if hours >= 48 and hours % 24 == 0 \
        else t['last_hours'].format(hours)


def _events(d, box, events, now, t):
    """Upcoming calendar events, one per line: day/time + summary."""
    x0, y0, x1, y1 = box
    d.text((x0, y0), t['upcoming'], font=font(18, True), fill=DARK)
    y = y0 + 26
    for e in events:
        if y + 24 > y1:
            break
        st = e['start'] if e['all_day'] else e['start'].astimezone(now.tzinfo)
        day = st.date()
        if day == now.date():
            when = t['today']
        elif (day - now.date()).days == 1:
            when = t['tomorrow']
        else:
            when = f"{t['days'][day.weekday()]} {day.day}.{day.month:02d}"
        if not e['all_day']:
            when += f' {st:%H:%M}'
        wf = font(17, True)
        d.text((x0, y), when, font=wf, fill=BLACK)
        y += 21
        summ = e['summary']
        sf = _fit(d, summ, 19, x1 - x0, minsize=13)
        while d.textlength(summ, font=sf) > x1 - x0 and len(summ) > 4:
            summ = summ[:-2].rstrip() + '…'
        d.text((x0, y), summ, font=sf, fill=DARK)
        y += 27


def weather(size, w, now, lang='en', status=None, history=None, hours=168,
            events=None, clock=False, chart_now=None):
    """Weather dashboard from ha.HomeAssistant.weather() data.  history (as
    from ha.history) replaces the forecast with a chart; events (from
    ha.events) add an "upcoming" column."""
    t = TEXT.get(lang, TEXT['en'])
    W, H = size
    im = Image.new('L', size, WHITE)
    d = ImageDraw.Draw(im)
    portrait = H > W

    # header: date + status
    date = f"{t['days_long'][now.weekday()]}, {now.day} {t['months'][now.month - 1]}"
    if clock:                     # live (minute) updates: show the time
        date += f'  ·  {now:%H:%M}'
    d.text((16, 8), date, font=_fit(d, date, 26, W - 220, bold=True), fill=BLACK)
    _status(d, W, status or {})
    d.line((12, 46, W - 12, 46), fill=BLACK, width=2)

    # current conditions; in landscape the calendar takes the right column
    cal_x = W - 262 if events and not portrait else W
    cond = w.get('condition')
    isz = 190 if not portrait else 200
    if cal_x < W:
        isz = 150
    im.paste(icons.icon(cond, isz), (16, 58 + (190 - isz) // 2))
    temp = _num(w.get('temperature'), 0) + '°'
    x0 = 16 + isz + 16
    tf = _fit(d, temp, 120, cal_x - x0 - 16, True, minsize=60)
    d.text((x0, 50), temp, font=tf, fill=BLACK)
    ctext = t['conditions'].get(cond, cond or '')
    d.text((x0 + 4, 190), ctext, font=_fit(d, ctext, 30, cal_x - x0 - 20, True), fill=BLACK)
    details = []
    if w.get('apparent') is not None:
        details.append(f"{t['feels']} {_num(w['apparent'])}°")
    if w.get('humidity') is not None:
        details.append(f"{t['humidity']} {_num(w['humidity'])}%")
    if w.get('wind_speed') is not None:
        details.append(f"{t['wind']} {_num(w['wind_speed'])} {w.get('wind_unit', '')}")
    if w.get('pressure') is not None:
        details.append(f"{_num(w['pressure'])} {w.get('pressure_unit', '')}")
    y = 232
    line = '  ·  '.join(details[:2])
    d.text((x0 + 4, y), line, font=_fit(d, line, 20, cal_x - x0 - 20), fill=DARK)
    line = '  ·  '.join(details[2:])
    d.text((x0 + 4, y + 26), line, font=_fit(d, line, 20, cal_x - x0 - 20), fill=DARK)
    if cal_x < W:
        d.line((cal_x - 12, 58, cal_x - 12, 286), fill=LIGHT, width=2)
        _events(d, (cal_x, 54, W - 14, 290), events, now, t)

    # extra sensors (indoor temperature etc.)
    ytop = 300 if not portrait else 290
    sensors = w.get('sensors') or []
    if sensors:
        cols = 2 if not portrait else 1
        rows = (len(sensors) + cols - 1) // cols
        rows = min(rows, 3)
        cw = (W - 32) // cols
        for i, s in enumerate(sensors[:rows * cols]):
            cx = 16 + (i % cols) * cw
            cy = ytop + (i // cols) * 30
            val = f"{s['state']} {s['unit']}".strip()
            vf = font(22, True)
            vw = d.textlength(val, font=vf)
            d.text((cx + cw - vw - 12, cy), val, font=vf, fill=BLACK)
            d.text((cx, cy + 2), s['name'],
                   font=_fit(d, s['name'], 20, cw - vw - 28), fill=DARK)
        ytop += rows * 30 + 6
    if events and portrait:
        d.line((12, ytop, W - 12, ytop), fill=LIGHT, width=1)
        n = min(len(events), 3)
        _events(d, (16, ytop + 6, W - 16, ytop + 32 + n * 48), events[:n], now, t)
        ytop += 38 + n * 48

    # forecast, or the history chart in its place
    d.line((12, ytop, W - 12, ytop), fill=BLACK, width=2)
    daily = [f for f in (w.get('daily') or []) if f.get('datetime')]
    footer_h = 26
    groups = chart.group_by_unit(list((history or {}).values()), 1)
    if groups:
        chart.panel(im, (12, ytop + 8, W - 12, H - footer_h), groups[0],
                    chart_now or now, hours, font, t['days'], period_label(t, hours))
    elif portrait:
        _forecast_rows(im, d, daily[:6], (12, ytop + 6, W - 12, H - footer_h), t, now)
    else:
        _forecast_cols(im, d, daily[:5], (12, ytop + 6, W - 12, H - footer_h), t, now)

    upd = f"{t['updated']} {now:%H:%M}"
    if status and status.get('ip'):
        upd += f"  ·  {status['ip']}"
    d.text((W - 12, H - 6), upd, font=font(15), fill=MID, anchor='rd')
    if w.get('name'):
        d.text((14, H - 6), w['name'], font=font(15), fill=MID, anchor='ld')
    return im


def _fdate(f):
    try:
        return datetime.datetime.fromisoformat(f['datetime'].replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None


def _forecast_cols(im, d, days, box, t, now):
    x0, y0, x1, y1 = box
    if not days:
        return
    cw = (x1 - x0) / len(days)
    isz = min(int(cw * 0.6), int((y1 - y0) - 110), 96)
    for i, f in enumerate(days):
        cx = x0 + cw * i + cw / 2
        dt = _fdate(f)
        name = t['days'][dt.astimezone(now.tzinfo).weekday()] if dt else '?'
        if dt and dt.astimezone(now.tzinfo).date() == now.date():
            name = t['today']
        d.text((cx, y0 + 4), name, font=font(24, True), fill=BLACK, anchor='mt')
        im.paste(icons.icon(f.get('condition'), isz), (int(cx - isz / 2), y0 + 34))
        yy = y0 + 38 + isz
        hi, lo = _num(f.get('temperature')), _num(f.get('templow'))
        hf, lf = font(26, True), font(22)
        hw, lw = d.textlength(hi + '°', font=hf), d.textlength(lo + '°', font=lf)
        gap = 10
        sx = cx - (hw + gap + lw) / 2
        d.text((sx, yy), hi + '°', font=hf, fill=BLACK)
        d.text((sx + hw + gap, yy + 3), lo + '°', font=lf, fill=MID)
        p = f.get('precipitation_probability')
        if p is None and f.get('precipitation'):
            ptxt = f"{_num(f['precipitation'], 1)} mm"
        else:
            ptxt = f'{_num(p)}%' if p is not None else ''
        if ptxt:
            d.text((cx, yy + 34), ptxt, font=font(17), fill=DARK, anchor='mt')
        if i:
            d.line((x0 + cw * i, y0 + 8, x0 + cw * i, y1 - 8), fill=LIGHT, width=1)


def _forecast_rows(im, d, days, box, t, now):
    x0, y0, x1, y1 = box
    if not days:
        return
    rh = (y1 - y0) / len(days)
    isz = int(min(rh - 8, 70))
    for i, f in enumerate(days):
        cy = y0 + rh * i + rh / 2
        dt = _fdate(f)
        name = t['days'][dt.astimezone(now.tzinfo).weekday()] if dt else '?'
        d.text((x0 + 8, cy), name, font=font(26, True), fill=BLACK, anchor='lm')
        im.paste(icons.icon(f.get('condition'), isz), (int(x0 + 90), int(cy - isz / 2)))
        txt = f"{_num(f.get('temperature'))}° / {_num(f.get('templow'))}°"
        d.text((x1 - 8, cy), txt, font=font(26, True), fill=BLACK, anchor='rm')


def history_screen(size, history, now, hours, lang='en', status=None):
    t = TEXT.get(lang, TEXT['en'])
    title = f"{t['history']} · {period_label(t, hours)}"
    return chart.screen(size, history, now, hours, font, t['days'], title,
                        lambda d, W: _status(d, W, status or {}))


# e-ink greys look darker than on a monitor: lift the mid-tones a little
_EINK_TONE = [round(255 * (v / 255) ** 0.8) for v in range(256)]


def photo(src, size, fit='contain', enhance=True):
    """Photo (path or file object) scaled to the canvas, grey and
    contrast-stretched; enhance adds the e-ink tone curve and sharpening
    (the 16-level dither to the panel is done later)."""
    im = Image.open(src)
    im.draft('RGB', (size[0] * 2, size[1] * 2))   # fast JPEG DCT downscale
    im = ImageOps.exif_transpose(im).convert('L')
    if fit == 'cover':
        im = ImageOps.fit(im, size, Image.LANCZOS)
    else:
        im = ImageOps.contain(im, size, Image.LANCZOS)
    im = ImageOps.autocontrast(im, cutoff=1)
    if enhance:
        im = im.filter(ImageFilter.UnsharpMask(radius=1.5, percent=70, threshold=2))
        im = im.point(_EINK_TONE)
    if im.size != size:
        bg = Image.new('L', size, WHITE)
        bg.paste(im, ((size[0] - im.width) // 2, (size[1] - im.height) // 2))
        im = bg
    return im


def message(size, title, lines=(), footer=''):
    """Text screen: big title, a few lines."""
    W, H = size
    im = Image.new('L', size, WHITE)
    d = ImageDraw.Draw(im)
    d.rectangle((8, 8, W - 9, H - 9), outline=BLACK, width=3)
    tf = _fit(d, title, 56, W - 60, bold=True)
    y = 60
    d.text((W / 2, y), title, font=tf, fill=BLACK, anchor='mt')
    y += tf.size + 40
    for ln in lines:
        lf = _fit(d, ln, 30, W - 60)
        d.text((W / 2, y), ln, font=lf, fill=BLACK, anchor='mt')
        y += lf.size + 16
    if footer:
        d.text((W / 2, H - 24), footer, font=font(18), fill=MID, anchor='mb')
    return im


def wrap(text, size, fontsize=40, bold=True):
    """Free text (e.g. from HA) word-wrapped and centred."""
    W, H = size
    im = Image.new('L', size, WHITE)
    d = ImageDraw.Draw(im)
    while fontsize > 14:
        f = font(fontsize, bold)
        lines = []
        for para in str(text).split('\n'):
            cur = ''
            for word in para.split():
                test = (cur + ' ' + word).strip()
                if d.textlength(test, font=f) <= W - 60:
                    cur = test
                else:
                    lines.append(cur)
                    cur = word
            lines.append(cur)
        lh = int(fontsize * 1.25)
        if lh * len(lines) <= H - 60:
            break
        fontsize -= 4
    y = (H - lh * len(lines)) / 2
    for ln in lines:
        d.text((W / 2, y), ln, font=f, fill=BLACK, anchor='mt')
        y += lh
    return im
