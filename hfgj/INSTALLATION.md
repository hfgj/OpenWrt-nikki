# 新路由器简化安装

状态：新增入口仍为本地未发布修改。下列简化命令在包含 setup.sh 的新版签名源正式发布并复核后才能使用。

当前支持 OpenWrt/ImmortalWrt 24.10、AArch64 CPU、aarch64_cortex-a53 包架构及 opkg。需要 Linux 5.13 或以上、firewall4、签名检查及必要工具。其他 CPU/包架构、APK 不在当前发布范围。首次简化入口的真实设备行为仍待用户手动验收。

通过 SSH 以 root 手动执行：

```sh
wget -O /tmp/hfgj-setup.sh https://hfgj.github.io/OpenWrt-nikki/setup.sh &&
sh /tmp/hfgj-setup.sh
```

入口按设备状态处理：

- 全新设备：检查工具与 bootstrap 存储，下载六个脚本、校验清单和公钥；验证清单完整性、每个脚本摘要、固定公钥指纹及入口版本一致性；调用仅首装模式安装 HFGJ 内核，再安装官方 Nikki/LuCI 与语言包。
- 已有 Nikki/Mihomo 包、未登记内核、服务或遗留配置：仅显示已登记包、迁移顺序与本机恢复材料要求后退出，不下载、不修改软件源、不安装或切换服务。这个概要不是已经验证好的设备迁移方案。
- 已登记 mihomo-hfgj：提示在 LuCI/opkg 更新，不重复安装或迁移。

六脚本为 setup.sh、feed.sh、install.sh、migrate.sh、migrate-job.sh 和 rollback-package.sh。首次入口依赖 HTTPS 下载来源；同站 bootstrap.sha256 用于一致性校验，并非独立脚本签名。公钥指纹固定为 5defecc84474da2e1dcb80017b9ef5052bdf50ccd57b392680c5c385d232eba0，feed.sh 再验证软件包索引签名。

下载文件和安装日志保存在打印的 /root/hfgj-install.* 持久目录。日志含 install.sh 的输出，不收集订阅或完整配置。下载、校验失败不会进入安装。仅首装模式在软件源准备前、迁移开始和事务切换前再次检查已有状态；这些检查不是对其他安装进程的全程锁定，不应同时运行其他包安装操作。包、恢复材料及内核安装峰值空间仍由原安装/迁移逻辑检查，入口的 1 MiB 检查只针对 bootstrap。

安装结束后在 LuCI 配置 Nikki，再检查包登记、实际内核版本、服务和业务功能。简化入口不导入另一台路由器的配置，不自动宣称设备验收通过。

## 安装结果检查

以下由用户在设备上手动执行：

```sh
opkg list-installed | grep -E '^(mihomo-hfgj|nikki|luci-app-nikki|luci-i18n-nikki)'
readlink -f /usr/bin/mihomo
/usr/bin/mihomo -v
```

后续在 LuCI 软件包页面刷新列表，按需升级 mihomo-hfgj；Nikki、LuCI 和语言包仍从官方源升级。包升级可能重启核心，由用户手动发起。

## 失败与恢复

入口保留退出码和持久安装日志，失败时先核对包登记和实际服务状态，不盲目重跑。全新设备没有旧核心，不能承诺回退到一个不存在的旧包；需要根据安装失败阶段处理残留状态。

已有内核不会被简化入口迁移。先结合设备实际原包、内核、存储和离线恢复资料核验迁移方案；执行与回退都由用户手动操作。对经过验证且记录了实际备份目录的迁移，离线回退形式为：

```sh
sh /实际备份目录/migrate.sh --rollback /实际备份目录
```

这是命令形式说明，必须将路径替换为该设备已核验的实际备份目录，并先核验恢复资料；不能使用其他设备或旧临时目录的备份。
