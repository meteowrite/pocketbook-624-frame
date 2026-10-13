#!/bin/sh
# Build data.ext4 (data-skel + local/ secrets), sdcard.img, the FEL boot
# script and pb624-rootfs.ext2.gz (web UI system update).
set -e
BOARD_DIR="$(cd "$(dirname "$0")" && pwd)"
D="$BUILD_DIR/pb624-data"
rm -rf "$D"
mkdir -p "$D"
# optional extra files for /data (git does not keep empty directories)
[ -d "$BOARD_DIR/data-skel" ] && cp -a "$BOARD_DIR/data-skel/." "$D/"
mkdir -p "$D/config" "$D/photos" "$D/dropbear" "$D/ssh" "$D/state"
for f in wpa_supplicant.conf frame.json; do
	[ -f "$BOARD_DIR/local/$f" ] && cp "$BOARD_DIR/local/$f" "$D/config/"
done
if [ -f "$BOARD_DIR/local/authorized_keys" ]; then
	cp "$BOARD_DIR/local/authorized_keys" "$D/ssh/"
elif [ -f "$BOARD_DIR/local/pb624_dev_key.pub" ]; then   # used by update.sh
	cp "$BOARD_DIR/local/pb624_dev_key.pub" "$D/ssh/authorized_keys"
fi
chmod 700 "$D/dropbear" "$D/ssh"
chmod 600 "$D/config/"* 2>/dev/null || true
rm -f "$BINARIES_DIR/data.ext4"
"$HOST_DIR/sbin/mke2fs" -q -t ext4 -L data -E root_owner=0:0 -d "$D" \
	"$BINARIES_DIR/data.ext4" 64M

"$HOST_DIR/bin/mkimage" -A arm -O linux -T script -C none \
	-d "$BOARD_DIR/fel-boot.cmd" "$BINARIES_DIR/fel-boot.scr" >/dev/null
support/scripts/genimage.sh -c "$BOARD_DIR/genimage.cfg"
# for "System update" in the web UI (or POST /api/update)
gzip -9 -c "$BINARIES_DIR/rootfs.ext2" > "$BINARIES_DIR/pb624-rootfs.ext2.gz"
