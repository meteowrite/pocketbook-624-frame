#!/bin/sh
/bin/busybox mount -t devtmpfs devtmpfs /dev
/bin/busybox mount -t proc proc /proc
/bin/busybox mount -t sysfs sysfs /sys
/bin/busybox mount -t tmpfs tmpfs /tmp
/bin/busybox --install -s
echo 1 > /proc/sys/kernel/printk
echo "PB624 e-ink test initramfs. Try: epdshow -i ; epdshow -m 0 white ; epdshow /epd/hello.pgm"
export PATH=/bin:/sbin:/usr/bin:/usr/sbin:/epd
while true; do sh -l; done
