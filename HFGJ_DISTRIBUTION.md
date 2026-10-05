# HFGJ Nikki 分发层

2026-10-06 本地更新：简化 setup.sh 入口与仅首装模式尚未提交/发布，bootstrap 扩为六脚本。当前线上仍为此前版本。新入口操作说明见 hfgj/INSTALLATION.md；下述旧状态记录不代表当前发布结论。

状态更新：2026-10-05。301W 已由用户手动完成迁移，HFGJ 包登记、实际运行内核及恢复材料已有核验；当前缓存下 provider 映射/专属 DNS 的 12 次节点抽测通过。准确包下载及 stat/入口/构建修复仍为本地待提交、待发布源码。本轮没有设备操作，新的源码不能称为线上或实机验收通过。

已发布源来自 `0445b9c`：https://hfgj.github.io/OpenWrt-nikki/ 。公开 migrate.sh 尚未包含准确下载修复，不能下载覆盖设备已经修复的脚本。301W 首次迁移已完成，不能重复执行首迁入口。设备证据、实际恢复目录和用户手动命令见工作区 `PROXY_RULES_HANDOFF.md` 及 `notes/nikki-301w-manual-migration-and-rollback-20261005.md`；它们是状态记录，不授予新操作权限。

## 维护范围

`main` 保留 `nikkinikki-org/OpenWrt-nikki:main` 官方镜像；`hfgj` 承载分发 Patch。维护自有安装入口、内核包和签名源，不修改 Nikki runtime、LuCI 前端或配置生成。设备内核安装、迁移、更新和回退均由用户手动发起。提交、推送、云端 `publish=false` 预览及正式发布分别确认。

| 内容 | 来源 |
| --- | --- |
| Nikki、LuCI、语言包 | Nikki 官方软件源，原包名不变 |
| HFGJ 稳定内核 | 自有源 `mihomo-hfgj`，提供虚拟依赖 `mihomo` |
| coreutils-stat 及其依赖 | 设备配置的发行版签名源 |
| 本机离线恢复包 | 已认证精确原 IPK 派生的 `recovery.ipk`，不发布 |

当前构建矩阵仅 `openwrt-24.10 / aarch64_cortex-a53`（301W，ImmortalWrt 24.10.2）。脚本包含 ARM64 包架构检查，不代表其他架构已有公开构建或实机验收；APK、其他 CPU 和 Alpha 不在本次范围。

`mihomo-hfgj` 安装 `/usr/libexec/mihomo`，alternatives 为 `300:/usr/bin/mihomo:/usr/libexec/mihomo`；声明与 Meta、Alpha、遗留 rollback 桥接包的 Conflicts/Replaces。SDK 不输出 Replaces 变量，recipe 保留其生成的 CONTROL 后追加真正的 Replaces。RSTRIP 禁用，二进制及摘要必须与 HFGJ Release 一致。包只含 core 和公开身份元数据，不含设备配置。遗留 `mihomo-hfgj-rollback/Makefile` 保留为停用历史，不构建分发。

## 文件职责与先决工具

| 文件 | 作用 |
| --- | --- |
| mihomo-hfgj/Makefile | 包声明，含 `+coreutils-stat`；运行中的 Nikki 在普通包升级 postinst 中重启 |
| rollback-package.sh | `--check-stat`/`--check-tools` 只读检查；`--build`/`--verify` 派生和校验恢复包，无联网、opkg 或服务操作 |
| install.sh | 完整 bootstrap 检查；独立 `--prepare-tools`；用户发起的 `--apply` 首装/迁移及官方 UI 包安装 |
| feed.sh | 检查固件、架构、签名策略，添加已验证的源和公钥，再 opkg update |
| migrate.sh | `--plan` 只读；`--apply` 验证、备份、切换及失败恢复；`--rollback` 本机离线恢复 |
| migrate-job.sh | 校验、plan 后建立持久 job/锁，nohup + setsid 执行迁移，记录真实退出状态 |
| hfgj/scripts/core_feed.py | Release 校验、分发修订号、IPK/索引依赖检查、完整源组装 |
| hfgj/scripts/setup-usign.sh、setup-opkg.sh | 固定官方提交编译隔离测试工具 |

stat 检查实际执行 `-c '%a:%u:%g:%Y'` 和 `-c '%a:%u:%g'`，验证格式和两次结果的一致性；不只检查命令存在。迁移 plan/apply 在下载包、建立事务备份前检查工具及 coreutils-stat 包登记。缺失或不兼容会明确报错，不修改核心/服务。

`install.sh --prepare-tools` 要求 root、受支持固件/opkg、签名策略及完整 bootstrap；当包未登记或 stat 不兼容时，仅执行 `opkg install coreutils-stat`（其包依赖由 opkg 处理），然后重新核验。不添加源、不更新索引、不安装/替换核心，不重启服务。网络、源或安装失败即退出。已安装且可用时不重复安装。包依赖声明不能代替停核前的准备步骤。

