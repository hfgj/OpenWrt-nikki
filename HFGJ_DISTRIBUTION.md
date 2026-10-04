# HFGJ Nikki 分发层

最新本地改造：2026-10-05 用户批准并实施正常包替换、恢复包协议及真实 opkg CI 检查；保留此前大分区、后台任务和 gzip 验签修复。首次设备迁移与旧脚本手动回滚均因普通 remove 的虚拟依赖检查失败，原内核未被替换；最后只读核对 Nikki 已运行。下文首次预览的历史结果不代表本次修复已发布或设备验证通过。

状态：2026-10-05 迁移修复为本地待发布源码。2026-10-04 软件源已经上线，但线上及设备旧迁移脚本存在已复现的依赖失败，不能重跑。本次仍需另行完成真实 SDK 构建、签名源更新和设备准备/切换；正式 usign 钥继续沿用。

## 维护范围

本仓库 fork 为 `hfgj/OpenWrt-nikki`。`main` 保留官方 `nikkinikki-org/OpenWrt-nikki:main` 镜像，`hfgj` 承载分发 Patch。维护自己的安装入口、内核包和签名源，不修改 Nikki runtime、LuCI 前端或配置生成流程。

设备上的来源固定为：

| 内容 | 软件源 / 包 |
| --- | --- |
| Nikki、LuCI、语言包 | Nikki 官方软件源，原包名不变 |
| HFGJ 稳定内核 | 自有源的 `mihomo-hfgj`，提供虚拟依赖 `mihomo` |
| 本机离线恢复 | 由精确原 IPK 派生的 `recovery.ipk`，仅保存在备份目录，不发布到软件源 |

`mihomo-hfgj` 安装 `/usr/libexec/mihomo`，通过 alternatives 提供 `/usr/bin/mihomo`，优先级 300；对 `mihomo-meta` / `mihomo-alpha` / 遗留 `mihomo-hfgj-rollback` 声明 Conflicts 和 Replaces。包只含内核及公开来源/摘要元数据，不含机场配置或设备配置。SDK 二次 strip 被禁用，保持 Release 二进制和 SHA256 一致。

OpenWrt 24.10 的标准 SDK 不输出 `Replaces` 变量。自有 recipe 在 SDK 生成 CONTROL 后显式追加真正的 Replaces 字段，随后由 SDK 打包、生成并签署索引；实际 IPK 和索引均须通过严格校验。直接安装新 IPK，由 opkg 正常替换旧包和遗留过渡包。此前“Provides 过渡包 + 普通 remove”的方案被真实设备依赖检查拒绝，已经停用；旧线上脚本和设备 ready 标记不能继续使用。

本次 2026-10-05 修复仍为本地待发布源码。完整 SDK 构建、线上签名索引和 301W 安装/alternatives/服务/连通性验收尚未完成。密钥不轮换，内核二进制仍来自已有 HFGJ-Stable Release；新包发布需要独立批准。

当前只开放 **24.10 / opkg / 已核实的 aarch64 包架构**。用户于 2026-10-04 回传 301W 实际信息：ImmortalWrt 24.10.2、`qualcommax/ipq807x`、`aarch64_cortex-a53`；opkg 接受 all/noarch/aarch64_cortex-a53，架构优先级分别为 1/1/10。修复后的 `/usr/bin/mihomo` 解析为 `/usr/libexec/mihomo`，符合包定义的入口布局。`hfgj/targets.json` 已据此填写唯一目标 `openwrt-24.10 / aarch64_cortex-a53`。这些输出不等于 HFGJ 内核已安装或功能验证通过。25.12/SNAPSHOT 的 APK、其他 CPU、Alpha 都需后续单独适配。

## 文件职责

