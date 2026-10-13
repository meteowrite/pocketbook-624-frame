#!/bin/sh
# Development boot over FEL: this build's U-Boot, kernel and DTB, with the
# rootfs from the SD card (mmcblk0p1).  Faster than reflashing for kernel work.
I=$(cd "$(dirname "$0")/.." && pwd)/output/images
sunxi-fel -p uboot "$I/u-boot-sunxi-with-spl.bin" \
	write 0x42000000 "$I/zImage" \
	write 0x43000000 "$I/sun5i-a13-pocketbook-624.dtb" \
	write 0x43100000 "$I/fel-boot.scr"
