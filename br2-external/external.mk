include $(sort $(wildcard $(BR2_EXTERNAL_PB624_PATH)/package/*/*.mk))

# pybind11 3.x's _version.py reads its headers from site-packages/include,
# but Buildroot only installs them to staging (breaks python-pillow 12).
define PB624_PYBIND_HEADERS_LINK
	mkdir -p $(PYTHON_PYBIND_INSTALL_PATH)/../include
	ln -snf $(STAGING_DIR)/usr/include/pybind11 \
		$(PYTHON_PYBIND_INSTALL_PATH)/../include/pybind11
endef
PYTHON_PYBIND_POST_INSTALL_STAGING_HOOKS += PB624_PYBIND_HEADERS_LINK
