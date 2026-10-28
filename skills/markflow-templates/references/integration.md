# 开发者接入与交付

## 本地接入

解压技能文件夹，Codex 用户放到 `~/.codex/skills/markflow-templates`，其他支持 SKILL.md 的助手按其技能目录安装。非 AI 开发者也可以直接调用 `scripts/markflow_template.py`，标准输出为 JSON：退出 0 成功，1 为错误，2 为预览警告。安装后新会话可用 `$markflow-templates` 调用。

导出 `.labeljob` 或 `.markflow` → 桌面“导入” → 选择目标模板 → 核对预览/打印机/纸型/DPI/微调 → “开始”。不要将 `preview` 命令当成打印命令。

模板数据保持动态变量，不把试验 MAC 固化进模板。目录和品牌资产来自调用方；包中不带厂商驱动、Logo 商标授权或现场配置。

## 管理端交付

优先使用管理页现有的编辑、发布和发送列表功能。若环境有已授权的接口客户端，可依照该部署的接口与鉴权约定调用。下面是当前管理端协议摘要，属于版本相关信息，接入前检查实际部署，不能把它当成公开、无鉴权的 API：

基路径 `/tools/label-studio/api/control/`：

| 方法/路径 | 输入或用途 |
|---|---|
| GET templates | 当前账号模板列表 |
| GET templates/{id} | bundle、etag、versions |
| POST save | `{id?,etag?,bundle}` 保存草稿 |
| POST publish | 同上，保存并新增不可变发布版本 |
| GET stations | 当前账号绑定电脑及在线/接收状态 |
| POST send-list | `{station,templates:[{id,revision}]}` 下发已发布版本列表 |

当前管理调用依赖登录会话，写入检查同源及 `X-CSRF-Token`，不是可直接交给第三方的 API Key 接口。不要从浏览器提取凭据、复制数据库、用 SSH 绕过正常访问流程，或将一次现场维护操作写成开发者接入方案。缺少授权会话时仍可完成本地模板文件，交由用户导入。

修改已有模板须读取当前 etag；遇到 409 刷新并比较用户/他人的修改，再决定如何合并，不盲目覆盖。发布结果包含 revision 和 digest，发送列表使用模板 ID + revision。保留用户原列表中其他模板，除非要求替换它们。

发送后核对电脑收到目标摘要/目录版本。接收成功不代表当前选中，也不代表打印完成：0.4.5 本地库按内容摘要保存，新版可能与旧版并存，须在电脑选择新版。不要自动改写用户数据文件或重启正在生产的应用来完成选择。

实际试印属于独立动作，指定目标电脑、模板版本、打印机配置和数量；请求 ID 应稳定且只提交一次。网络超时或结果 unknown 时先查记录与纸面，不能通过换一个请求 ID 自动重印。打印计数按队列提交成功统计，不证明标签已经完整输出。

## 后续 MCP 边界（尚未实现）

当前交付是 Skill + 离线 CLI，不是已运行的 MCP Server。需要程序化接入时可以把同一实现包装为：

| 建议工具 | 副作用 |
|---|---|
| template_create / template_validate / template_preview / template_pack | 仅生成调用方指定的本地文件 |
| template_publish | 新增云端发布版本 |
| station_list / delivery_status | 读取已授权的工站信息 |
| template_send_list | 修改指定电脑的模板目录 |
| station_test_print | 物理打印，一次一张并跟踪结果 |

联网部分应先由管理端提供正式开发者凭据和模板/电脑范围授权；认证材料由运行环境注入，不写入技能、模板或工具返回。区分只读、发布、下发与实际打印权限。尚未提供这些接口前，不宣称第三方已能无登录远程控制打印机。
