<img src="desktop/assets/markflow.png" width="88" alt="MarkFlow Logo">

# 印序 MarkFlow

面向设备生产的桌面标签工作室。导入模板，点击开始，读取 ESP32 的 MAC 并生成二维码标签。

本仓库包含桌面客户端、模板示例、模板开发 Skill、客户端测试和构建发布配置，不包含网页管理端或服务端代码。

## 功能

- 本地模板列表，导入 `.labeljob` 或 `.markflow` 模板列表。
- 标签预览、开始和停止；本地导入支持离线生产。
- ESP32 MAC 读取，二维码、文字和 PNG 图标渲染。
- Windows 驱动打印，纸张尺寸、DPI、纸型及毫米偏移设置。
- 可选电脑绑定、接收管理端模板列表和单张测试任务、回传执行结果。
- 每次稳定重新接入打印一张，同一 MAC 可以再次打印；累计次数跨模板、跨重启保留。
- 同次接入不循环打印，短暂 USB 重连防重复；结果未知时停止，等待人工核对。
- GitHub 正式版检查、下载校验、更新重启与失败回退（Windows）。

当前版本为 `0.4.5`，优先支持 Windows 10/11 x64。macOS 代码路径仍在开发验证中，不代表已有同版本正式安装包。当前“开始”入口支持 ESP32 MAC 模板；手动输入示例用于协议兼容，尚未接入新版开始入口。

## 使用

1. 安装打印机厂商的 Windows 驱动。
2. 从本仓库 [Releases](https://github.com/TailorLiuuu/MarkFlow/releases) 下载已发布版本，完整解压后运行 `MarkFlow.exe`。不要只复制 exe，须保留 `_internal` 和 `LabelDeskWorker.exe`。
3. 导入或选择模板，在“打印设置”选择打印机、纸张和毫米偏移。
4. 点击“开始”，接入 ESP32；点击“停止”阻止新的生产和远程测试任务。

发送模板列表本身不会打印。远程“测试打印”可以打印同一个 MAC；本地生产按每次接入打印，同一设备重新插入可以重印。累计次数按成功提交到系统队列统计，结果未知或失败不计入。已经进入系统队列的任务不能通过停止按钮撤回。系统队列接受任务不等于纸面质量已确认。

桌面端无需登录；可选管理连接默认使用公开 Toolbox 服务地址。电脑绑定及服务端访问权限由管理端控制。MAC 读取会重启 ESP32，不会擦写固件。

软件版本和下载统一在本仓库 Releases 维护。Windows 客户端提供“检查更新”：启动后及每 6 小时检查正式版，有新版时提示。点击“下载并更新”后，从本仓库下载并校验，停止生产后安装并重启；启动失败会回退旧程序。模板、绑定和微调保存在独立用户数据目录，不随程序替换。

## 开发与构建

### 模板开发 Skill / 离线工具

[markflow-templates](skills/markflow-templates/SKILL.md) 可创建 30 × 20 mm ESP32 标签，修改 MAC 显示格式、校验、生成预览，以及打包模板列表。技能目录可以单独复制，不依赖安装整个桌面软件。示例使用合成 MAC，不含电脑绑定、生产记录或品牌 Logo。

**直接试跑**：克隆仓库并进入根目录，使用 Python 3.10+（预览推荐 3.11）。创建、校验、格式修改和打包仅需标准库：

```sh
git clone https://github.com/TailorLiuuu/MarkFlow.git
cd MarkFlow
python -m venv .venv
```

Windows PowerShell 执行 `.\.venv\Scripts\Activate.ps1`，macOS/Linux 执行 `source .venv/bin/activate`，然后：

```sh
mkdir .skill-demo
python skills/markflow-templates/scripts/self_test.py
python skills/markflow-templates/scripts/markflow_template.py create --prefix "Device_" --output .skill-demo/device.labeljob
python skills/markflow-templates/scripts/markflow_template.py validate .skill-demo/device.labeljob
python -m pip install -r skills/markflow-templates/scripts/requirements-preview.txt
python skills/markflow-templates/scripts/markflow_template.py preview .skill-demo/device.labeljob --output .skill-demo/device.png
python skills/markflow-templates/scripts/markflow_template.py pack .skill-demo/device.labeljob --output .skill-demo/production.markflow
```

打开生成的 PNG 检查排版，将 `.labeljob` 或 `.markflow` 导入桌面端并选择即可。上述命令不会连接 ESP32 或打印机；输出文件存在时会拒绝覆盖，重复试跑请使用新的输出文件名。预览退出码 2 表示裁切或二维码密度警告；Linux/后台环境缺少字体时可用 `--font /path/to/font.ttf` 指定本地字体。

**作为 Skill 使用**：复制整个 `skills/markflow-templates` 文件夹到自己的 Codex 技能目录（默认 `~/.codex/skills/`，自定义时使用 `$CODEX_HOME/skills/`）；在新会话中输入：

> 使用 $markflow-templates 制作 30×20 标签，底部 MAC 不带冒号，二维码保留原始 MAC，生成导入文件和预览。

已有模板去掉底部 MAC 的冒号：

```sh
python skills/markflow-templates/scripts/markflow_template.py mac-text original.labeljob --element mac --style compact --output compact.labeljob
```

只调整指定文本元素，保留二维码与排版；若设备号流程含前缀或其他格式，脚本会拒绝直接替换。格式说明见 [模板契约](skills/markflow-templates/references/format.md)，管理端交付和后续 MCP 接入边界见 [开发者接入](skills/markflow-templates/references/integration.md)。当前提供的是 Skill 与离线 CLI，未提供联网 MCP 服务。

### 桌面客户端

推荐 Python 3.11，Windows x64：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r desktop/requirements.txt
python -m unittest discover -s tests -v
python desktop/app.py
```

打包及校验：

```powershell
python scripts/privacy_check.py --working-tree
python -m PyInstaller --noconfirm --clean desktop/MarkFlow.spec
python scripts/package_release.py
```

产物写入 `release/`。厂商 SDK DLL 不随源码提供；标准 Windows 驱动打印不依赖它。若需开发原生 SDK 接口，请在具备使用及分发许可后自行放置 `desktop/sdk/LP_LabelPrinter.dll`，Git 默认排除该二进制。

更多说明见 [发布流程](docs/RELEASING.md) 和 [数据与隐私边界](docs/PRIVACY.md)。未指定开源许可证；公开可见不等于授予额外使用或再分发许可。

设备检测与管理端 HTTP、打印机枚举分开执行；USB 每 150 ms 检测，稳定约 0.4 秒开始读取 MAC。读 MAC 会产生 USB 重置，短暂断开会合并；观察到断开持续约 1 秒后再接入可打印下一张。运行消息记录检测到队列提交、读 MAC、排版校验和提交各阶段耗时，便于现场定位。
