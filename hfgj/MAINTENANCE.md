# Nikki 云端维护与签名源

范围限于上游 Nikki main 和已正式发布的 HFGJ-Stable Linux ARM64 内核。当前目标：OpenWrt 24.10 / aarch64_cortex-a53。不处理 Alpha，不自动更新设备，不新增跨仓库凭据。

## 触发和开关

- HFGJ_AUTO_SYNC=true：每日 UTC 03:41（北京时间 11:41）同步 Nikki 上游；手动同步也受此开关约束。
- HFGJ_AUTO_FEED=true：允许 Nikki hfgj push 和每六小时 UTC :23 检查构建并申请发布。GitHub 定时任务可能延迟；检查间隔不是升级时限保证。
- HFGJ_FEED_ENABLED=true：允许验证通过的签名源部署到 Pages。自动发布还需要 AUTO_FEED；单独开启 FEED_ENABLED 不启用自动维护。
- 手动 feed 默认 publish=false，只输出预览。显式 publish=true 且 FEED_ENABLED 开启才部署，不受 AUTO_FEED 约束。force_build 不授予发布权限。
- 上游同步成功后显式触发 feed，补偿工作流令牌推送不会触发普通 push 工作流的行为。仅 AUTO_FEED 为 true 时传入 publish=true，否则生成预览。
- Mihomo 更新由六小时检查发现，跨仓库即时触发另行讨论。无变化时跳过构建和部署。

## 检查与失败处理

同步先运行回归与语法检查，再原子推送 main 和 hfgj；冲突或检查失败停止。feed 锁定源码 SHA、内核版本和摘要，验证包内容、签名及索引并拒绝降级。全部目标构建和组装通过后，部署前重新读取远端 hfgj；源码已过期、分支缺失或读取失败均阻止发布。重新运行才能获取新源码，不自动重试写入。

feed 共用并发组，避免多个 feed 同时部署；同步使用独立并发组。源码检查与 Pages 部署不是原子事务，检查后分支仍可能更新。内核下载期间频道变化导致元数据不一致时失败，下一次运行重新获取。

已通过同步检查并推送的源码，不因后续 SDK 构建或部署失败自动回退。构建、组装或保护检查失败保留已有 feed；部署本身失败需核对 Pages 实际状态。通过 Actions 失败状态报告，未配置额外通知或监控。

## 分步启用和验收

本地修改、提交、推送、首次预览、正式发布、首次启用开关分别按用户批准范围执行。手动预览示例仅在该步骤获批后执行：

```sh
gh workflow run hfgj-feed.yml -R hfgj/OpenWrt-nikki --ref hfgj -f publish=false
```

预览检查锁定源码、内核版本、IPK 内容和摘要、签名索引及预览文件。发布后下载公开源复核 feed-state 和实际文件；部署成功不等于设备验收。

首次启用 AUTO_SYNC/AUTO_FEED 需要明确授权。启用后正常批准范围内自动维护不逐次确认；异常停止并报告。触发 Actions 后提供运行链接与待核验项目，等用户告知完成再查询，不持续轮询。

设备更新、迁移、内核切换、服务停止/重启及回退由用户手动执行。交付命令时核对实际设备和备份，不复用旧临时脚本或 manifest。
