include $(TOPDIR)/rules.mk

PKG_NAME:=mihomo-hfgj
# Generated from a verified HFGJ release by hfgj/scripts/core_feed.py.
-include $(CURDIR)/core.mk
PKG_VERSION:=$(if $(HFGJ_PACKAGE_VERSION),$(HFGJ_PACKAGE_VERSION),0.0.0)
PKG_RELEASE:=$(if $(HFGJ_PACKAGE_RELEASE),$(HFGJ_PACKAGE_RELEASE),1)
PKG_SOURCE:=$(HFGJ_CORE_ASSET)
PKG_SOURCE_URL:=$(HFGJ_CORE_BASE_URL)
PKG_HASH:=$(HFGJ_CORE_SHA256)
PKG_LICENSE:=GPL-3.0-only
PKG_MAINTAINER:=HFGJ

include $(INCLUDE_DIR)/package.mk
# Preserve the exact, already stripped Release binary and its verified digest.
RSTRIP:=:

define Package/mihomo-hfgj
  SECTION:=net
  CATEGORY:=Network
  TITLE:=HFGJ Mihomo stable core
  URL:=https://github.com/hfgj/mihomo
  DEPENDS:=@aarch64 +ca-bundle +ip-full +kmod-inet-diag +kmod-tun
  PROVIDES:=mihomo
  CONFLICTS:=mihomo-meta mihomo-alpha
  ALTERNATIVES:=300:/usr/bin/mihomo:/usr/libexec/mihomo
endef

define Package/mihomo-hfgj/description
  HFGJ stable Mihomo with provider hosts and node DNS support.
  Nikki and LuCI remain official packages. Update this core through opkg/LuCI.
endef

# Temporary dependency provider during either package-name transition.
# OpenWrt 24.10's standard SDK does not emit a Replaces control field.
define Package/mihomo-hfgj-rollback
  SECTION:=net
  CATEGORY:=Network
  TITLE:=HFGJ core rollback dependency bridge
  URL:=https://github.com/hfgj/OpenWrt-nikki
  PROVIDES:=mihomo
endef

define Package/mihomo-hfgj-rollback/description
  Temporary dependency provider for migrate.sh migration/rollback only.
  Do not install this package as a usable Mihomo core.
endef

define Build/Prepare
	[ -n "$(HFGJ_CORE_VERSION)" ] || { echo "Run core_feed.py prepare first" >&2; exit 1; }
	mkdir -p $(PKG_BUILD_DIR)
	gzip -dc $(DL_DIR)/$(PKG_SOURCE) > $(PKG_BUILD_DIR)/mihomo
	chmod 0755 $(PKG_BUILD_DIR)/mihomo
endef

define Build/Configure
endef

define Build/Compile
endef

define Package/mihomo-hfgj/install
	$(INSTALL_DIR) $(1)/usr/libexec
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/mihomo $(1)/usr/libexec/mihomo
	$(INSTALL_DIR) $(1)/usr/share/mihomo-hfgj
	$(INSTALL_DATA) $(CURDIR)/core.json $(1)/usr/share/mihomo-hfgj/core.json
endef

define Package/mihomo-hfgj-rollback/install
	$(INSTALL_DIR) $(1)/usr/share/mihomo-hfgj-rollback
	echo "Temporary dependency bridge; no core binary" > $(1)/usr/share/mihomo-hfgj-rollback/README
endef

# A LuCI package upgrade must switch the running core as well as the file.
# The migration script stops Nikki first, so it controls its own start/rollback.
define Package/mihomo-hfgj/postinst
#!/bin/sh
if [ -z "$${IPKG_INSTROOT}" ] && [ -x /etc/init.d/nikki ]; then
	if /etc/init.d/nikki running >/dev/null 2>&1; then
		/etc/init.d/nikki restart || exit 1
	fi
fi
exit 0
endef

$(eval $(call BuildPackage,mihomo-hfgj))
$(eval $(call BuildPackage,mihomo-hfgj-rollback))
