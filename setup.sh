#!/bin/sh
# User-run entry point. Existing installations never enter the fresh-install path.
set -eu
umask 077
[ $# = 0 ] || { echo '用法：sh setup.sh（全新安装；已有内核仅显示说明）' >&2; exit 1; }
[ "$(id -u)" = 0 ] || { echo '请以 root 执行。' >&2; exit 1; }
command -v opkg >/dev/null || { echo '当前仅支持 opkg，尚不支持 APK。' >&2; exit 1; }
. /etc/openwrt_release
case "$DISTRIB_RELEASE" in 24.10|24.10.*) ;; *) echo '当前仅支持 OpenWrt/ImmortalWrt 24.10。' >&2; exit 1 ;; esac
[ "$DISTRIB_ARCH:$(uname -m)" = aarch64_cortex-a53:aarch64 ] || {
    echo '当前公开包仅支持 aarch64_cortex-a53 / AArch64。' >&2; exit 1;
}
hfgj_existing=
hfgj_using_own=0
for hfgj_package in mihomo-hfgj mihomo-meta mihomo-alpha mihomo-hfgj-rollback mihomo nikki luci-app-nikki; do
    hfgj_status=$(opkg status "$hfgj_package") || { echo '读取软件包状态失败，停止。' >&2; exit 1; }
    if printf '%s\n' "$hfgj_status" | grep -q '^Status: .* installed$'; then
        hfgj_existing="$hfgj_existing $hfgj_package"
        [ "$hfgj_package" != mihomo-hfgj ] || hfgj_using_own=1
    fi
done
if [ "$hfgj_using_own" = 1 ]; then
    echo '已安装 mihomo-hfgj。本入口不重复安装或迁移。'
    echo '请在 LuCI 软件包页面刷新列表，按需升级 mihomo-hfgj、Nikki 和 LuCI。'
    exit 0
fi
if [ -n "$hfgj_existing" ] || [ -e /usr/bin/mihomo ] || [ -L /usr/bin/mihomo ] ||
   [ -e /usr/libexec/mihomo ] || [ -L /usr/libexec/mihomo ] || [ -e /etc/init.d/nikki ] || [ -L /etc/init.d/nikki ] ||
   [ -e /etc/config/nikki ] || [ -e /etc/nikki ]; then
    printf '发现已有 Nikki/Mihomo；已登记包：%s\n' "${hfgj_existing:-无；可能为手动安装}"
    echo '迁移计划：核对包与实际内核 → 验证旧包及离线恢复材料 → 备份 → 用户手动切换 → 验收。'
    echo '请先准备针对本机的迁移和离线回退命令；本入口未下载、安装软件或切换服务。'
    exit 0
fi
for hfgj_tool in wget sha256sum awk grep mktemp df uname usign jsonfilter tar gzip readlink du cut sed sort uniq wc cp chmod find mv rm mkdir dirname basename cat; do
    command -v "$hfgj_tool" >/dev/null || { printf '缺少工具：%s；未开始安装。\n' "$hfgj_tool" >&2; exit 1; }
done
[ -x /sbin/fw4 ] || { echo '需要 firewall4。' >&2; exit 1; }
hfgj_kernel=$(uname -r)
hfgj_major=${hfgj_kernel%%.*}
hfgj_minor=${hfgj_kernel#*.}; hfgj_minor=${hfgj_minor%%.*}
case "$hfgj_major:$hfgj_minor" in *[!0-9:]*|:*|*:) echo '无法识别 Linux 内核版本。' >&2; exit 1 ;; esac
[ "$hfgj_major" -gt 5 ] || { [ "$hfgj_major" -eq 5 ] && [ "$hfgj_minor" -ge 13 ]; } || {
    echo 'Nikki 需要 Linux 内核 5.13 或以上。' >&2; exit 1;
}
grep -Eq '^[[:space:]]*option[[:space:]]+check_signature([[:space:]]|$)' /etc/opkg.conf || {
    echo '请先启用 opkg 签名检查。' >&2; exit 1;
}
# Bound bootstrap download storage; the existing installer checks package/backup peak space.
hfgj_available=$(df -Pk /root | awk 'END {print $4}')
case "$hfgj_available" in ''|*[!0-9]*) echo '无法读取持久存储剩余空间。' >&2; exit 1 ;; esac
[ "$hfgj_available" -ge 1024 ] || { echo '安装文件目录至少需要 1 MiB 空间。' >&2; exit 1; }
case "$(df -Pk /root | awk 'END {print $1}')" in tmpfs|ramfs) echo '安装资料目录必须位于持久存储。' >&2; exit 1 ;; esac
HFGJ_FEED_URL=https://hfgj.github.io/OpenWrt-nikki
HFGJ_OFFICIAL_URL=https://nikkinikki.pages.dev
HFGJ_KEY_SHA256=5defecc84474da2e1dcb80017b9ef5052bdf50ccd57b392680c5c385d232eba0
export HFGJ_FEED_URL HFGJ_OFFICIAL_URL HFGJ_KEY_SHA256
hfgj_entry=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/$(basename -- "$0")
hfgj_dir=$(mktemp -d /root/hfgj-install.XXXXXX)
printf '安装资料和日志：%s\n' "$hfgj_dir"
cd "$hfgj_dir"
# Preserve diagnostic files on failure; never reuse a previous temporary bundle.
for hfgj_file in setup.sh feed.sh install.sh migrate.sh migrate-job.sh rollback-package.sh bootstrap.sha256 key-build.pub; do
    wget -q -O "$hfgj_file" "$HFGJ_FEED_URL/$hfgj_file" || {
        printf '下载失败：%s；未开始安装。资料：%s\n' "$hfgj_file" "$hfgj_dir" >&2; exit 1;
    }
done
awk '
    BEGIN {split("setup.sh feed.sh install.sh migrate.sh migrate-job.sh rollback-package.sh", names, " "); for(i in names) expected[names[i]]=1}
    NF!=2 || length($1)!=64 || $1~/[^0-9a-f]/ || !($2 in expected) || seen[$2]++ {bad=1}
    END {exit (bad || NR!=6)}
' bootstrap.sha256 || { echo '安装清单不完整或格式错误；未开始安装。' >&2; exit 1; }
sha256sum -c bootstrap.sha256 || { echo '安装文件校验失败；未开始安装。' >&2; exit 1; }
[ "$(sha256sum key-build.pub | cut -d ' ' -f 1)" = "$HFGJ_KEY_SHA256" ] || {
    echo '公钥指纹不匹配；未开始安装。' >&2; exit 1;
}
[ "$(sha256sum "$hfgj_entry" | cut -d ' ' -f 1)" = "$(sha256sum setup.sh | cut -d ' ' -f 1)" ] || {
    echo '安装入口与当前发布版本不同，请重新下载入口；未开始安装。' >&2; exit 1;
}
echo '校验通过，开始安装 HFGJ 内核及官方 Nikki/LuCI。'
if sh install.sh --apply-fresh > install.log 2>&1; then
    cat install.log
    echo '安装完成。请进入 LuCI 配置 Nikki，再验证节点、DNS、上传下载和 UDP。'
else
    hfgj_rc=$?
    cat install.log >&2
    printf '安装失败（%s）。日志：%s/install.log；先检查实际包和服务状态，不要盲目重跑。\n' "$hfgj_rc" "$hfgj_dir" >&2
    exit "$hfgj_rc"
fi