## 下载与用户手动操作

以下操作用于修复正式发布或明确交付并核验之后的新设备；本地批准不等于发布授权，现已迁移的 301W 不重跑首迁。

同一次分发下载 `setup.sh`、`feed.sh`、`install.sh`、`migrate.sh`、`migrate-job.sh`、`rollback-package.sh`、`bootstrap.sha256` 到同一目录。manifest 必须恰好包含六个脚本各一次，名称与摘要格式正确，所有文件摘要匹配。install 和后台启动/worker 共用同一检查入口。清单用于完整性检查，首次下载仍需与已审阅的来源/摘要比对；同站清单不是独立信任锚。不使用 curl-to-shell。

首装顺序为：完整 bootstrap 校验 → feed → 工具准备 → migrate apply → 确认 HFGJ 包已登记 → 官方 Nikki/LuCI/语言包。用户手动执行：

```sh
sh install.sh --check-bootstrap
sh install.sh --apply
```

已有 Nikki 的迁移先准备、再预览，最后由用户启动：

```sh
sh install.sh --check-bootstrap
sh feed.sh
sh install.sh --prepare-tools
sh migrate.sh --plan
# 仅用户决定开始切换后执行；网络/SSH 可能中断：
sh migrate-job.sh --start
# 使用启动输出中的真实目录查询：
sh migrate-job.sh --status JOB_DIR
```

plan 不添加源、不下载、不安装、不创建备份/任务、不停服务；它检查工具、包登记、核心入口及存储设置，不声称已完成候选包或功能验收。后台 start 在 plan 通过后才创建 job 和 active.lock；worker 再次核验完整 bootstrap。未完成任务/遗留锁先审阅，不盲删锁或重复启动。状态 running 或启动命令 exit 0 只代表任务已启动；最终 exit_code、事务阶段和包/实际内核/服务核验共同决定结果。

自定义源须显式设置公开 HTTPS `HFGJ_FEED_URL`。可固定公钥 SHA256：`5defecc84474da2e1dcb80017b9ef5052bdf50ccd57b392680c5c385d232eba0`。正式签名钥继续沿用，不关闭签名、不轮换、不输出私钥。官方 Nikki 安装入口明确请求 Meta，不作为 HFGJ 首装入口。

## 准确下载、存储与恢复

opkg `38eccbb1` 的按包名 download 会受 Replaces 候选选择影响。生产脚本从已验签索引中按准确 Package+Version 取得唯一 Filename/SHA256，从对应公开 HTTPS 源下载，再核对摘要和实际 IPK 身份。拒绝重复记录、不安全文件名、错误版本/身份、来源歧义及签名错误；不再按包名 download 原包/候选包。gzip/纯文本缓存都在副本验签，不改原缓存、不跳过签名、不使用 force-depends 或数据库回写。

停核前验证候选架构/版本/依赖/摘要/alternatives/替换关系、`-v` 及精确原 IPK、实际原字节和派生恢复包。原精确版本已从源撤掉时提前拒绝，不以裸二进制替代完整包恢复保证。内核版本不变也不能省略工具和恢复材料校验。

默认持久路径 `/root/hfgj-core-backups`，可设置 `HFGJ_BACKUP_DIR`、`HFGJ_WORK_DIR`、`HFGJ_STORAGE_MOUNT`。指定挂载点必须已挂载、可写、持久；路径不能经符号链接逃出挂载点，不回落 overlay，不以 tmpfs/ramfs 保存恢复材料。备份/工作目录为 700，保留私有配置/缓存快照，不打印或上传其内容。

空间分阶段检查：备份与工作目录同文件系统合计预算；安装前 opkg 原始可用空间需大于完整 Installed-Size，另保留 8 MiB，不计旧文件可回收量。停核后再核对实际剩余空间和峰值。squashfs lower 文件不能贡献 overlay 可回收量，不预先假设 deleted executable 已释放空间。

恢复协议保持 2。备份包含精确原包、实际原字节、recovery.ipk、helper、migrate.sh、摘要及事务状态。恢复包只改 Version 和 Conflicts/Replaces，压缩 payload 原样保留，维护脚本的内容/权限/所有者/时间保持不变。字段 stat 失败立即拒绝，不把两个失败的空结果误判为相等。

捕获到的安装/校验/启动/信号失败触发事务恢复；断电、SIGKILL、文件系统故障不能保证自动恢复。首次无旧核心时没有可恢复的原包。显式离线回退由用户在确认原 job 已结束且锁已释放后执行，使用真实备份目录：

```sh
B=/持久挂载点/实际备份目录
nohup setsid sh "$B/migrate.sh" --rollback "$B" \
  >"$B/manual-rollback.log" 2>&1 </dev/null &
# 完成后检查，后台 shell Done 不是成功判据：
cat "$B/transaction"
```

