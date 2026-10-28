---
name: markflow-templates
description: 为印序 MarkFlow 创建、修改、校验和预览 ESP32 标签模板，导出可导入桌面端的 .labeljob 或模板列表；适用于 MAC、二维码、文字和 PNG Logo 的标签排版及模板交付。
---

# MarkFlow 模板

以用户提供的当前模板为编辑起点，输出可导入 MarkFlow 的数据文件。只有新建模板时使用随附的 30 × 20 mm 示例。不要把历史工站的纸张、偏移、设备地址或品牌视为其他用户的默认配置。

## 工作方式

1. 确定标签尺寸、打印 DPI、哪些是可见文字、哪些进入二维码。未提供时可先按 30 × 20 mm、300 DPI 制作并说明假设。
2. 修改前读取现有模板的元素和 workflow；不要打印 `image_png` 的整段 Base64。布局参考 [references/format.md](references/format.md)。
3. 运行下面的离线工具生成新文件。所有输出拒绝覆盖已有文件；原模板留作对照。纯格式变更保留位置、字号、Logo、二维码和流程，除非用户也要求修改。
4. 用合成 MAC 做 `validate` 和 `preview`，查看 PNG。预览退出码 2 表示有裁切或二维码密度警告，修复或明确说明，不当成已通过。
5. 交付模板文件、预览以及具体变更。要导入或远程下发时，读取 [references/integration.md](references/integration.md)。只有用户要求试印时才执行实际打印，并区分接收、提交队列和纸面确认。

## 离线命令

命令中的 `SKILL_DIR` 指本技能目录；把它替换为实际路径。创建、修改、校验和列表打包仅需 Python 3.10+ 标准库；预览建议 Python 3.11 的独立虚拟环境。

```sh
python SKILL_DIR/scripts/markflow_template.py create --name "设备标签" --prefix "Device_" --output device.labeljob
python SKILL_DIR/scripts/markflow_template.py validate device.labeljob
python -m pip install -r SKILL_DIR/scripts/requirements-preview.txt
python SKILL_DIR/scripts/markflow_template.py preview device.labeljob --dpi 300 --output device.png
python SKILL_DIR/scripts/markflow_template.py pack device.labeljob --output production.markflow
```

新建时可加 `--logo logo.png`，把本地 PNG 内嵌。默认不包含品牌资产。其他纸张尺寸直接编辑 JSON 后校验，`create` 的预设仅针对 30 × 20 mm。

例如“底部 MAC 去掉冒号，二维码不变”：

```sh
python SKILL_DIR/scripts/markflow_template.py mac-text original.labeljob --element mac --style compact --output compact.labeljob
python SKILL_DIR/scripts/markflow_template.py preview compact.labeljob --output compact.png
```

`{{mac}}` 是原始带冒号地址，`{{device_id}}` 是流程处理后的设备号。只有设备号流程为无前缀、大写、去分隔符时，才可用 `{{device_id}}` 表示纯无冒号 MAC。脚本会拒绝不满足此条件的模板，避免将品牌前缀或其他编号误当成 MAC。

## 交付边界

- 本工具不联网、不打开串口、不打印，也不读取电脑绑定和用户数据目录；执行模板文件不会执行任意代码。
- 保留品牌大小写；`RingHi_` 和 `RingHI_` 不相同。
- 模板位置和打印机毫米偏移分别管理。居中图片不能证明纸面居中；跨标签间隙优先核对纸型、尺寸及测纸校准。
- 保存模板、发布版本、发送列表、桌面选择、开始生产是不同动作。当前客户端收到新版本后不一定自动选中。
- 不将数据库、绑定凭据、实际 MAC、用户名路径或生产记录放入共享示例。

本技能的校验和渲染基线为 MarkFlow 0.4.5。版本更新时按 [references/maintenance.md](references/maintenance.md) 同步，不能仅更新文档中的版本号。
