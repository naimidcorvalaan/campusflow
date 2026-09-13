# 正式工作台视觉收尾 · 2026-09-12

> 历史验收记录：下文 `artifacts/` 路径为本地验收产物，不随公开仓库发布。当前正式截图见 [README 图片](assets/readme/)，清理记录见[公开发布前检查](publication_hygiene.md)。

后续环境入口、任务估时标题与对齐的局部调整见 [FRONTEND_POLISH.md](FRONTEND_POLISH.md)。

本轮基于已通过的正式迁移工作区继续，没有重做设计。分支 `main`，HEAD `423bae5`。开始时已有的未提交展示层修改、原型与验收材料全部保留，未提交或推送。

## 最终呈现

- 环境缩为「校区 · 日期 时间」和轻量的「切换 / 时间设置」。详细控件仍使用原 key、默认北洋园、原时间覆盖规则；进入其他页面时只收起环境区域，控件始终挂载。
- 材料估时导航直接显示原工具内容，取消外层 accordion。结果仍按原流程收起来源输入，可通过「更换材料」展开。上传对象、补充说明、估时处理和正式确认门禁未重写。
- 上传区用原生 dropzone 自己的点击、Enter、拖放和文件 input。Streamlit 1.31 无公开微文案参数，因此通过稳定 data-testid 隐藏整块英文说明，以 CSS 呈现中文；没有替换 React 文本节点、复制 input、改 widget key 或修改依赖包。原生中文字段标签仍提供可访问名称。文件类型与大小校验不变。
- 桌面隐藏原生 sidebar 顶部关闭按钮，窄屏保留。设置抽屉关闭按钮不受该规则影响。
- 无计划首页的位置输入放入「补充当前位置（可选）」。已有计划时，「重新规划」本身已折叠，不再嵌套展开区。
- 设置保留 48% 桌面宽度、三个 Tab、grouped settings、44px 关闭区域、保存逻辑和草稿隔离。

## 渲染保护

浏览器发现并修正了 Streamlit 不支持嵌套 expander 的限制。位置输入的标签、key 和原处理函数保持不变。

「使用当前时间」的刷新改为沿用已存在的延后 rerun 机制，先挂载输入，再执行原时间重置。这避免提前返回造成原生控件清理；没有修改时间计算或规划业务。

## 验证

使用 `scripts/offline_product_preview.py` 运行正式 Streamlit renderer；mock 模型、隔离临时数据库、禁止 requests 外部请求，不依赖 prototype server。

- focused：235 passed。
- `compileall -q src scripts` 与 `git diff --check` 均通过。
- 最终全量：2435 passed，0 failed，119.14 秒。使用全新系统临时目录。此前全量在浏览器修正前通过 2433 项，修正后补上两项渲染契约回归并再次跑全量。
- 1440：空白首页、首次规划、当前行动与真实规划路线、环境切换、手动时间与恢复、直接材料输入、鼠标/键盘/拖放上传、估时结果、来源展开、部分覆盖、设置三个 Tab。
- 390：当前行动、连续时间线、材料输入和结果、设置及可达关闭按钮，无横向溢出。截图等待 sidebar 动画结束。
- 切页、开关设置没有模型调用；计划指纹不变。材料上传字节数与「填表」补充保留。估时没有越过正式确认门禁。
- THINKING 采样中补充输入与估时提交按钮均最多一套。详情在 `artifacts/frontend-freeze/browser-results.json`。

## 最终桌面截图

1. `artifacts/frontend-freeze/01-today-empty-1440.png`
2. `artifacts/frontend-freeze/02-today-planned-1440.png`
3. `artifacts/frontend-freeze/03-material-1440.png`
4. `artifacts/frontend-freeze/04-settings-1440.png`

材料空状态与窄屏补充观察保存在同目录的 `check-*.png`。全部来自正式 renderer 的合成数据演示，并非真实模型理解能力验收。

本轮不再提出新视觉方向，保留已通过的样式系统。未修改 planner、P5、材料处理算法、地图、SQLite schema、refs、progress 或原子发布。
