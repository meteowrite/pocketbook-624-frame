#!/bin/sh
# Boot the mainline test kernel + upstream DTB over FEL (board in FEL mode).
# Uses our U-Boot; rootfs is the existing internal card (read-only).
cd "$(dirname "$0")/out"
I=../../output/images
sunxi-fel -p uboot "$I/u-boot-sunxi-with-spl.bin" \
	write 0x42000000 zImage \
	write 0x43000000 sun5i-a13-pocketbook-624.dtb \
	write 0x43100000 fel-boot-mainline.scr