恢复只检查本机工具和保存材料，不联网补工具，不重放整份 opkg 数据库，不覆盖现用配置/缓存。原包/字节未变可只恢复服务状态，保留旧备份兼容路径；已变但旧协议缺必要材料则停核前拒绝。受控 force-downgrade 只用于验证过的本机派生恢复版本，随后正常升级精确原包并按需恢复实际原字节。结果须为 rolled-back/rolled-back-unchanged，原包版本/入口/摘要正确且服务状态符合原记录。rollback-failed 时保留日志和材料，不盲目重复删装。

普通 LuCI/opkg 升级会触发 core postinst 重启，不是每次升级都有首迁事务回滚保证；仍由用户手动决定设备更新。Zashboard 裸核心更新不维护 opkg 登记，不混用后把包版本当作运行版本。sysupgrade 保留性另行实机验收。

## 云端版本与发布

只消费 `hfgj/mihomo:HFGJ-Stable` 的 ARM64 Release；验证官方 GitHub 资产地址、资产摘要、SHA256SUMS、ARM64 ELF 和 HFGJ 版本标记。不修改内核二进制，SDK/组装使用同一次锁定源码和元数据。

包版本 `X.Y.Z+hfgj.<资产更新时间>-r<分发输入提交时间>`。修订号与分发指纹共用输入：recipe、提交的 targets、feed workflow、五个 bootstrap 脚本及 hfgj/scripts 的普通文件。准备拒绝这些输入的未提交改动，取其历史最大提交时间。内核或分发输入变化必须高于已发布包版本；同版本换指纹、元数据或目标矩阵以及降级均拒绝，拒绝时不生成包参数/输出。新正式修订号在另行批准提交后确定，不能将本地未提交内容打成当前线上版本。assemble 还会使用正式公钥验签现有公开索引，再核对同版本 IPK 摘要；即使 force_build，出现同版本不同字节也拒绝，必须提升修订号。

云端目标覆盖写入忽略的 `hfgj/build/targets.json`，不改已提交 targets；锁定矩阵随构建 artifact 传递至 assemble。目标矩阵变化也要求更高版本。任何源码逻辑变化重建整份源，不自动更新设备。

SDK 只分发 core 一个 IPK；官方工具由设备发行版源提供。组装校验 coreutils-stat 依赖在 IPK 和签名索引中存在且一致，同时核对包身份、版本、架构、payload、替换字段、文件名/大小/摘要、签名、压缩索引及 JSON 索引。所有目标成功后才组成完整 Pages artifact。bootstrap 清单由此次源码生成，六份文件必须逐字节一致。

`hfgj-feed.yml` 手动输入 publish 默认 false。提交/推送、真实 SDK 预览、正式签名源发布各自确认；本轮不执行。定时 feed 受 HFGJ_AUTO_FEED 控制，Pages 受 HFGJ_FEED_ENABLED 控制；Nikki 同步受 HFGJ_AUTO_SYNC 控制。本轮未查询或更改远端开关，不能据历史快照称自动维护已启用。

Nikki 云端同步只允许官方镜像快进，然后合入 hfgj、测试成功后原子推送；冲突/回归/Shell 语法失败不改远端。六个 Shell 文件逐个 sh -n，不能使用只检查第一个脚本的多参数写法。Mihomo Meta→hfgj 上游自动同步尚待独立实现，Alpha 尚未移植。

## 验证范围

本地测试使用一次性 usign 钥、固定版本真实 opkg 的 disposable offline-root、模拟服务/网络/固件路径和本地 Git fixture。覆盖准确下载、工具缺失/不兼容/准备失败、完整 bootstrap、首装顺序、后台启动门槛、分发修订号/版本拒绝、包依赖、恢复/信号/空间/资料损坏及云端同步拒绝。fixture 包安装/服务均在隔离目录，不操作宿主或设备核心。

本轮 75 项完整套件及最后补充的 2 项检查通过，77 项唯一用例、零跳过；另有 12 项工具入口专项检查、Python/Shell/YAML 静态检查及三个 workflow 的 17 段 run 脚本语法检查通过。日志在工作区 artifacts/nikki-stat-distribution-fix-20261005/。历史 SDK 发布已有记录，但本轮变更仍须另行批准云端 publish=false 预览后才可核验新 SDK CONTROL 和产物。本地静态/隔离回归不代表新脚本已在设备部署。真实 provider 内容变化后的热切换、UDP、所有节点、实际离线回退、完整业务上传下载和 sysupgrade 尚未验收。

公开实现依据：[OpenWrt coreutils 包定义](https://github.com/openwrt/packages/blob/openwrt-24.10/utils/coreutils/Makefile)、[Nikki 官方包定义](https://github.com/nikkinikki-org/OpenWrt-nikki/blob/main/nikki/Makefile)、[OpenWrt 包元数据与打包](https://github.com/openwrt/openwrt/blob/openwrt-24.10/include/package-pack.mk)、[SDK Action](https://github.com/openwrt/gh-action-sdk)。
