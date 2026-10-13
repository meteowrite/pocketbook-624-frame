#!/usr/bin/env python3
"""Render a text/shapes test image as an 800x600 8-bit PGM for epdshow."""
import math
import sys

from PIL import Image, ImageDraw, ImageFont

FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans{}.ttf'


def font(size, bold=False):
    return ImageFont.truetype(FONT.format('-Bold' if bold else ''), size)


def main(out):
    im = Image.new('L', (800, 600), 255)
    d = ImageDraw.Draw(im)

    d.rectangle((10, 10, 789, 589), outline=0, width=4)
    d.text((400, 70), 'Hello, PocketBook 624!', font=font(52, True),
           fill=0, anchor='mm')
    d.text((400, 135), 'Mainline Linux 6.1.9  -  A13 TCON0 + sun4i DRM',
           font=font(26), fill=0, anchor='mm')
    d.text((400, 170), 'ED060SCE  800x600  GC16 waveform from default.wbf',
           font=font(22), fill=64, anchor='mm')

    # 16 grey swatches
    for i in range(16):
        x = 40 + i * 45
        d.rectangle((x, 220, x + 40, 300), fill=i * 17, outline=0)
        d.text((x + 20, 315), str(i), font=font(16), fill=0, anchor='mm')

    # shapes
    d.ellipse((60, 360, 260, 560), outline=0, width=6)
    d.ellipse((110, 410, 210, 510), fill=0)
    d.polygon([(400, 360), (300, 560), (500, 560)], fill=128, outline=0)
    for i in range(0, 200, 10):
        d.line((560 + i, 360, 760, 360 + i), fill=0, width=1)
        d.line((560, 360 + i, 560 + i, 560), fill=0, width=1)

    im.save(out, format='PPM')  # 'L' mode saves as binary PGM (P5)


def heart(out, text):
    """Big centred heart with a line of text below it (4x supersampled)."""
    k = 4
    im = Image.new('L', (800 * k, 600 * k), 255)
    d = ImageDraw.Draw(im)
    cx, cy, s = 400 * k, 245 * k, 13 * k
    pts = []
    for i in range(720):
        t = math.radians(i / 2)
        x = 16 * math.sin(t) ** 3
        y = (13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t)
             - math.cos(4 * t))
        pts.append((cx + s * x, cy - s * y))
    d.polygon(pts, fill=0)
    d.text((400 * k, 520 * k), text, font=font(64 * k, True), fill=0,
           anchor='mm')
    im.resize((800, 600), Image.LANCZOS).save(out, format='PPM')

if len(sys.argv) > 2:
    heart(sys.argv[1], sys.argv[2])
else:
    main(sys.argv[1] if len(sys.argv) > 1 else 'hello.pgm')
