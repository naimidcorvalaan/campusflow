# Streamlit Community Cloud 可行性检查

> 2026-09-13 后续更新：本文保留可行性检查时的状态。Git 产物与公开发布前卫生的最新结果见[公开发布前检查](publication_hygiene.md)。

发布提交前复核：入口、配置和 TZ 相关离线测试 99 passed；本轮没有继续修改 Cloud 代码或业务逻辑。公开仓库从最终开发 HEAD 独立导出，具体操作见[公开快照步骤](public_snapshot_release.md)。

检查日期：2026-09-12。基线：`main / 263d970`，保留此前未提交的 README 与 `docs/assets/readme/`。
本轮没有使用真实密钥、调用 TJU API、提交、推送或部署。

## 结论

**适合先做受限访问的个人试用 / 演示部署；尚不能承诺模型在云端可用，也不适合直接向不受信任用户开放个人档案。**

页面运行、模型网络、长期保存是三件不同的事。本轮解决了 console 入口导入问题，并补充显式服务器时区生效处理；
业务状态机、Windows 启动器、视觉、路网和数据库结构不变。

- 正式 Main file path：`src/p2_live_main.py`。
- 推荐在 Cloud Advanced settings 明确选择 **Python 3.12**，保持 `streamlit==1.31.1` 及当前 requirements。
- 必须在真实云实例验证 Cloud → TJU 的网络与服务权限。用户设备上的 VPN 无法代替服务器网络。
- SQLite 可作为实例内临时档案，但不是 Community Cloud 上的可靠长期存储。
- 第一阶段优先设置为仅指定人员可访问；平台访问控制不等于 CampusFlow 内部逐用户认证。

## A. 可以复用的部分

| 检查项 | 代码事实与判断 |
| --- | --- |
| 正式入口 | 文件末尾调用 `main()`；部署不使用 bat，不依赖 prototype server |
| 依赖声明 | 正式 `src` 的第三方导入为 Streamlit、requests、dotenv、pypdfium2、defusedxml，均已声明 |
| Pillow | PDF 页图使用 `bitmap.to_pil()`；锁定的 Streamlit 1.31.1 已声明 `pillow>=7.1.0,<11`，不是仅存在于开发机的隐式安装 |
| Linux 数据目录 | `default_data_directory()` 已处理 Linux XDG/home，显式 `CAMPUSFLOW_DATA_DIR` 优先；创建父目录后连接 SQLite |
| CSS / 品牌 | `Path(__file__)` 定位 `workspace.css`；品牌为内嵌资源，字体使用浏览器字体族，不读取 Windows 字体文件 |
| 校园地图 | 三个 `data/*.json` 已被 Git 跟踪；地图注册表以模块位置定位，大小写与文件名一致，无开发机外部文件依赖 |
| 地图计算 | 基于高德地图数据构建并本地化的北洋园、卫津路校园路网，继续在本地计算；不增加运行时地图 API |
| DOCX | 上传 bytes → `BytesIO` / ZIP / defusedxml；不启动 Word、LibreOffice，不解压原件到仓库 |
| PDF | 上传 bytes → PDFium 文字提取或渲染 → 内存 PNG；不依赖 Poppler、Ghostscript 或 Windows 命令 |
| 图片 | 浏览器上传字节，不读取访问者的本地绝对路径；模型需要的图像数据由服务器构造 |
| 移动端 | sidebar、行动、时间线、估时、设置均为浏览器 UI；无桌面本地软件协议、文件路径或 prototype 地址依赖 |

`pypdfium2` 官方发布支持 Linux 预构建分发，PDFium 通常随 wheel 提供。当前没有需要添加 `packages.txt` 的已证实启动依赖。
未嵌入中文字体的特殊 PDF，仍需在目标 Linux 实例观察字体替代效果；若确有缺字，再评估字体包，而不是提前安装整套办公软件。

Windows 专用逻辑位于 `启动 CampusFlow.bat`、`start_campusflow.bat`、启动器及 Windows 子进程管理辅助脚本。
这些属于本地一键启动路径，不是正式页面的 Linux blocker。`%LOCALAPPDATA%` 仅在持久化路径的 Windows 分支使用。