| 文件 | 具体作用 |
| --- | --- |
| `mihomo-hfgj/Makefile` | SDK 包装已验证的 HFGJ ARM64 Release，包含正常升级时的 Nikki 重启钩子 |
| `mihomo-hfgj-rollback/Makefile` | 停用历史 recipe，不再构建或分发 |
| `rollback-package.sh` | 停核前派生/验证本机恢复包，保留原压缩 payload 和维护脚本，不执行 opkg/服务操作 |
| `hfgj/scripts/setup-opkg.sh` | 从官方固定提交编译设备同版本 opkg，供 CI 的 offline-root 测试使用 |
| `hfgj/scripts/setup-usign.sh` | 为 CI 从 OpenWrt 官方仓库编译固定提交的 usign；不依赖 Ubuntu apt 提供该包 |
| `hfgj/scripts/core_feed.py` | 锁定 Release 版本/摘要、生成包参数、检查 IPK 与 signed index、组装完整软件源 |
| `feed.sh` | 检查固件/架构/签名策略，验证自有索引，再添加公钥和软件源；保留现有 Nikki 源 |
| `migrate.sh` | 默认只预览；明确 `--apply` 后完成验证、备份、停服务、包名切换、检查和启动；支持回滚 |
| `install.sh` | 首装入口：先接入源并安装自有 core，再安装官方 Nikki/LuCI/语言包 |
| `.github/workflows/hfgj-feed.yml` | 云端锁定内核→SDK 打包和签名→全部目标校验→预览/Pages 发布 |
| `.github/workflows/hfgj-sync.yml` | 云端同步 Nikki 官方 main，测试成功后原子推送镜像和 Patch 分支 |
| `.github/workflows/hfgj-check.yml` | PR、hfgj push、手动触发本地检查 |

继承的官方构建、发布、dependabot、stale 和日志清理任务加入官方仓库条件，在 HFGJ fork 中不运行。不要使用 GitHub 通用 Sync Fork 更新 `hfgj`；它可能覆盖自己的安装入口。Git 的 `upstream` 只是官方远端的名字，云端 workflow 会自行添加并 fetch；后续正常更新不依赖本地 clone。

## 云端更新链路

Mihomo 的稳定 Release 由 `hfgj/mihomo` 负责。此仓库只消费已发布的 `HFGJ-Stable`，接受 `vX.Y.Z-hfgj.<12位提交>`，拒绝官方/Alpha/本地实验版本。准备任务验证 GitHub 资产地址、SHA256SUMS、资产 API 摘要、ARM64 ELF 及版本标记；所有 SDK 和组装 job 使用同一次准备锁定的源码提交、Release 参数和摘要。

包版本是 `X.Y.Z+hfgj.<资产更新时间>-r<修订号>`，修订号来自包定义/校验工具最近提交时间。内核有新发布或包逻辑变化时，opkg 能识别更高版本；自动源更新拒绝降级。其他分发脚本变化也会重建整份源；内容不变则跳过。需要发布回退版本时必须另行设计版本策略，不能简单复用较低版本。

组装任务要求每个目标只含 core 一个包，验证包版本、架构、文件内容、alternatives、Replaces/Conflicts、元数据、usign 签名、索引文件名/长度/摘要、压缩索引和 JSON 索引。任何目标失败都不发布；发布用完整 Pages artifact 替换整份源。

启用后每 6 小时检查一次 HFGJ 稳定 Release；Nikki 上游同步每天执行。同步仅允许镜像快进，然后把官方更新合入自己的 Patch，测试通过才 `git push --atomic` 同时更新两分支。冲突或测试失败不更新远端，不覆盖已发布源。冲突需要人工审阅，可在 GitHub 环境修复；不能承诺所有未来冲突自动解决。

GitHub token 产生的 push 不会自动触发另一个 push workflow，所以 Nikki 同步 workflow 显式 dispatch 打包 workflow。**此仓库不负责把 MetaCubeX 更新合入 hfgj/mihomo**；Mihomo 的 `Meta → hfgj` 自动同步与冲突处理需要独立核对/设置，不能拿 Nikki 同步代替。Alpha 仍未移植。

## 第一次云端启用前要做什么

用户已批准首次云端预览阶段：提交/推送、内核 Release、签名钥和预览所需远端设置。正式钥和预览所需远端设置已完成；五平台稳定内核已发布，签名源预览已核验。正式 Pages 源上线及设备迁移仍需另行确认。

1. **已完成：** 用户回传设备固件、目标、opkg 架构及修复后的入口；`hfgj/targets.json` 已填写实际目标：

   ```json
   {"include":[{"branch":"openwrt-24.10","arch":"aarch64_cortex-a53"}]}
   ```

