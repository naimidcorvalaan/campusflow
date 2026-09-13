# 首次公开仓库发布前检查

> 本文记录卫生检查结束时的状态。后续已选择“开发仓库保留完整历史、公开仓库从最终 HEAD 独立导出”的策略，提交与公开快照操作见[公开发布步骤](public_snapshot_release.md)。下文暂存区数量和未提交说明均为当时记录。

检查日期：2026-09-13。基线：`main / 263d970`。本轮仅调整忽略规则、文档资源与 Git index；未提交、推送或部署，未调用 TJU。
开始时已有的 README 重写、正式高清图片和 Cloud 最小适配全部保留。

## 结论与历史边界

当前准备发布的文件树已排除本地验收产物、环境、缓存和用户数据库。模式扫描及人工复核未发现可确认的真实密钥、手机号、学号或用户上传原件混入该文件树。
这不等于对所有历史二进制内容完成了取证：本轮检查了可达历史中的文本与文件名，当前图片做了视觉审查，未对每个历史图片版本逐像素或 OCR 审查。

**取消跟踪只影响下一次提交，不会清除旧提交。** 这 76 个旧路径仍存在于历史中；将当前分支历史推到 GitHub 后仍可检索。
如果首次公开也要求完全不带历史测试产物，需另行决定以审查后的干净文件树建立首次公开历史。本轮没有重写或删除任何历史。

## 文件处置

开始时有 418 个已跟踪文件、9 个未跟踪文件和 24,482 个已忽略文件。忽略文件主要为 Python 环境、浏览器缓存、临时 SQLite、上传测试材料和验收输出。

| 范围 | 处置 | 数量 |
| --- | --- | ---: |
| `artifacts/frontend-freeze/` | 从 index 移除，本地原件不变 | 12 |
| `artifacts/frontend-polish/` | 从 index 移除，本地原件不变 | 10 |
| `artifacts/workbench-migration/` | 从 index 移除，本地原件不变 | 28 |
| 根目录 `screenshots/` | 已被新版 README 图片替代，停止跟踪，本地保留 | 6 |
| `prototype/visual-direction/screenshots/` | 生成输出停止跟踪，本地保留 | 20 |

逐文件决策见 [publication_file_inventory.csv](publication_file_inventory.csv)，含原路径、处理原因和归档目标。76 个原文件均用 SHA-256 对比确认没有改变。

公开保留：正式源码与测试、启动脚本、依赖声明、三个校园数据文件、部署示例、项目文档、README 正式图片，以及原型的六个源文件。
四张具有设计沿革价值的原型图按原始字节复制到 [`assets/design-history/`](assets/design-history/)，明确标为历史提案；其余测试帧、失败截图和验证 JSON 不进入新的公开文件树。

## 忽略规则

`artifacts/` 整体默认忽略，替代以前仅列举部分子目录的规则。另忽略旧截图目录、原型生成截图、上传目录、临时目录、浏览器测试报告和常见开发缓存。
SQLite 的 `.sqlite`、`.sqlite3`、`.db` 及 journal / WAL / SHM 辅助文件均忽略；环境、日志和真实 Secrets 继续忽略。

保留 `.env.example`、`*.env.example` 和 `*.secrets.toml.example` 等模板的可跟踪性，`docs/assets/` 不被忽略。没有全局屏蔽 PNG、Word 或 PDF 扩展名。
上传原件应留在忽略的上传/临时目录；`.gitignore` 不能按文件内容识别随意放在其他目录的私人材料，也不能阻止显式强制添加。

## README 正式资源

| 文件 | 原始尺寸 |
| --- | --- |
| [`assets/readme/today.png`](assets/readme/today.png) | 2880×1800 PNG |
| [`assets/readme/timeline.png`](assets/readme/timeline.png) | 2880×2400 PNG |
| [`assets/readme/task-estimate.png`](assets/readme/task-estimate.png) | 2880×2080 PNG |
| [`assets/readme/settings.png`](assets/readme/settings.png) | 2880×1800 PNG |
| [`assets/readme/decision-flow.svg`](assets/readme/decision-flow.svg) | 1080×560 SVG，矢量 |

这些资源没有改图、压缩或移动；README 不依赖 artifacts 或原型服务器。旧演示文档中的截图指引已更新。
历史前端验收文档仍保留当时记录，但明确说明其原始证据仅本地留存。