### 上传限制的现状

当前 `.streamlit/config.toml` 设置 `server.maxUploadSize = 5`，所以浏览器入口统一先受 **5MB** 限制。
图片业务上限也是 5MB；DOCX / PDF 读取器自身上限为 10MB，但正常页面上传会先受到服务器 5MB 限制。
这是既有配置差异，本轮不更改上传规则，不能宣传为正式页面可上传 10MB 文档。

PDF 最多 20 页、每次最多 5 个需要看图的页面；渲染长边不超过 1800 像素，页图总字节有限制。
DOCX 还有 ZIP 展开体积、成员数、XML 大小等检查。文件原件、提取全文与 PDF 页图不作为个人档案保存；
上传内容仍会存在服务器会话内存中，复杂材料与并发上传的内存峰值需要目标实例验证。

## B. 必须的小适配与配置

### 已完成：console 入口导入

隔离副本从仓库根目录执行 `streamlit run src/p2_live_main.py`，复现 `ModuleNotFoundError: No module named 'src'`。
Streamlit console 入口加入的是脚本目录，不能依赖调用者已经把仓库根目录放进 `sys.path`。
本地启动器使用 `python -m streamlit`，因此此前本地运行没有暴露这个差异。

在正式入口的项目导入前，以 `__file__` 计算并补入仓库根目录。修复后相同 console 启动与正式页面加载通过。
没有修改工作目录、业务函数、widget key 或正式状态。

### 已完成：显式时区在启动后生效

当前页面与日期控件使用 `datetime.now()` / `date.today()`。服务器系统时区不能被假定为天津时区，尤其会影响午夜前后的日期与当天课表。

新增 `src/runtime_environment.py`：在配置载入后，只有明确设置 `TZ` 且系统支持 `time.tzset()` 时刷新进程时区。
Cloud Secrets 建议 `TZ = "Asia/Shanghai"`。POSIX 进程可能缓存时区，单纯在解释器启动后写环境变量不够可靠。
未配置 TZ 的本地环境不变；Windows 没有该接口，继续使用系统时钟。它不是用户设置，也不修改时间覆盖规则。

### Secrets 不需要新增 caller 配置系统

Streamlit 的**顶层标量 Secrets 会成为进程环境变量**。已核对仓库锁定的 1.31.1 实现：bootstrap 载入 Secrets，
`Secrets` 将顶层值写入 `os.environ`。因此现有 `load_project_configuration()` 与 TJU caller 可直接复用。

在 Community Cloud 的 Advanced settings → Secrets 中填写：

```toml
TJU_LLM_BASE_URL = "https://your-service.invalid/v1"
TJU_LLM_MODEL = "your-model-id"
TJU_LLM_API_KEY = "replace-with-your-service-key"
TZ = "Asia/Shanghai"
CAMPUSFLOW_DATA_DIR = "/tmp/campusflow"
```

前三项必须换成当前服务分配的值，其余为云端演示配置。不要加 `[tju]` 分组，否则这些值不会自动成为现有 caller 使用的环境变量。
不要把真实 TOML 提交到仓库。可复制的无密钥示例见 [Secrets 模板](../deploy/streamlit-community.secrets.toml.example)。

本地继续使用 process env / `.env`，现有环境变量优先于 dotenv。Cloud root Secrets 本身会向进程环境注入值，
不要再同时配置同名但不同值的 Cloud Secrets。`/tmp/campusflow` 只用于接受丢失的云端演示，不影响 Windows 档案。

## C. 必须真实部署后验证的风险

### 最大未知：Cloud 服务器 → TJU 服务

```text
电脑 / 手机 / iPad → Streamlit Cloud 页面及 WebSocket
Streamlit Cloud 中的 Python requests → TJU LLM service
```

模型请求由服务器上的 `requests.post()` 发出，使用配置端点、Bearer key 与请求超时。
代码没有创建校园 VPN、没有配置学校 IP 白名单，也没有把模型请求交给用户浏览器。
手机开 TJU VPN，只影响手机的网络，不会给 Cloud 实例建立到学校的通道。

