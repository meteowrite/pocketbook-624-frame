#!/usr/bin/env python3
"""Decoder for E Ink .wbf waveform files (4-bit, 16x16 transitions)."""
import struct, sys


def u24(d, o):
    return d[o] | d[o + 1] << 8 | d[o + 2] << 16


def parse(path):
    d = open(path, 'rb').read()
    modes = d[0x25] + 1
    temps = d[0x26] + 1
    trange = list(d[0x30:0x30 + temps + 1])
    wmta = u24(d, 0x20)
    out = []  # out[mode][temp] = list of frames, frame[from][to] = 2-bit code
    for m in range(modes):
        tt = u24(d, wmta + 4 * m)
        row = []
        for t in range(temps):
            wa = u24(d, tt + 4 * t)
            row.append(decode(d, wa))
        out.append(row)
    return dict(modes=modes, temps=temps, trange=trange, wf=out, rate=d[0x17])


def decode(d, i):
    frames = []
    cur = [[0] * 16 for _ in range(16)]
    x = y = 0
    rle = True
    while True:
        b = d[i]
        if b == 0xff:
            break
        if b == 0xfc:
            rle = not rle
            i += 1
            continue
        if rle:
            n = d[i + 1] + 1
            i += 2
        else:
            n = 1
            i += 1
        for _ in range(n):
            for k in range(4):
                cur[y][x] = (b >> (2 * k)) & 3
                x += 1
            if x >= 16:
                x = 0
                y += 1
                if y >= 16:
                    y = 0
                    frames.append(cur)
                    cur = [[0] * 16 for _ in range(16)]
    return frames


if __name__ == '__main__':
    w = parse(sys.argv[1])
    print('modes', w['modes'], 'temps', w['temps'], 'ranges', w['trange'])
    for m in range(w['modes']):
        print('mode', m, 'frames per temp:', [len(f) for f in w['wf'][m]])
