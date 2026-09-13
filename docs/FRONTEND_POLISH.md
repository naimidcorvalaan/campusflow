# 正式前端局部收尾

> 历史验收记录：下文 `artifacts/` 路径为本地验收产物，不随公开仓库发布。当前正式截图见 [README 图片](assets/readme/)，清理记录见[公开发布前检查](publication_hygiene.md)。

本轮继续保留 `main / 423bae5` 上已有未提交迁移成果，只修改展示组织、样式及界面展开状态。没有修改规划、P5、估时算法、SQLite/schema、地图、引用或原子发布，没有真实 TJU 调用，没有提交或推送。

## 变化

- 删除侧栏「校区与时间」。顶部「切换校区」「时间设置」为两个独立的轻操作，分别直接展开对应的原生控件，再次点击收起。两者可以同时展开。导航切页时收起，不清除校区或时间值。
- 环境摘要与控件标题实测留白约 37px；两个区域标题均为 14px、同色、同一基线。校园下拉框与时间控件组宽度均为 558px；原生控件高度均为 42px，字号14px、文字颜色、边框、背景一致。小时和分钟仍使用原控件与原时间规则。
- 可见名称统一为「任务估时」。大标题改为「难以估计任务时间？让 CampusFlow 帮你估」，并紧接上传与任务说明。旧内部视图标识和按钮 key 保持，避免热更新重置已选视图。
- 清除隐藏区域外层、空结果占位和纯样式标记产生的 flex 间距。原生控件一直挂载；结果、THINKING 内容出现时，容器正常显示。
- 「补充我的情况」保留轻操作，「帮我看看」保留主按钮。1440 下两个按钮左边界均为 x=262；输入框外边框同线，原生 textarea 内部为 x=263。按钮之间约 10px 间距。
- 设置保持 48% 桌面抽屉、原三个 Tab 和保存/草稿逻辑。

## 浏览器证据

运行 `scripts/offline_product_preview.py`，使用正式 renderer、mock 模型和隔离临时数据库。脚本 `scripts/frontend_polish_browser.cjs` 的实际测量和断言保存在 `artifacts/frontend-polish/browser-results.json`。

已检查 1440 的环境入口、手动时间与恢复当前时间、任务估时空状态、补充展开、DOCX 结果、个人设置三个 Tab；390 的环境控件、任务输入与补充展开、设置关闭。无横向溢出。

上传对象与「填表」补充经过环境开关、恢复当前时间和导航仍保留，没有额外模型调用。125 帧 THINKING 观察中补充输入和估时提交按钮最多各一套。估时仍由原流程生成，未越过正式任务确认门禁。

## 截图

1. `artifacts/frontend-polish/01-today-environment-1440.png`
2. `artifacts/frontend-polish/02-task-estimation-empty-1440.png`
3. `artifacts/frontend-polish/03-task-estimation-expanded-1440.png`
4. `artifacts/frontend-polish/04-settings-1440.png`

补充的窄屏和估时结果截图在同目录 `check-*.png`。所有截图使用合成数据，不作为真实模型理解能力验收。

Focused：236 passed。全量测试一次：2436 passed，0 failed（133.72 秒，使用全新系统临时目录）。`compileall -q src scripts` 和 `git diff --check` 通过。