## 敏感信息检查

覆盖开始时全部已跟踪/未跟踪文本、staged/unstaged diff，并扫描可达 Git 历史的 699 个文本 blob。
匹配规则包括常见 API key、PAT、JWT、Bearer、密码/密钥赋值、中国手机号、TJU 学号形状和本地绝对路径。
还在内存中将忽略的本地配置中的凭据与待发布文本、diff、历史文本做精确比对；报告不记录凭据值。

- 未发现真实凭据的精确匹配或高信号密钥格式命中；手机号形状未命中。
- 学号形状命中来自档案隔离测试和浏览器测试的合成 A/B 档案，不是用户课表或真实档案导出。
- Bearer、密码和密钥字面量来自错误脱敏测试或配置占位符，已按上下文检查并保留测试。
- 绝对路径命中为文档中的用户名占位符和 Windows 浏览器检查脚本的系统安装路径；没有发现真实用户目录值需要公开清理。
- 当前全部 73 张候选 PNG/JPEG 做了联系表视觉审查；公开保留的正式和设计图片另行检查。材料标题为通用示例，未发现可确认的真实学号、姓名、手机号或已填写私人表格。
- `.env`、`.env.dev`、用户数据库、原始材料和本地测试日志仍在忽略目录中，未删除。未发现这些敏感文件类型已被 Git 跟踪或出现在可达历史文件名中。

扫描有格式和范围限制；首次公开前仍可人工复核所保留的示例内容。没有为消除疑似命中而删除测试或改业务代码。

## Cloud 配置复核

[`deploy/streamlit-community.secrets.toml.example`](../deploy/streamlit-community.secrets.toml.example) 的三个模型值均为明确占位符；域名使用 `.invalid`。
`TZ = "Asia/Shanghai"` 和 `CAMPUSFLOW_DATA_DIR = "/tmp/campusflow"` 是 Cloud 示例配置，不含私人路径或密钥。真实值只填入平台 Secrets。

正式入口仍为 `src/p2_live_main.py`，既有适配只处理根目录导入和显式 TZ；Windows 启动脚本、requirements、`.streamlit/config.toml` 与本轮开始时完全相同。
本轮没有改变本地双击启动、模型配置读取或业务状态。
Cloud → TJU 网络可达性与 Cloud SQLite 的长期持久化仍按[可行性检查](community_cloud_readiness.md)中的边界处理。

## 验证与提交组织

发布前核验包括：待发布文件集的相对链接、PNG/SVG 解码与渲染、忽略规则正反例、敏感信息复扫、原件 SHA-256、受保护代码哈希、staged 与 unstaged 的 `git diff --check`。
实际结果：README 的 20 个链接及 5 个图片引用均有效；离线 Edge 中 5 张图片全部加载，按小于源尺寸显示，没有横向溢出。设计评审页的归档图片也全部加载。
30 个应忽略路径、10 个应可跟踪路径检查通过。最终候选公开文件集为 342 个 index 文件加 16 个未跟踪新文件，350 个文本文件及两种 diff 复扫未出现高信号密钥、真实本地凭据、手机号或真实用户绝对路径命中。
本轮开始时的所有原件仍存在；正式源码、测试、脚本、地图数据、部署配置、启动器和 requirements 的内容哈希均未被本轮改变。staged / unstaged diff 检查均通过。
以上为本地 Markdown 渲染与仓库相对链接验证，没有向 GitHub 上传，也未声称完成线上 GitHub 页面验收。
本轮没有业务变更，不将上一轮 `2441 passed` 宣称为本轮重新运行的 full 结果。

建议以后分三次提交，当前不执行：

1. 仓库卫生：忽略规则、76 个取消跟踪、设计历史归档、相关文档链接与检查清单。
2. 产品 README：既有 README 重写与 `docs/assets/readme/` 正式资源。
3. Cloud 最小兼容：既有入口适配、TZ helper、对应测试、Secrets 模板及可行性文档。

本轮暂存区只包含第 1 组的 76 个取消跟踪；其他编辑和新增文件尚未暂存，不能只提交当前 index 就当作完成发布准备。
本检查针对 Git 文件树。现有本地源码 ZIP 脚本按磁盘目录收集文件，不等价于 Git 发布树；本轮没有修改该脚本或制作公开压缩包。