2. 先提交/推送获准的 Mihomo Patch，完成 `HFGJ-Stable` 的首个五平台 Release。此仓库不接受 `-hfgj.local.*` 本地产物作为正式源输入。
3. 为 core feed **生成一次并保管一套 usign 密钥**。无需购买证书，也不能复用/获得官方 Nikki 的私钥。私钥只放离线备份和 GitHub secret，不提交到 Git。公钥公开。CI 配置：

   | 类型 | 名称 | 用途 |
   | --- | --- | --- |
   | Secret | `HFGJ_KEY_BUILD` | usign 私钥原始文本，交 SDK 签名 |
   | Secret | `HFGJ_KEY_BUILD_PUB` | 配套公钥原始文本，组装时验证 |
   | Variable | `HFGJ_FEED_URL` | 可选；默认 `https://hfgj.github.io/OpenWrt-nikki` |
   | Variable | `HFGJ_TARGETS` | 可选；覆盖 targets.json 的显式矩阵 JSON |
   | Variable | `HFGJ_AUTO_FEED` | `true` 才运行周期打包 |
   | Variable | `HFGJ_FEED_ENABLED` | `true` 才允许 Pages 发布 |
   | Variable | `HFGJ_AUTO_SYNC` | `true` 才允许 Nikki 云端同步/推送 |

4. 提交/推送获准的 Nikki `hfgj` Patch，把默认分支设为 `hfgj`，启用 GitHub Pages 的 Actions 部署；检查 Actions 权限、分支保护和 Environment 审批规则。定时任务从默认分支读取。首次手动打包先保持 `publish=false`，下载预览产物检查真实 SDK IPK。
5. 首次真实 SDK 包检查通过，再确认发布并设置 `HFGJ_FEED_ENABLED=true`，手动 `publish=true`。首次迁移实机成功后，再启用周期更新和自动同步。

签名校验不应被关闭。正式公钥文件 SHA256 为 `5defecc84474da2e1dcb80017b9ef5052bdf50ccd57b392680c5c385d232eba0`。bootstrap 可显式 `export HFGJ_KEY_SHA256=5defecc84474da2e1dcb80017b9ef5052bdf50ccd57b392680c5c385d232eba0` 固定该公钥。密钥轮换需要额外设计，不能删掉旧信任后期待旧设备自动接受新钥。

## 设备首次迁移与首装

脚本目前没有发布，以下是启用后的执行方式说明，不能现在照着未上线 URL 安装。

自定义部署根地址时，先显式 `export HFGJ_FEED_URL=实际公开HTTPS根地址`；默认地址无需设置。从同一次正式 feed 下载 `feed.sh`、`install.sh`、`migrate.sh` 到同一目录，先检查内容，再明确执行 `sh install.sh --apply`。不要重跑官方的一键安装脚本作为 HFGJ 安装入口，它明确请求官方 `mihomo-meta`。

已有 Nikki 的迁移也可以分步执行：`feed.sh` 添加签名源，`migrate.sh --plan` 检查现有登记/入口，然后批准后 `migrate.sh --apply`。plan 不添加源、不下载、不停服务、不安装。feed.sh 添加源并执行 opkg update，本身不替换 core。

apply 在停服务前检查：注册包、alternatives 布局、签名索引、候选 IPK、架构/版本/摘要、可执行 `-v`、空间、**与现有包版本完全相同的原 IPK**、本机恢复包及 helper。官方源已撤掉旧包且本机没有可取得的精确原 IPK 时，迁移会提前拒绝；不要用只有二进制、没有原包的备份承诺包状态可回滚。

opkg 的 `src/gz` 索引缓存可能保留 gzip 字节，也可能是纯文本。迁移脚本在临时文件中解压或复制缓存，在该副本上验签后读取版本和 SHA256，不改原缓存，也不因格式差异跳过验签。安装包摘要必须匹配同一份已验证的索引。

备份在 `/root/hfgj-core-backups/migration.*`，权限限制为 root：原 IPK、原实际二进制、包状态快照、Nikki UCI、私有配置/缓存快照。脚本不输出配置、订阅或密钥内容，也不把它们上传云端。迁移后备份不会自动删除，需确认实机功能通过后另行清理。

停 Nikki 后直接安装已验证的新 core IPK，核对登记版本、旧包清除、入口/实际摘要并按原运行状态启动。安装、校验、启动失败及可捕获的 HUP/INT/TERM 会触发自动回滚；断电、SIGKILL 或设备文件系统故障无法保证自动执行回滚。显式还原：`sh migrate.sh --rollback /root/hfgj-core-backups/实际目录`。还原先检查原包/原字节是否未变；未变时只恢复原服务状态。发生切换时检查恢复包/helper 摘要与派生关系，受控安装较低的 `原版本~hfgjrestore` 恢复包，再正常升级到未修改的精确原 IPK，最后按需恢复实际原字节；不重放整份 opkg 数据库，不覆盖现用配置/缓存。无旧 core 的首装没有可自动恢复的旧包；失败需检查当前包/服务状态后再重试。