学校是否要求校园来源 IP、VPN、项目专属白名单或特定服务权限，不能由当前代码或历史网页服务说明推断。
TLS 使用 requests 默认校验，未发现 `verify=False`、Windows 证书路径或硬编码代理；requests 仍会遵循云进程的代理 / CA 环境配置。

后续需要在获授权的目标实例验证：DNS/TLS/连通性、401/403 权限、429 限流、文本与多模态请求及超时。
**本轮没有执行任何 TJU 请求，不能报告肯定可达或肯定不可达。** 若要求校园网络，应先询问服务方允许的部署网络，不能靠修改前端解决。

### 运行与容量

- 干净 Linux / Python 3.12 的安装及 PDFium 实际加载仍需验证；本机仅有 Python 3.8，WSL 枚举返回访问拒绝，未获得 Linux 实测环境。
- 本轮创建独立 venv 并尝试只用 requirements 从零安装，但 240 秒内未完成；未声称干净安装通过。现有环境 `pip check` 通过不能代替这项验证。
- 并发图片 / PDF、模型响应耗时、Cloud 内存和应用休眠后的重连，需要目标实例观察。
- 同一部署使用同一服务配置，各会话会共享该服务账号的额度；有界单轮调用不等于全站配额或滥用控制。
- 电脑、手机、iPad 的浏览器预计可用，但真实公网 WebSocket、设备网络和 Safari 行为仍需部署后测试。

## D. 当前不扩展的架构问题

### SQLite：能运行，不等于长期保存

Cloud Linux 可以在可写目录创建 SQLite。当前默认路径为 `~/.local/share/campusflow/campusflow.sqlite3`（有 XDG 配置时相应变化）。
示例将演示数据放到 `/tmp/campusflow`，明确按临时数据使用。

本地磁盘尚在时，重连同一档案可继续读取；进程重启、休眠恢复、redeploy 或实例更换后是否保留文件，不能作为保证。
Streamlit 官方明确不保证 Community Cloud 本地文件持久性，数据可能被删除。
丢失影响任务、实际进度、保存的计划、偏好、常用地点、课表和结构化草稿；匿名会话也不承诺跨会话保存。

现有 SQLite 以内部 `user_id` 分开数据、事务提交、逐档案 revision 检查并使用 5 秒连接等待。
可供小规模单实例试用，但多写入者仍有串行写锁与忙等待，不能当作无限并发存储。
本轮不换 PostgreSQL / Supabase，不改 schema，也不增加自动备份服务。

### 本地档案选择不等于公网认证

同一学号可选回同一档案，这是可信本地模式的行为。陌生用户如果知道或猜到该标识，不能靠 SQLite 的 `user_id` 字段阻止其进入同一档案。
仓库的外部认证接线 / 原型不是 Community Cloud 的自动登录配置。

第一阶段建议使用 Cloud 的“Only specific people can view this app”，先限本人 / 可信演示人员，并只使用无敏感数据。
这只限制谁能打开应用，不会自动把 Cloud 访问者映射为 CampusFlow 内部身份。
不受信任用户的档案隔离、长期存储、全站配额控制属于后续单独设计；本轮不重构这些系统。

## GitHub 推送前检查

已检查 Git 索引、可达历史的敏感文件名，以及 694 个可达文本 blob 的 key 形态模式；没有发现被跟踪的 `.env` / Secrets / SQLite 文件或该模式的 key。
这是有限静态扫描，不是“保证不存在任何秘密”的证明。

