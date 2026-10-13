# FEL development boot: kernel + DTB loaded by sunxi-fel, rootfs on the SD
setenv bootargs console=ttyS0,115200 root=PARTUUID=50623624-01 rootwait ro panic=10 drm_kms_helper.fbdev_emulation=0
bootz 0x42000000 - 0x43000000
