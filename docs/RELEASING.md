# 构建与发布

软件源码及后续软件发布统一在 `TailorLiuuu/MarkFlow`。管理端在独立工程维护，不进入本仓库。

## 日常检查

`Desktop CI` 在 push 和 pull request 时检查隐私边界、运行客户端测试，并在干净 Windows runner 构建完整软件；构建产物保留在 Actions 中，不自动成为正式 Release。

## 发布 Windows 版本

1. 更新 `desktop/agent_runtime.py` 的 `VERSION`、`desktop/MarkFlow.spec` 中的 macOS 版本元数据及版本说明。
2. 检查隐私扫描、客户端测试和干净构建结果；安装候选包验证真实打印机、ESP32、开始/停止和管理端回执。CI 不连接实际硬件。
3. 提交并推送版本对应代码后创建 `vX.Y.Z` 标签；标签必须与客户端 `VERSION` 一致。
4. `Windows Release` 自动构建 Windows x64 ZIP，生成 SHA256SUMS 和 GitHub Release 草稿。不会直接公开正式发布。
5. 在草稿检查资产、版本说明与实际硬件验证结果，确认后手动发布。

Release 草稿包含客户端 ZIP、校验文件及 GitHub 自带源码归档。默认不包含厂商 DLL、驱动安装器、管理端、用户数据或现场测试截图。不把旧电脑目录或先前现场压缩包直接上传。

当前使用手动下载安装更新；本仓库暂未实现应用内更新检查或静默更新。macOS 构建、签名、公证和更新仍需单独完成，不将未验证的包标为正式发布。

`LabelDeskWorker`、旧数据目录和 `InfinLabLabelDesk` 启动项名称暂时保留用于升级兼容，不应随显示名称变更而清空用户数据。