- `.env`、`.streamlit/secrets.toml`、`.venv`、数据库及 sidecar、日志已有忽略规则，不能上传真实内容。
- 当前 **50 个 `artifacts/` 文件已被 Git 跟踪**，包括三个正式 UI 验证目录中的截图与 JSON。它们会随仓库推送，不受后来添加 ignore 的影响。
- `.gitignore` 只排除了部分 artifacts 子目录，新的 `artifacts/new-debug.png` 不会被忽略。最小建议是后续补 `artifacts/`；已跟踪的 50 个文件仍需逐项决定是否保留。本轮未修改 ignore、未取消跟踪、未删除文件。
- 学号形态候选位于测试和 `scripts/profile_browser_check.cjs` 的合成 A/B 档案流程，不能把它们当作真实身份数据；推送前仍应复核二进制截图，文本扫描不能检查截图里的文字。
- 当前没有被跟踪的 Word / PDF / Excel 原件。未来真实作业、课表、申请表和截图不应放入会被 `git add` 收集的目录。
- `prototype/` 是已跟踪的历史设计资料；README 的 `docs/assets/readme/` 是有意发布的脱敏产品截图。不要用全局忽略 PNG 的方式把正式文档资源一起排除。
- Codex 临时产物、缓存、真实日志、私有诊断与凭据不属于部署所需文件；GitHub 上传前检查 staged diff 及待发布历史，而不只看当前 `.gitignore`。

## E. 推荐部署步骤

1. 审阅本轮两项启动适配、已有 README 改动，以及 artifacts 的发布范围。处理敏感文件风险后，由用户另行决定提交 / 推送。
2. 首次优先使用私有仓库或将 Cloud 应用访问设为仅指定人员；源码公开与应用访问权限是两个分别要检查的设置。
3. 在 Community Cloud 创建应用，选择仓库与实际分支，Main file path 填 `src/p2_live_main.py`，Python 明确选 3.12。
4. 将顶层 Secrets 模板填入控制台，配置 TJU 三项、`TZ` 和临时数据目录。不要配置本地 bat、systemd、Nginx 或 prototype server。
5. 查看干净构建日志。先验证页面、天津时间及日期、地图载入、设置保存、DOCX/PDF/图片上传读取，不先尝试大材料或连续模型请求。
6. 在明确授权下，从云实例做一次小型 TJU 文本请求；成功后再验证小型图片 / PDF 的完整流程。按网络、权限、模型错误分开处理。
7. 用电脑、手机、iPad 打开实际 URL，检查导航、时间线、估时、设置、重连与上传。再做一次重启后的临时数据行为观察，接受档案可能丢失后才邀请试用者。

## 本轮验证与改动

- 修改 `src/p2_live_main.py`：仓库根目录导入引导、启动时调用显式时区适配。
- 新增 `src/runtime_environment.py`、`tests/test_runtime_environment.py`、Secrets 占位模板及本文。
- caller、requirements、Windows 启动、默认档案目录、正式 CSS、地图、planner/P5、材料与 SQLite schema 均未修改。
- focused：137 passed；增加 root Secrets 到现有 caller 的接线测试后，全量 **2441 passed / 116.90s，0 failed**。
- `compileall`、`git diff --check`、现有环境 `pip check` 通过。
- 无真实配置的隔离副本：直接 console 启动先复现导入失败，修复后正式页面正常加载；三项假 Secrets 被现有入口识别，网络调用被阻断。
- 浏览器检查了 1440 正式空状态、任务估时和三 Tab 设置；390 独立会话可展开导航并进入设置，页面宽度为 390，无横向溢出，关闭按钮位于 x=327–371，宽 44px，实际点击可关闭。已有完整行动 / 时间线逻辑由回归覆盖，本轮未伪称它们在真实公网 URL 上已验收。
- `src` 的项目内导入与文件名大小写检查通过；未发现缺失模块或 Linux 大小写冲突。
- 没有完成干净 Linux 安装、真实云部署或 TJU 网络验证；这些仍是下一阶段明确验收项。

## 官方依据

- [Community Cloud 文件组织](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/file-organization)：从仓库根目录运行，随仓库获取依赖与配置。
- [部署与 Python 选择](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)：入口、Python 版本与 Secrets 设置。
- [Secrets 管理](https://docs.streamlit.io/develop/concepts/connections/secrets-management)：顶层 Secrets 可通过环境变量读取。
- [本地 SQLite 与存储边界](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data)：Community Cloud 不保证本地文件持久性。
- [应用分享与访问范围](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app)：公开 / 指定人员访问设置。
- [pypdfium2 安装说明](https://pypdfium2.readthedocs.io/en/stable/readme.html#installation)：预构建分发、PDFium 及可选 Pillow 依赖。