正常 LuCI 更新时升级 `mihomo-hfgj`；Nikki、LuCI、语言包继续从官方源更新，已安装的虚拟依赖由自己的包满足。自有 core 的 postinst 会在 Nikki 原本运行时重启，使新二进制生效。**首次迁移的自动回滚不是每次普通 LuCI 升级的回滚保证**；正常升级仍需保留可恢复版本并在设备验证。

Zashboard 更新 provider 仍走 Mihomo 原来的 API/定时/缓存流程，无需改 Nikki。路由器 core 更新优先走 LuCI/opkg；Zashboard 的 core updater 会直接换二进制，不同步 opkg 记录，不能混用后仍把包版本当作实际运行版本。

sysupgrade 的源、公钥、包重装与配置恢复须另行实机验证。不能将普通 LuCI 包升级结论扩大到刷固件一定保留。

## 检查范围与待完成项

本地 Python 测试使用模拟 Release/IPK、一次性 usign 测试钥、隔离的路由器目录/opkg 和本地 Git 仓库，覆盖签名/索引篡改、错误版本/架构、额外配置入包、迁移/回滚、安装/启动/TERM 失败、空间不足、缺原版包、损坏备份、同步成功/冲突/测试失败。测试钥与正式 feed 钥独立；正式钥已按本阶段授权创建并配置，私钥不在 Git 中。

