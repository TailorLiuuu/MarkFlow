# 版本维护

基线来源：`https://github.com/TailorLiuuu/MarkFlow`，v0.4.5，提交 `dc10b368d1685d5186d12a5d9b875409070e0283`。

`scripts/runtime/label_contract.py` 和 `label_render.py` 是对应客户端的原样副本，使技能单独解压也能使用。CLI 只调用 render_label，不调用依赖工站偏移的 translate_label。不要把整个桌面应用及其数据目录复制进技能。

升级步骤：

1. 对照目标客户端的契约、渲染器和模板库导入逻辑，检查字段、文件格式、工作流与容量限制。
2. 同步这两个文件，调整 CLI 和文档。保留旧客户端兼容性说明，不以新代码校验通过代替旧客户端可用。
3. 运行 `python scripts/self_test.py`；安装预览依赖后渲染默认模板与现有模板，在实际 Windows 客户端验证导入。检查预览文字、QR 内容和 Logo，纸面验证单独记录。
4. 打包时只包含 SKILL.md、agents、scripts、references、assets 中的源文件；排除缓存、虚拟环境、凭据、真实设备号、绑定数据、日志和个人路径。

源码公开可见不等于附带额外使用或分发许可；MarkFlow 原仓库尚未指定开源许可证。对外分发权限需由代码所有者确认。随附中性模板无品牌 Logo，第三方 Python 依赖按各自许可证使用。
