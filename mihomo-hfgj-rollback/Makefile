include $(TOPDIR)/rules.mk

PKG_NAME:=mihomo-hfgj-rollback
-include $(CURDIR)/core.mk
PKG_VERSION:=$(if $(HFGJ_PACKAGE_VERSION),$(HFGJ_PACKAGE_VERSION),0.0.0)
PKG_RELEASE:=$(if $(HFGJ_PACKAGE_RELEASE),$(HFGJ_PACKAGE_RELEASE),1)
PKG_LICENSE:=GPL-3.0-only
PKG_MAINTAINER:=HFGJ

include $(INCLUDE_DIR)/package.mk

# A distinct source package is required by gh-action-sdk's PACKAGES interface.
define Package/mihomo-hfgj-rollback
  SECTION:=net
  CATEGORY:=Network
  TITLE:=HFGJ core rollback dependency bridge
  URL:=https://github.com/hfgj/OpenWrt-nikki
  DEPENDS:=@aarch64
  PROVIDES:=mihomo
endef

define Package/mihomo-hfgj-rollback/description
  Temporary dependency provider for migrate.sh migration/rollback only.
  Do not install this package as a usable Mihomo core.
endef

define Build/Prepare
	[ -n "$(HFGJ_CORE_VERSION)" ] || { echo "Run core_feed.py prepare first" >&2; exit 1; }
	mkdir -p $(PKG_BUILD_DIR)
endef

define Build/Configure
endef

define Build/Compile
endef

define Package/mihomo-hfgj-rollback/install
	$(INSTALL_DIR) $(1)/usr/share/mihomo-hfgj-rollback
	echo "Temporary dependency bridge; no core binary" > $(1)/usr/share/mihomo-hfgj-rollback/README
endef

$(eval $(call BuildPackage,mihomo-hfgj-rollback))
