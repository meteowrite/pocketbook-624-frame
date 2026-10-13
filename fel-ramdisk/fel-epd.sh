#!/bin/sh
# Boot the PB624 over FEL with a kernel, DTB and the small test initramfs.
#   KERNEL (default: the Buildroot zImage), DTB (default: from build-dtb.sh)
cd "$(dirname "$0")"
KERNEL=${KERNEL:-../output/images/zImage}
DTB=${DTB:-sun5i-a13-pocketbook-624.dtb}
sunxi-fel -p uboot ../output/images/u-boot-sunxi-with-spl.bin \
	write 0x42000000 "$KERNEL" \
	write 0x43000000 "$DTB" \
	write 0x43300000 uRamdisk-epd \
	write 0x43100000 boot-epd.scr
