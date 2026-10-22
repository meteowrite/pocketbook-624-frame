#!/bin/sh
# Update a running PB624 over the network (ssh as root, key
# br2-external/board/pb624/local/pb624_dev_key if present).
#   update.sh [-u] [host]      (default pb624.local)
# The rootfs goes to the system slot that is not running (pb624-ota
# install): it is read back and checked, then booted as a trial.  If it is
# not back online within ~5 minutes the frame returns to the old slot by
# itself.  The script waits for the reboot and reports which system runs.
#   -u  also rewrite U-Boot if it differs.  U-Boot has a single copy: a
#       power loss while it is written (a second) needs FEL to recover.
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
UBOOT=
[ "$1" = -u ] && { UBOOT=1; shift; }
HOST=${1:-pb624.local}
I=$HERE/output/images
KEY=$HERE/br2-external/board/pb624/local/pb624_dev_key
SSH="ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
[ -f "$KEY" ] && SSH="$SSH -i $KEY -o IdentitiesOnly=yes"
GZ=$I/pb624-rootfs.ext2.gz

if ! $SSH root@$HOST 'command -v pb624-ota' >/dev/null; then
	echo "$HOST has no pb624-ota (pre-A/B system): see README, 'Old cards'"
	exit 1
fi
GZ_MD5=$(md5sum "$GZ" | cut -d' ' -f1)
UB_MD5=$(md5sum "$I/u-boot-sunxi-with-spl.bin" | cut -d' ' -f1)
echo "uploading to $HOST"
$SSH root@$HOST 'rm -rf /data/update; mkdir -p /data/update'
$SSH root@$HOST 'cat > /data/update/rootfs.ext2.gz' < "$GZ"
[ -n "$UBOOT" ] && $SSH root@$HOST 'cat > /data/update/u-boot.bin' < "$I/u-boot-sunxi-with-spl.bin"

BOOT_ID=$($SSH root@$HOST 'cat /proc/sys/kernel/random/boot_id')
$SSH root@$HOST "GZ_MD5=$GZ_MD5 UB_MD5=$UB_MD5 UBOOT=$UBOOT sh -s" <<'REMOTE'
set -e
cd /data/update
[ "$(md5sum rootfs.ext2.gz | cut -d' ' -f1)" = "$GZ_MD5" ] || { echo "upload corrupt"; exit 1; }
if [ -n "$UBOOT" ]; then
	[ "$(md5sum u-boot.bin | cut -d' ' -f1)" = "$UB_MD5" ] || { echo "upload corrupt"; exit 1; }
	pb624-ota bootloader u-boot.bin
fi
pb624-ota install rootfs.ext2.gz --no-reboot
rm -rf /data/update
reboot
REMOTE

echo "waiting for $HOST to come back"
t=0
while [ $t -lt 900 ]; do
	sleep 10; t=$((t + 10))
	id=$($SSH -o ConnectTimeout=5 root@$HOST 'cat /proc/sys/kernel/random/boot_id' 2>/dev/null) || continue
	[ "$id" != "$BOOT_ID" ] || continue
	# up again: wait until the app confirmed the update (or it fell back)
	out=$($SSH root@$HOST 'pb624-ota status' 2>/dev/null) || continue
	echo "$out" | grep -q '"trial": true' && continue
	echo "$out" | grep -E '"(slot|version|result)"'
	exit 0
done
echo "$HOST did not come back within 15 min"
exit 1
