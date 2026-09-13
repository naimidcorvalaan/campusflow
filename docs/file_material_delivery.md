# Word 与 PDF 任务材料支持

起点：main / 1b3005ffafd460027677218c95d5f91d34f2e119，工作区干净。
仅扩展统一任务入口内部和材料来源；首页品牌、布局、个人设置、规划与地图保持不变。

## 用户路径

展开“难以估计任务时间？让campusflow帮你估”，上传图片、DOCX或PDF，点“帮我看看”。
文件先成为现有短摘要草稿，可修改采用分钟、勾选事项，再确认加入；多事项仍为同一原子批次。
估时、已有任务补充、实际进度与stable task_ref沿用原有逻辑。

DOCX读取正文标题、段落、列表项和简单表格的基本顺序，不恢复精确排版/列表编号。
图片、公式、文本框、嵌入对象、页眉页脚等不承诺完整读取，会提示限制；几乎无文字时拒绝空估算。
不支持DOC或DOCM，不访问外部关系，不执行VBA/OLE。

PDF按页判断：至少40个有效字符、正常字符比例与文本多样性达标，且无明显图像区域时走文字；
稀疏文字、页码/水印或明显图像页进入视觉候选。小于页面10%的装饰图片不自动触发视觉。
混合文件保留可读取文字并附必要图像页，同一消息中按页序传入，未选页面明确不在分析范围。
这是保守的启发式，不是排版理解或OCR；复杂公式、图表、特殊文字编码仍需用户核对。

## 集中限制

定义位于 `src/file_material.py`：

| 范围 | 限制 |
| --- | --- |
| DOCX / PDF文件 | 各10MB |
| PDF总页数 | 1–20页；更长需先拆分 |
| 每次视觉页 | 最多5页；超出要求选页 |
| 文字加用户材料补充 | 最多12000字符；不静默截断 |
| 页面渲染 | 目标140DPI，最长边1800px |
| 本次全部页面PNG | 合计最多5MB，Base64后约增加三分之一 |
| DOCX ZIP | 最多1000成员、展开合计32MB、单个必要XML4MB、压缩比200 |

页码支持单页、范围与逗号组合，拒绝重复、越界和倒置。空白页跳过；完全空文件拒绝。
页数不是任务分钟数；通知相对日期不采用PDF/Word文件创建时间。

## 技术与隐私边界

- DOCX只用标准库ZIP和defusedxml读取两个必要XML，不解压到磁盘；拒绝DTD/外部实体、路径穿越与异常容器。
- PDFium无表单初始化、JS/XFA或链接访问；所有PDFium调用由进程内锁串行保护，锁中无用户数据。
- 文件、提取全文与PNG仅存在当前处理内存；模型消息只走现有正式客户端，无图床，无新供应商。
- 持久化只新增source_type与文件/页码指纹等元数据；MaterialDraft.original_text保存用户主动补充，不保存提取全文。
- 使用临时含全文对象执行证据校验，返回草稿前恢复用户补充；解析/repair失败也不会把全文写入快照。
- 结构化任务范围和短原文依据属于用户档案，仍按user_id隔离。匿名不产生永久材料记录。
- SQL schema保持v5；新字段有默认值，旧文字/图片草稿兼容。文件丢失后提示重新选择，不影响已确认任务。
- 不记录全文、文件bytes、图片Base64、prompt或密钥。异常对用户只返回固定安全说明。
- 这是本地有界解析，不是针对任意恶意PDF的操作系统安全沙箱；不运行不可信嵌入代码。

## 模型调用

| 操作 | 材料理解调用 |
| --- | --- |
| 上传、选页、浏览、编辑、恢复 | 0 |
| 帮我看看：DOCX/文字PDF | 1次现有文字调用 |
| 帮我看看：扫描/混合PDF | 1次现有messages客户端调用，包含有限多图 |
| 格式repair | 最多额外1次文字调用，不重新逐页发图 |
| 用户明确重新估算 | 复用既有单项估时 |
| 确认加入 | 不重读材料，保留原有规划/表达重建调用 |

