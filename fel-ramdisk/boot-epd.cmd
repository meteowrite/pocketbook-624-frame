# PB624 e-ink bring-up: small initramfs, no fbdev/fbcon on the e-ink pipeline
setenv bootargs console=ttyS0,115200 rdinit=/init panic=10 mem=256M drm_kms_helper.fbdev_emulation=0
bootz 0x42000000 0x43300000 0x43000000
