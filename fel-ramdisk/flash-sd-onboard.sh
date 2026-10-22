#!/bin/sh
# Runs on the board from the FEL flashing ramdisk.
#   sh /flash/flash.sh [/dev/mmcblkN]  (default: the PB624 card, else mmcblk0)
# Update mode (card already has the PB624 layout, ext4 "data" in p2): write
# only U-Boot and the rootfs (p1 = system slot A), keep /data (config,
# photos, backups).  This also clears the A/B boot state, so slot A boots.
# Full mode: back up the old p1 to RAM, write the whole image, add slot B
# (p3, last 384 MiB), grow the data partition up to it, store the backup in
# /data/backup.
# default: the card that already has a PB624 data partition, else mmcblk0
DEV=$1
if [ -z "$DEV" ]; then
	for d in /dev/mmcblk0 /dev/mmcblk1; do
		blkid ${d}p2 2>/dev/null | grep -q 'LABEL="data"' && { DEV=$d; break; }
	done
	DEV=${DEV:-/dev/mmcblk0}
fi
echo "target: $DEV"
P2_START=@P2_START@
P1_MD5=@P1_MD5@
IMG=/flash/sdcard.img.gz
set -e
verify() {
	sum=$(dd if=${DEV}p1 bs=1M count=$(((P2_START - 2048) / 2048)) 2>/dev/null | md5sum | cut -d' ' -f1)
	[ "$sum" = "$P1_MD5" ] && echo "rootfs verified" || { echo "ROOTFS CHECKSUM MISMATCH"; exit 2; }
}
[ -b $DEV ] || { echo "no $DEV"; exit 1; }
mkdir -p /mnt /new /data2

if blkid ${DEV}p2 2>/dev/null | grep -q 'LABEL="data"'; then
	echo "update mode: U-Boot + rootfs, keeping ${DEV}p2"
	# byte ranges via tail/head: dd skip/count miscount short pipe reads
	zcat $IMG | tail -c +8193 | head -c $((1016 * 1024)) |
		dd of=$DEV bs=1k seek=8 conv=notrunc 2>/dev/null
	zcat $IMG | tail -c +1048577 | head -c $(((P2_START - 2048) * 512)) |
		dd of=$DEV bs=1M seek=1 conv=notrunc 2>/dev/null
	# disk signature for root=PARTUUID=50623624-01, partition table kept
	printf '\044\066\142\120' | dd of=$DEV bs=1 seek=440 conv=notrunc 2>/dev/null
	sync
	blockdev --rereadpt $DEV 2>/dev/null || partprobe $DEV 2>/dev/null || true
	verify
	echo "FLASH DONE"
	exit 0
fi

BK=
if mount -o ro ${DEV}p1 /mnt 2>/dev/null; then
	echo "backing up old ${DEV}p1 ($(du -sh /mnt | cut -f1))"
	tar -C /mnt -cf - . | gzip > /tmp/old-rootfs.tar.gz && BK=/tmp/old-rootfs.tar.gz
	umount /mnt
	ls -la /tmp/old-rootfs.tar.gz
fi
echo "full mode: writing image"
zcat $IMG | dd of=$DEV bs=1M 2>&1 | tail -1
sync
echo "adding slot B, growing data partition"
SECT=$(cat /sys/block/$(basename $DEV)/size)
B=$(( (SECT - 786432) / 2048 * 2048 ))
printf 'd\n2\nn\np\n2\n%s\n%s\nn\np\n3\n%s\n\nw\n' $P2_START $((B - 1)) $B |
	fdisk -u $DEV >/dev/null 2>&1 || true
dd if=/dev/zero of=$DEV bs=1M seek=$((B / 2048)) count=1 conv=notrunc 2>/dev/null
partprobe $DEV 2>/dev/null || true
sleep 1
grep "$(basename $DEV)" /proc/partitions
mount -o ro ${DEV}p1 /new
mount --bind /dev /new/dev
mount -t proc proc /new/proc
chroot /new /sbin/e2fsck -fy ${DEV}p2 >/dev/null 2>&1 || true
chroot /new /sbin/resize2fs ${DEV}p2 2>&1 | tail -1
umount /new/proc /new/dev /new
if [ -n "$BK" ]; then
	mount ${DEV}p2 /data2
	mkdir -p /data2/backup
	cp $BK /data2/backup/
	ls -la /data2/backup
	umount /data2
fi
sync
verify
echo "FLASH DONE"
