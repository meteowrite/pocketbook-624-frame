#!/bin/sh
# Write the PB624 image to a microSD card in a reader (run with sudo).
#   flash-sd.sh [-b BACKUP.tar.gz] [-y] /dev/sdX
#     -b  first tar up the card's current first partition (if it is ext4)
#     -y  don't ask for confirmation
# Writes sdcard.img (U-Boot, rootfs, data), then grows the data partition
# to the end of the card.
set -e
IMG=$(cd "$(dirname "$0")/.." && pwd)/output/images/sdcard.img
BACKUP= YES=
while getopts b:y o; do
	case $o in
	b) BACKUP=$OPTARG ;;
	y) YES=1 ;;
	*) exit 1 ;;
	esac
done
shift $((OPTIND - 1))
DEV=$1
[ -b "$DEV" ] || { echo "usage: $0 [-b backup.tar.gz] [-y] /dev/sdX"; exit 1; }
[ -f "$IMG" ] || { echo "no $IMG, build first"; exit 1; }
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

name=$(basename "$DEV")
if [ "$(cat /sys/block/$name/removable 2>/dev/null)" != 1 ] &&
   ! readlink -f /sys/block/$name | grep -q usb; then
	echo "$DEV is not a removable/USB disk, refusing"; exit 1
fi
size=$(($(cat /sys/block/$name/size) / 2048))
[ $size -le 131072 ] || { echo "$DEV is ${size} MiB (>128 GiB), refusing"; exit 1; }
if grep -q "^$DEV" /proc/mounts; then
	echo "$DEV has mounted partitions:"; grep "^$DEV" /proc/mounts; exit 1
fi
lsblk -o NAME,SIZE,FSTYPE,LABEL,MODEL "$DEV"

if [ -n "$BACKUP" ]; then
	p1=${DEV}1
	[ -b "$p1" ] || p1=${DEV}p1
	if [ "$(blkid -o value -s TYPE "$p1" 2>/dev/null)" = ext4 ]; then
		m=$(mktemp -d)
		mount -o ro "$p1" "$m"
		echo "backing up $p1 to $BACKUP"
		tar -C "$m" -czf "$BACKUP" .
		umount "$m"; rmdir "$m"
		ls -la "$BACKUP"
	else
		echo "no ext4 on $p1, nothing to back up"
	fi
fi

if [ -z "$YES" ]; then
	printf "ERASE %s (%s MiB) and write %s? type YES: " "$DEV" "$size" "$IMG"
	read a; [ "$a" = YES ] || exit 1
fi
dd if="$IMG" of="$DEV" bs=4M conv=fsync status=progress
sync
# grow partition 2 (data) to the end of the card
echo ", +" | sfdisk -q -N 2 --no-reread "$DEV"
partprobe "$DEV" 2>/dev/null || blockdev --rereadpt "$DEV"
sleep 1
p2=${DEV}2
[ -b "$p2" ] || p2=${DEV}p2
e2fsck -fy "$p2" >/dev/null || true
resize2fs "$p2"
sync
lsblk -o NAME,SIZE,FSTYPE,LABEL "$DEV"
echo "done"
