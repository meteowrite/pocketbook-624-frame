#!/bin/sh
# PB624 rootfs tweaks (TARGET_DIR is $1)
set -e
T="$1"
# persistent ssh host keys and authorized_keys on /data
ln -snf /data/dropbear "$T/etc/dropbear"
mkdir -p "$T/root"
ln -snf /data/ssh "$T/root/.ssh"
# root password on /data (change it with passwd, kept across updates):
# S00data creates /data/config/shadow from shadow.default, post-fakeroot.sh
# turns etc/shadow into a link (after the permission tables need the file)
# (busybox passwd also rewrites /etc/passwd, so that one moves too)
cp -p "$T/etc/shadow" "$T/etc/shadow.default"
cp -p "$T/etc/passwd" "$T/etc/passwd.default"
# mount point for the data partition (rootfs is read-only)
mkdir -p "$T/data"
# login on the USB serial gadget too (g_serial, /dev/ttyGS0)
grep -q '^ttyGS0:' "$T/etc/inittab" ||
	echo 'ttyGS0::respawn:/sbin/getty -L ttyGS0 0 vt100 # USB serial' >> "$T/etc/inittab"
# version of this build, shown by pb624-ota and the web UI
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
V=$(git -C "$REPO" describe --always --dirty 2>/dev/null || echo unknown)
echo "$(date -u +%Y%m%d-%H%M) $V" > "$T/etc/pb624-version"
# renamed init scripts leave the old copy behind in an incremental build
rm -f "$T/etc/init.d/S05data"
# Files from the stock firmware that this repo does not redistribute; put
# your own copies in board/pb624/local/ (see local/README).
L="$(cd "$(dirname "$0")" && pwd)/local"
mkdir -p "$T/usr/share/pb624" "$T/boot"
if [ ! -f "$L/default.wbf" ]; then
	echo "post-build: missing $L/default.wbf (e-ink waveform, see local/README)" >&2
	exit 1
fi
cp "$L/default.wbf" "$T/usr/share/pb624/default.wbf"
ln -snf /usr/share/pb624/default.wbf "$T/boot/default.wbf"
# optional low-battery picture (800x600 PGM); the app draws one otherwise
rm -f "$T/usr/share/pb624/low_pwr.pgm"
if [ -f "$L/low_pwr.pgm" ]; then
	cp "$L/low_pwr.pgm" "$T/usr/share/pb624/low_pwr.pgm"
fi
