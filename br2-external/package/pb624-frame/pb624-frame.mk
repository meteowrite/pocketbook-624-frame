################################################################################
#
# pb624-frame
#
################################################################################

PB624_FRAME_VERSION = 1.0
PB624_FRAME_SITE = $(BR2_EXTERNAL_PB624_PATH)/src/pb624-frame
PB624_FRAME_SITE_METHOD = local
PB624_FRAME_LICENSE = MIT

define PB624_FRAME_INSTALL_TARGET_CMDS
	mkdir -p $(TARGET_DIR)/usr/lib/pb624-frame
	cp -a $(@D)/pb624frame $(@D)/main.py $(TARGET_DIR)/usr/lib/pb624-frame/
endef

$(eval $(generic-package))
