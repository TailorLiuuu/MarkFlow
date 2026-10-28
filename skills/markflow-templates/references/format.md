# 模板契约与排版

基线：MarkFlow 0.4.5，生产包版本 1。可运行的校验器在 `scripts/runtime/label_contract.py`；完整最小示例在 `assets/esp32-30x20.labeljob`。

## 包结构

单模板 `.labeljob` 是 UTF-8 JSON：

```json
{
  "format": "label-production", "version": 1, "name": "设备标签",
  "template": {
    "format": "label-desk", "version": 1, "name": "30x20",
    "width": 30, "height": 20, "elements": []
  },
  "workflow": {"variables": {}, "steps": [
    {"id":"source","type":"read_esp32_mac"},
    {"id":"format","type":"format_id","case":"upper","separator":"compact","prefix":""},
    {"id":"preview","type":"preview"},
    {"id":"print","type":"print_label"}
  ]}
}
```

以上仅表示结构，空 `elements` 无效。单模板最多 2 MiB、1–60 个元素；宽高各 10–150 mm。标签元素的 `x/y/w/h` 和文字 `font` 均为毫米；原点是标签左上角。元素须在边界内。`font` 为 0.6–15 mm，实际能否放下由渲染检查。

| type | 必需或常用属性 |
|---|---|
| text | `id,x,y,w,h,font,content`；可选 `bold`、`align: left/center/right` |
| qr | `id,x,y,w,h,content`；w 必须等于 h |
| image | `id,x,y,w,h,image_png`；可选 `crop_white`，PNG Base64 不带 data URL 前缀 |
| line / rect | `id,x,y,w,h` |

PNG 最大 2048 × 2048，编码长度不超过 1,500,000 字符。颜色会转换为碳带打印的黑白图；不要用深浅颜色区分关键信息。像素图需干净边缘，白底或透明底均可。校验 PNG 头不等于能解码，最终还须预览。

## 变量与 MAC

支持 `device_id,mac,mac_last4,mac_first3,mac_last3,model,batch,date`，使用 `{{field}}`，不支持 JS、表达式、网络 URL 或自定义执行节点。

输入 `02:00:00:00:C3:AC` 时：

| 字段 | 示例含义 |
|---|---|
| mac | `02:00:00:00:C3:AC`，原始地址 |
| mac_last4 | `C3AC` |
| mac_first3 | `02:00:00` |
| mac_last3 | `00:C3:AC` |
| device_id | 标准 compact 流程下为 `02000000C3AC` |

`format_id` 只处理 `device_id`，不修改 `mac`。`separator` 可为 compact/colon/keep，`case` 可为 upper/lower/keep，还可追加前缀。若编号用于其他用途，不能为了改显示而随意修改整条流程；应先判断该变更会影响哪些文本和二维码。

工作流为 3–8 步：数据源一项，零或多个 format_id，最后 preview、print_label。当前桌面“开始”支持 read_esp32_mac；manual_input 虽可通过契约校验，并未接入当前生产入口。preview 节点表示排版处理，不保证弹出人工确认框。

## 渲染与纸面

- 使用 203/300/600 DPI，像素约等于毫米 × DPI ÷ 25.4。300 DPI 下 30 × 20 mm 约为 354 × 236 像素。
- 二维码含四模块静区且取整数像素模块。每模块少于两点拒绝，少于三点警告。不要裁掉静区来让二维码显得更大。
- QR 元素框包括静区；Logo 框通常只围绕图案，所以“两个框一样大”不等于视觉一样大。当前 30 × 20 布局示例在有 Logo 时用 14 mm QR 框和约 8.89 mm 图案框作为起点，以预览和实际扫码为准。
- 字体环境可能不同，预览不能替代 Windows 目标机检查。校验只检查边界和契约，不自动发现所有元素互相遮挡。
- 后台 Qt 可能不自动载入 Windows 字体。CLI 默认加载系统 Arial；其他环境可用 `preview --font font.ttf` 注册本地字体。没有可用字体时拒绝生成预览；仍需目视检查中文或特殊字符是否缺字。
- 不把工站的 X/Y 微调写进所有用户的模板。微调后越界、标签跨缝、模糊扫码分别检查偏移、测纸/间隙、分辨率和打印浓度。

## 列表与摘要

模板列表是 `{"format":"markflow-list","version":1,"templates":[...]}`，建议扩展名 `.markflow`，1–30 项。桌面整批校验后导入；本 CLI 额外拒绝重复摘要。

摘要算法为：对 bundle 执行 `json.dumps(ensure_ascii=False, sort_keys=True, separators=(',',':'), allow_nan=False)` 后取 UTF-8 字节的 SHA-256。不能用带缩进的文件字节哈希替代。任何内容变更都会产生新摘要；摘要代表模板内容而非稳定的云端模板 ID。
