# HFGJ Nikki 分发层

状态：2026-10-04 本地实现和检查完成；用户已批准首次云端构建阶段，正在执行。正式 usign 钥已生成并配置到本 fork 的两个 Actions Secrets；软件源和设备尚未部署。本文的安装地址仍不是已上线的源。

## 维护范围

本仓库 fork 为 `hfgj/OpenWrt-nikki`。`main` 保留官方 `nikkinikki-org/OpenWrt-nikki:main` 镜像，`hfgj` 承载分发 Patch。维护自己的安装入口、内核包和签名源，不修改 Nikki runtime、LuCI 前端或配置生成流程。

设备上的来源固定为：

| 内容 | 软件源 / 包 |
| --- | --- |
| Nikki、LuCI、语言包 | Nikki 官方软件源，原包名不变 |
| HFGJ 稳定内核 | 自有源的 `mihomo-hfgj`，提供虚拟依赖 `mihomo` |
| 包名切换的临时依赖 | `mihomo-hfgj-rollback`，不含内核，脚本使用后移除 |

`mihomo-hfgj` 安装 `/usr/libexec/mihomo`，通过 alternatives 提供 `/usr/bin/mihomo`，优先级 300；与 `mihomo-meta` / `mihomo-alpha` 冲突。包只含内核及公开来源/摘要元数据，不含机场配置或设备配置。SDK 二次 strip 被禁用，保持 Release 二进制和 SHA256 一致。

OpenWrt 24.10 的标准 SDK 不输出 `Replaces` 字段，因此脚本在两种方向的包名迁移中使用依赖过渡包：先安装过渡包，移除旧内核包，安装目标包，最后移除过渡包。整个过程保持 Nikki 的虚拟 `mihomo` 依赖有提供者；不使用 `--force-depends`，不编辑 opkg 数据库。过渡包单独安装不能提供可运行的代理内核。

当前只开放 **24.10 / opkg / 已核实的 aarch64 包架构**。用户于 2026-10-04 回传 301W 实际信息：ImmortalWrt 24.10.2、`qualcommax/ipq807x`、`aarch64_cortex-a53`；opkg 接受 all/noarch/aarch64_cortex-a53，架构优先级分别为 1/1/10。修复后的 `/usr/bin/mihomo` 解析为 `/usr/libexec/mihomo`，符合包定义的入口布局。`hfgj/targets.json` 已据此填写唯一目标 `openwrt-24.10 / aarch64_cortex-a53`。这些输出不等于 HFGJ 内核已安装或功能验证通过。25.12/SNAPSHOT 的 APK、其他 CPU、Alpha 都需后续单独适配。

## 文件职责

| 文件 | 具体作用 |
| --- | --- |
| `mihomo-hfgj/Makefile` | SDK 包装已验证的 HFGJ ARM64 Release，包含正常升级时的 Nikki 重启钩子 |
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

组装任务要求每个目标只含 core 和过渡两个包，验证包版本、架构、文件内容、alternatives、元数据、usign 签名、索引文件名/长度/摘要、压缩索引和 JSON 索引。任何目标失败都不发布；发布用完整 Pages artifact 替换整份源。

启用后每 6 小时检查一次 HFGJ 稳定 Release；Nikki 上游同步每天执行。同步仅允许镜像快进，然后把官方更新合入自己的 Patch，测试通过才 `git push --atomic` 同时更新两分支。冲突或测试失败不更新远端，不覆盖已发布源。冲突需要人工审阅，可在 GitHub 环境修复；不能承诺所有未来冲突自动解决。

GitHub token 产生的 push 不会自动触发另一个 push workflow，所以 Nikki 同步 workflow 显式 dispatch 打包 workflow。**此仓库不负责把 MetaCubeX 更新合入 hfgj/mihomo**；Mihomo 的 `Meta → hfgj` 自动同步与冲突处理需要独立核对/设置，不能拿 Nikki 同步代替。Alpha 仍未移植。

## 第一次云端启用前要做什么

