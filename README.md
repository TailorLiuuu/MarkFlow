<img src="desktop/assets/markflow.png" width="88" alt="MarkFlow Logo">

# 印序 MarkFlow

面向设备生产的桌面标签工作室。导入模板，点击开始，读取 ESP32 的 MAC 并生成二维码标签。

本仓库仅包含桌面客户端、模板示例、客户端测试和构建发布配置，不包含网页管理端或服务端代码。

## 功能

- 本地模板列表，导入 `.labeljob` 或 `.markflow` 模板列表。
- 标签预览、开始和停止；本地导入支持离线生产。
- ESP32 MAC 读取，二维码、文字和 PNG 图标渲染。
- Windows 驱动打印，纸张尺寸、DPI、纸型及毫米偏移设置。
- 可选电脑绑定、接收管理端模板列表和单张测试任务、回传执行结果。
- 生产去重、任务持久化与中断恢复；不对结果未知的任务自动重打。
- GitHub 正式版检查、下载校验、更新重启与失败回退（Windows）。

当前版本为 `0.4.3`，优先支持 Windows 10/11 x64。macOS 代码路径仍在开发验证中，不代表已有同版本正式安装包。当前“开始”入口支持 ESP32 MAC 模板；手动输入示例用于协议兼容，尚未接入新版开始入口。

## 使用

1. 安装打印机厂商的 Windows 驱动。
2. 从本仓库 [Releases](https://github.com/TailorLiuuu/MarkFlow/releases) 下载已发布版本，完整解压后运行 `MarkFlow.exe`。不要只复制 exe，须保留 `_internal` 和 `LabelDeskWorker.exe`。
3. 导入或选择模板，在“打印设置”选择打印机、纸张和毫米偏移。
4. 点击“开始”，接入 ESP32；点击“停止”阻止新的生产和远程测试任务。

发送模板列表本身不会打印。远程“测试打印”可以打印同一个 MAC；生产仍按模板和设备去重。已经进入系统队列的任务不能通过停止按钮撤回。系统队列接受任务不等于纸面质量已确认。

桌面端无需登录；可选管理连接默认使用公开 Toolbox 服务地址。电脑绑定及服务端访问权限由管理端控制。MAC 读取会重启 ESP32，不会擦写固件。

软件版本和下载统一在本仓库 Releases 维护。Windows 客户端提供“检查更新”：启动后及每 6 小时检查正式版，有新版时提示。点击“下载并更新”后，从本仓库下载并校验，停止生产后安装并重启；启动失败会回退旧程序。模板、绑定和微调保存在独立用户数据目录，不随程序替换。

## 开发与构建

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
