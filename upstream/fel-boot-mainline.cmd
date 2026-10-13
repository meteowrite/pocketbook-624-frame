# FEL boot of the mainline test kernel, rootfs from the internal card
setenv bootargs console=ttyS0,115200 root=PARTUUID=50623624-01 rootwait ro panic=10
bootz 0x42000000 - 0x43000000
