#!/bin/sh
# Build a small uRamdisk: busybox + needed libs from the Buildroot target,
# epdshow, the stock waveform, test images, and the Wi-Fi bits (r8188eu,
# firmware, wpa_supplicant).
# FLASH_IMG=sdcard.img.gz FLASH_P2_START=<sector> adds the on-board SD flasher.
set -e
cd "$(dirname "$0")"
BR=$(cd .. && pwd)/output
T=$BR/target
K=$BR/build/linux-6.1.9
KREL=$(cat $K/include/config/kernel.release)
GEN=$K/usr/gen_init_cpio
WBF=../br2-external/board/pb624/local/default.wbf
READELF=$BR/host/bin/arm-linux-readelf
$BR/host/bin/arm-linux-strip -o .epdshow.stripped $T/usr/bin/epdshow

BINS="bin/busybox usr/sbin/i2cdetect usr/sbin/i2cget usr/sbin/i2cset
      usr/sbin/i2cdump usr/sbin/wpa_supplicant usr/sbin/wpa_cli"

# shared libs needed by $BINS, recursively (paths relative to $T)
libs() {
	todo="$BINS" seen="lib/ld-linux-armhf.so.3"
	while [ -n "$todo" ]; do
		set -- $todo; f=$1; shift; todo="$*"
		for n in $($READELF -d "$T/$f" 2>/dev/null |
			   sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p'); do
			for d in lib usr/lib; do
				[ -e "$T/$d/$n" ] || continue
				case " $seen " in *" $d/$n "*) ;; *)
					seen="$seen $d/$n"; todo="$todo $d/$n";;
				esac
				break
			done
		done
	done
	echo $seen
}

if [ -n "$FLASH_IMG" ]; then
	P1_MD5=$(md5sum "$(dirname "$FLASH_IMG")/rootfs.ext2" | cut -d' ' -f1)
	sed "s/@P2_START@/$FLASH_P2_START/; s/@P1_MD5@/$P1_MD5/" flash-sd-onboard.sh > .flash.sh
fi
{
	for d in /bin /sbin /usr /usr/bin /usr/sbin /usr/lib /usr/share \
		 /usr/share/udhcpc /lib /lib/firmware /lib/firmware/rtlwifi \
		 /lib/modules /lib/modules/$KREL /dev /proc /sys /tmp /boot \
		 /epd /root /etc /var /var/run /run; do
		echo "dir $d 0755 0 0"
	done
	echo "nod /dev/console 0600 0 0 c 5 1"
	echo "file /init $PWD/init.sh 0755 0 0"
	echo "slink /bin/sh busybox 0777 0 0"
	for f in $BINS $(libs); do
		echo "file /$f $(readlink -f $T/$f) 0755 0 0"
	done
	echo "file /usr/share/udhcpc/default.script $T/usr/share/udhcpc/default.script 0755 0 0"
	echo "file /lib/modules/$KREL/r8188eu.ko $K/drivers/staging/r8188eu/r8188eu.ko 0644 0 0"
	echo "file /lib/firmware/rtlwifi/rtl8188eufw.bin $T/lib/firmware/rtlwifi/rtl8188eufw.bin 0644 0 0"
	echo "file /epd/epdshow $PWD/.epdshow.stripped 0755 0 0"
	echo "file /boot/default.wbf $WBF 0644 0 0"
	if [ -n "$FLASH_IMG" ]; then   # flashing ramdisk: image + on-board script
		echo "dir /flash 0755 0 0"
		echo "file /flash/sdcard.img.gz $FLASH_IMG 0644 0 0"
		echo "file /flash/flash.sh $PWD/.flash.sh 0755 0 0"
	fi
	for p in *.pgm; do
		echo "file /epd/$p $PWD/$p 0644 0 0"
	done
} > .initrd.list
$GEN .initrd.list | gzip -9 > initrd-epd.cpio.gz
mkimage -A arm -T ramdisk -C gzip -d initrd-epd.cpio.gz uRamdisk-epd >/dev/null
rm -f .epdshow.stripped .initrd.list .flash.sh
ls -la uRamdisk-epd