用户已批准首次云端预览阶段：提交/推送、内核 Release、签名钥和预览所需远端设置。当前钥已配置，其他步骤按执行结果核对；正式 Pages 源上线及设备迁移仍需另行确认。

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

apply 在停服务前检查：注册包、alternatives 布局、签名索引、候选 IPK、架构/版本/摘要、可执行 `-v`、空间、**与现有包版本完全相同的原 IPK**、过渡包。官方源已撤掉旧包且本机没有可取得的精确原 IPK 时，迁移会提前拒绝；不要用只有二进制、没有原包的备份承诺包状态可回滚。

备份在 `/root/hfgj-core-backups/migration.*`，权限限制为 root：原 IPK、原实际二进制、包状态快照、Nikki UCI、私有配置/缓存快照。脚本不输出配置、订阅或密钥内容，也不把它们上传云端。迁移后备份不会自动删除，需确认实机功能通过后另行清理。

停 Nikki 后安装过渡包并切换 core 包，核对路径/摘要并按原运行状态启动。安装、校验、启动失败及可捕获的 HUP/INT/TERM 会触发自动回滚；断电、SIGKILL 或设备文件系统故障无法保证自动执行回滚。显式还原：`sh migrate.sh --rollback /root/hfgj-core-backups/实际目录`。还原检查备份摘要，通过 opkg 恢复原包后恢复实际原字节，不重放整份 opkg 数据库，不覆盖现用配置/缓存。无旧 core 的首装没有可自动恢复的旧包；失败需检查当前包/服务状态后再重试。

正常 LuCI 更新时升级 `mihomo-hfgj`；Nikki、LuCI、语言包继续从官方源更新，已安装的虚拟依赖由自己的包满足。自有 core 的 postinst 会在 Nikki 原本运行时重启，使新二进制生效。**首次迁移的自动回滚不是每次普通 LuCI 升级的回滚保证**；正常升级仍需保留可恢复版本并在设备验证。

Zashboard 更新 provider 仍走 Mihomo 原来的 API/定时/缓存流程，无需改 Nikki。路由器 core 更新优先走 LuCI/opkg；Zashboard 的 core updater 会直接换二进制，不同步 opkg 记录，不能混用后仍把包版本当作实际运行版本。

sysupgrade 的源、公钥、包重装与配置恢复须另行实机验证。不能将普通 LuCI 包升级结论扩大到刷固件一定保留。

## 检查范围与待完成项

本地 Python 测试使用模拟 Release/IPK、一次性 usign 测试钥、隔离的路由器目录/opkg 和本地 Git 仓库，覆盖签名/索引篡改、错误版本/架构、额外配置入包、迁移/回滚、安装/启动/TERM 失败、空间不足、缺原版包、损坏备份、同步成功/冲突/测试失败。测试钥与正式 feed 钥独立；正式钥已按本阶段授权创建并配置，私钥不在 Git 中。

本机没有可运行的 Docker daemon，**没有执行真实 OpenWrt SDK 构建**。YAML/内嵌 shell/差异检查与模拟测试通过，不代表真实 IPK、GitHub Actions、LuCI 升级或 301W 的 provider 连通性验证通过。这些在首个云端预览与最后实机部署阶段完成。

公开实现依据：

- [Nikki 官方 install.sh](https://github.com/nikkinikki-org/OpenWrt-nikki/blob/main/install.sh)
- [Nikki 官方包定义](https://github.com/nikkinikki-org/OpenWrt-nikki/blob/main/nikki/Makefile)
- [OpenWrt 24.10 版本定义](https://github.com/openwrt/openwrt/blob/openwrt-24.10/include/package-defaults.mk)
- [OpenWrt 24.10 包元数据与打包实现](https://github.com/openwrt/openwrt/blob/openwrt-24.10/include/package-pack.mk)
- [OpenWrt 24.10 索引和签名实现](https://github.com/openwrt/openwrt/blob/openwrt-24.10/package/Makefile)
- [OpenWrt 官方 SDK Action](https://github.com/openwrt/gh-action-sdk)