本机没有可运行的 Docker daemon，真实 SDK 构建在 GitHub Actions 完成。[签名源预览 37176161081](https://github.com/hfgj/OpenWrt-nikki/actions/runs/37176161081) 使用源码 `67cecafc1edef254ac4b855e8841fc8245dfe3e2`，prepare、SDK build、assemble 成功，deploy 跳过（publish=false）。对应检查任务 37176135508 通过全部 16 项测试。

已下载锁定内核、原始 SDK 包和签名预览，核验正式公钥指纹、usign 签名、两个 IPK 的控制字段/内容、索引摘要/大小、压缩索引和 JSON 索引。本地重新组装结果与云端预览逐文件一致。内核版本为 `v1.19.31-hfgj.fd80bb057aba`，IPK 版本为 `1.19.31+hfgj.20261004035505-r1791086903`；包内二进制与 HFGJ-Stable Linux ARM64 资产完全一致。

Pages 源仍未上线，三个自动开关未启用；未在 301W 操作。LuCI 升级、实际 provider 连接及上传下载仍待最后的实机验证。Mihomo 的上游云端同步仍需独立实现。

公开实现依据：

- [Nikki 官方 install.sh](https://github.com/nikkinikki-org/OpenWrt-nikki/blob/main/install.sh)
- [Nikki 官方包定义](https://github.com/nikkinikki-org/OpenWrt-nikki/blob/main/nikki/Makefile)
- [OpenWrt 24.10 版本定义](https://github.com/openwrt/openwrt/blob/openwrt-24.10/include/package-defaults.mk)
- [OpenWrt 24.10 包元数据与打包实现](https://github.com/openwrt/openwrt/blob/openwrt-24.10/include/package-pack.mk)
- [OpenWrt 24.10 索引和签名实现](https://github.com/openwrt/openwrt/blob/openwrt-24.10/package/Makefile)
- [OpenWrt 官方 SDK Action](https://github.com/openwrt/gh-action-sdk)

## 外置持久存储与后台任务（本地待发布）

`migrate.sh` 新增 `HFGJ_BACKUP_DIR`、`HFGJ_WORK_DIR`、`HFGJ_STORAGE_MOUNT`。前两个分别保存持久回滚资料及下载/解包/opkg 临时文件；指定挂载点时必须已挂载为可写持久文件系统，路径不能通过符号链接逃出挂载点。备份和工作目录拒绝 tmpfs/ramfs；默认仍支持 /root 的持久路径。

空间计算分阶段：停服务前检查备份、工作目录预算，同一文件系统合计预算。opkg 在移除旧包之前要求原始可用空间大于完整 Installed-Size，因此停前及停后都检查较大候选/原包的 Installed-Size，再保留 8 MiB；这一步不计旧 core 可回收空间。停服务后读取实际剩余空间，结合随后能移除的现有 core，要求足以容纳新内核或回滚时“原包内核 + 实际原字节的临时恢复文件”的较高峰值，再加 8 MiB。不会预先计入 deleted executable 可能释放的空间；overlay 的 squashfs lower 文件也不计入可回收量。空间不足且包未动时，恢复原运行状态并退出。

每个事务保留独立 backup/work 目录；state 记录工作目录，手工回滚使用原工作目录。opkg 通过 `--tmp-dir` 将解包放入工作目录。回滚前验证原包、原字节、恢复包、helper 与派生关系，停止服务后重新核验空间，再使用正常包替换与版本升级完成逆向切换。不使用 force-depends、force-reinstall 或包数据库回写。受控 force-downgrade 只用于本地已验证恢复版本。

`migrate-job.sh --start` 要求同目录五个脚本及 `bootstrap.sha256`，校验后复制到持久 job 目录，以 nohup + setsid 新会话运行。stdout/stderr、job.log、status 不连接 SSH，网络断连不会要求继续输入交互命令。任务锁拒绝重复启动；发生 SIGKILL/掉电等导致的遗留锁必须先审阅，不自动清理。TERM/INT 会传给迁移子进程并等待回滚，完成后保存真实退出码和释放锁。`--status JOB_DIR` 读取保存的状态。status 尚为 starting/running 时不能把它当作成功；启动命令 exit 0 也仅代表已发起任务。

301W 候选设置（当前脚本尚未发布，不能下载在线旧脚本后使用这些参数）：

```sh
export HFGJ_STORAGE_MOUNT=/mnt/mmcblk0p9
export HFGJ_BACKUP_DIR=/mnt/mmcblk0p9/hfgj-core/backups
export HFGJ_WORK_DIR=/mnt/mmcblk0p9/hfgj-core/work
export HFGJ_KEY_SHA256=5defecc84474da2e1dcb80017b9ef5052bdf50ccd57b392680c5c385d232eba0
# 从重新发布且核验过的源取得五个脚本和 bootstrap.sha256，再在该目录执行：
sh migrate-job.sh --start
# 输出的 JOB_DIR 用于后续查询：
sh migrate-job.sh --status JOB_DIR
```

core 仍安装在 /usr/libexec/mihomo，Nikki/配置/规则体系不迁移到新分区。任务源码自带的 SHA256 清单用于完整性核验；首次下载仍须与已审阅来源摘要比对，不把从同一网站取得的清单称为独立信任锚。候选 IPK 始终由 usign 签名索引验证。

本轮仅本地修改及隔离测试，不代表已经在 301W 实测。原设备只剩约 11 MiB overlay、p9 可用约 1.75 GiB；实际停止旧进程后是否满足峰值门槛，以届时读取结果为准。p9 开机挂载机制尚未确认，恢复前也要确认该分区已挂载。

## 2026-10-05 回滚协议与验收范围

新 state 使用 recovery_protocol=2，记录原 IPK/实际二进制、recovery.ipk/helper、候选内核摘要、工作目录及必需挂载点。备份同时保留 migrate.sh 与 rollback-package.sh。transaction 记录 prepared、stopping、installing-candidate、verifying-candidate、completed 及分阶段恢复状态。包管理器退出码为零仍须核对实际文件和登记，不将后台启动或 service running 当作迁移/功能验收通过。

恢复包只修改 Version 与 Conflicts/Replaces，data.tar.gz 原字节不重压，维护脚本及其权限/所有者/时间保持原样。最终原 IPK 为正常版本升级，原控制字段和包登记恢复；派生版本不留在成功结果。helper 仅支持当前 SDK 的 gzip/tar IPK 和平面控制目录，使用 opkg 支持的 USTAR；不支持的格式、路径或字段在停核前拒绝。

旧备份只在原包、版本、入口和实际字节均未变时支持服务恢复。原内核已改变的旧备份在停核前拒绝，不会再次重放已知错误的 remove 路径。恢复中断可保留在派生版本，脚本返回失败并保留日志和离线资料；只尝试启动有登记且摘要属于已验证备份/包的可用内核，重复恢复可继续回到精确原版本。

CI 使用官方 opkg-lede 固定提交 38eccbb1fd694d4798ac1baf88f9ba83d1eac616 的原生程序，在独立 offline-root 测试新 helper 的 Meta/Alpha 双向恢复、遗留过渡包清理与 HFGJ 同名恢复。事务模拟测试额外覆盖安装/部分写入/启动/信号/恢复各阶段失败、已停止服务、旧备份、空间及备份破坏。另将生产 migrate.sh/rollback-package.sh 接到真实 opkg，验证正向切换、显式恢复及新内核启动失败的自动恢复；服务和下载使用 fixture，绝对 alternatives 链接按 offline-root 解释。隔离 fixture 验证不代替真实 SDK 或设备功能验证。
