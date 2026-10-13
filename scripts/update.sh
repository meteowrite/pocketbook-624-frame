#!/bin/sh
# Update a running PB624 over the network: U-Boot + rootfs partition (p1),
# /data untouched.   usage: update.sh [host]   (default pb624.local)
# Uses br2-external/board/pb624/local/pb624_dev_key if present (ssh as root).
#
# The read-only rootfs is rewritten in place.  Everything that needs to exec
# a program happens before that; afterwards only shell builtins are safe, so
# the reboot is done by arming the watchdog (fd closed without the magic 'V').
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
HOST=${1:-pb624.local}
I=$HERE/output/images
KEY=$HERE/br2-external/board/pb624/local/pb624_dev_key
SSH="ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
[ -f "$KEY" ] && SSH="$SSH -i $KEY -o IdentitiesOnly=yes"

echo "compressing rootfs"
GZ_MD5=$(gzip -1 -c "$I/rootfs.ext2" | tee "$I/rootfs.ext2.gz" | md5sum | cut -d' ' -f1)
UB_MD5=$(md5sum "$I/u-boot-sunxi-with-spl.bin" | cut -d' ' -f1)
echo "uploading to $HOST"
$SSH root@$HOST 'rm -rf /data/update; mkdir -p /data/update'
$SSH root@$HOST 'cat > /data/update/rootfs.ext2.gz' < "$I/rootfs.ext2.gz"
$SSH root@$HOST 'cat > /data/update/u-boot.bin' < "$I/u-boot-sunxi-with-spl.bin"

$SSH root@$HOST "GZ_MD5=$GZ_MD5 UB_MD5=$UB_MD5 sh -s" <<'REMOTE'
set -e
cd /data/update
[ "$(md5sum rootfs.ext2.gz | cut -d' ' -f1)" = "$GZ_MD5" ] || { echo "upload corrupt"; exit 1; }
[ "$(md5sum u-boot.bin | cut -d' ' -f1)" = "$UB_MD5" ] || { echo "upload corrupt"; exit 1; }
DEV=
for d in /dev/mmcblk0 /dev/mmcblk1; do
	blkid ${d}p2 2>/dev/null | grep -q 'LABEL="data"' && { DEV=$d; break; }
done
[ -n "$DEV" ] || { echo "no PB624 card found"; exit 1; }
echo "target $DEV, uploads verified"
/etc/init.d/S80frame stop || true          # also disarms the watchdog
dd if=u-boot.bin of=$DEV bs=1k seek=8 conv=notrunc,fsync 2>/dev/null
zcat rootfs.ext2.gz | dd of=${DEV}p1 bs=1M conv=fsync 2>/dev/null
echo "written; rebooting via watchdog in ~16 s"
exec 3>/dev/watchdog
REMOTE
echo "done; the board reboots into the new rootfs"
