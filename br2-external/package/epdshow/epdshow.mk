################################################################################
#
# epdshow
#
################################################################################

EPDSHOW_VERSION = 1.0
EPDSHOW_SITE = $(BR2_EXTERNAL_PB624_PATH)/src/epdshow
EPDSHOW_SITE_METHOD = local
EPDSHOW_LICENSE = GPL-2.0

define EPDSHOW_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) $(TARGET_CONFIGURE_OPTS) -C $(@D)
endef

define EPDSHOW_INSTALL_TARGET_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D) DESTDIR=$(TARGET_DIR) install
endef

$(eval $(generic-package))