离线浏览器样例：整理1次，确认后总计8次mock角色调用（其余7次为既有规划/表达链），没有声称确认整体零模型调用。
本轮真实TJU调用为0。单图接口此前已由用户真实验收；多页PDF合并消息的服务接受情况及真实理解质量尚待正式网页验收。

## 依赖选择

查询日期：2026-09-08。

- 评估的 [PyMuPDF 1.24.11](https://pypi.org/project/PyMuPDF/1.24.11/) 支持Python3.8，但采用AGPL/商业双许可。
- 选择 [pypdfium2 5.13.0](https://pypi.org/project/pypdfium2/5.13.0/)：预编译Windows x64 wheel约3.9MB，含PDFium，无强制额外运行依赖；使用项目已有Pillow输出PNG。许可为Apache/BSD及附带第三方条款，分发需保留包内许可证。
- [defusedxml 0.7.1](https://pypi.org/project/defusedxml/0.7.1/) wheel约25KB，无Word编辑/Office依赖。
- [PDFium线程限制](https://pypdfium2.readthedocs.io/en/stable/python_api.html#incompatibility-with-threading)要求串行调用，已使用锁保护。
- 未升级Python、Streamlit或原四个固定依赖；不需用户安装Poppler/Ghostscript。

## 验证记录

开发fixture全部运行时生成，不提交DOCX/PDF二进制。`tests/document_fixtures.py`提供空/损坏/文本/扫描/混合等有限样例。
`scripts/document_material_preview.py`提供有限mock响应；正式页面入口通过`offline_product_preview.py?documents=1`运行，网络强制禁用。
`scripts/document_browser_check.cjs`实际走上传、单次整理、手改分钟、多项取消勾选与确认；截图和测量在忽略目录`artifacts/document_material/`。

- focused：145 passed；full：2321 passed（101.64秒），全量之后仅收紧上传器文案选择器作用域及更新文档/浏览器检查脚本，未改业务链；最终UI focused 86 passed。
- compileall（src/tests/scripts）、git diff --check通过；当前环境和全新临时Python3.8.10环境pip check均通过。
- 当前环境新增两包约8秒，下载约3.9MB+25KB，安装目录合计约7.9MB（含缓存文件）。原固定依赖未升级。
- 独立临时venv整份requirements真实联网安装成功，启动器dependencies_ready=True，实际渲染2页扫描PDF。最初受限执行中的pip等待已中断；获准的联网执行边界下安装完成，不能据最初无输出断言库不兼容。
- 真实Edge/Streamlit：DOCX、文字PDF、2页扫描PDF、混合PDF均完成上传→单次整理→确认；6页扫描PDF要求选页，选1–2后完成；DOC有明确不支持提示且0调用。
- DOCX手改80分钟后确认；文字PDF拆2项并取消第2项后确认；上传、编辑、勾选均未重复理解。
- 1440×900与390×844无横向溢出；实际查看了上传区、短摘要、渲染页与窄屏页面。
- 重开已保存文件草稿：结构化事项恢复、原文件缺失提示可见、上传原件为空；A/B隔离与失败回滚另有临时SQLite回归。
- 无真实TJU请求，没有修改真实个人数据库，没有提交/推送。

## 本轮修改范围

- 本地读取：新增 `src/file_material.py`。
- 现有链路接线：`src/material_inbox.py`、`src/material_ui.py`、`src/p2_tju_live_adapter.py`。
- 上传器中文名称：`src/campusflow_ui.py`，仅新增文档上传器的选择器分支，原图片上传器及品牌/抽屉样式保留。
- 依赖与说明：`requirements.txt`、`.gitignore`、`README.md`、本文件。
- 离线走查：`scripts/offline_product_preview.py`、新增 `scripts/document_material_preview.py` 和 `scripts/document_browser_check.cjs`。
- 回归与动态fixture：新增 `tests/document_fixtures.py`、`tests/test_file_material.py`。
- 截图/fixture在忽略目录，不作为Release二进制提交。

建议提交标题：`材料：支持 Word/PDF 任务理解与有界多页估时`。
正文：本地读取DOCX和PDF，统一进入现有材料草稿与原子确认；限制文件、文字与视觉页数；
原件及提取全文不入库；新增离线回归、浏览器证据和Python3.8依赖验证。
