#!/usr/bin/env python3
"""Convert an uncompressed 8/24/32-bit BMP to an 800x600 8-bit PGM (P5)."""
import struct
import sys


def main(src, dst):
    d = open(src, 'rb').read()
    off, = struct.unpack_from('<I', d, 10)
    hsz, w, h, _, bpp, comp = struct.unpack_from('<IiiHHI', d, 14)
    if comp not in (0, 3):
        sys.exit('compressed BMP not supported')
    flip = h > 0
    h = abs(h)
    pal = []
    if bpp == 8:
        po = 14 + hsz
        pal = [d[po + 4 * i:po + 4 * i + 3] for i in range(256)]
    stride = (w * bpp // 8 + 3) & ~3
    out = bytearray(800 * 600)
    for y in range(min(h, 600)):
        row = off + (h - 1 - y if flip else y) * stride
        for x in range(min(w, 800)):
            if bpp == 8:
                b, g, r = pal[d[row + x]]
            else:
                b, g, r = d[row + x * bpp // 8:row + x * bpp // 8 + 3]
            out[y * 800 + x] = (r * 77 + g * 151 + b * 28) >> 8
    open(dst, 'wb').write(b'P5 800 600 255\n' + out)


main(sys.argv[1], sys.argv[2])
