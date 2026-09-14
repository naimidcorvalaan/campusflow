# CampusFlow 时空视觉语言正式迁移

2026-09-14。沿用已确认的视觉方向，仅迁入首页 mini route、Sidebar 校园纹理和现有连续时间线的节点/移动段样式。

## 正式数据链路

`p2_live_main` 将当前选定的 `map_data` 与同一个已发布 `LiveFinalTurn.turn` 传给现有 `render_page_streamlit`。新增的 [spacetime_ui.py](../src/spacetime_ui.py) 只做只读投影：

1. 从 `turn.movement_blocks` 选择尚未结束且出发时间最早的一段，保留该段的起终点 id、正式名称、出行方式、距离、预计分钟和收拾/出发/抵达时刻。
2. `MovementBlock` 没有保存路径节点。展示层使用该段的起终点和方式调用现有 `p3_route_provider.plan_route` 获取本地路径；必须是 `REAL_MAP`、可计算、距离与已发布移动段完全一致，路径节点坐标也必须齐全。
3. 通过检查后，将该路径的真实节点坐标统一投影成 SVG；背景连接来自同一校区、同一出行方式的现有 adjacency。卫津路道路侧接入节点直接使用 loader 的真实坐标，不改造节点或连接。
4. 距离与移动用时仍读取正式移动段，不在展示时再次调用估时函数。峰时/骑行规则的既有计算结果因此保持原样。
5. 地点只在去除重复校区前缀后仍能解析到相同 POI 时使用短名；否则保留正式全名。图形、文字和时刻均由同一条移动段驱动。

没有导入 prototype 的 `networks.js`、示例起终点、示例分钟或日程。没有新增地图服务、SDK、瓦片或模型调用；小地图不维护第二份计划状态。

无移动段、已结束、校区不匹配、节点不可用、无法连通或距离不一致时，不渲染地图；已有文字安排和必要的真实状态提示保持原来的行为。不会跳过失效的下一段而用更晚的路线替代。未重新规划的保存方案也不显示当前移动地图。

## 展示层改动

- 首页保留当前行动第一焦点。有路线时，mini route 在右侧，下一固定安排接在当前行动下方；窄屏按行动、移动、固定安排顺序排列。无地图时沿用原有简洁布局。
- Sidebar 从当前选择的校园 registry 读取真实节点与边，生成低对比度纹理，坐标去重只消除同位置接入点的重叠。纹理为 `aria-hidden` 装饰，不参与点击；加载失败只隐藏纹理，不阻断首次配置和导航。
- 时间线继续使用 `build_current_plan_display` 的原有条目。只给收拾、移动、抵达准备和固定安排附加视觉类，并将移动行现有的时间区间排成出发/抵达刻度。任务、真实空档、并行安排、就近食堂标记和课前准备都保留。
- 导航仍为今天、时间线、任务估时和个人设置，控件 key 与回调保持不变。没有迁入评审导航、纹理展示页、四状态 demo 或照片占位。

## 正式页面截图

截图通过 [离线验收入口](../scripts/spacetime_preview.py) 向正式流程注入有限 mock 文本响应，再点击正式的「帮我安排」。使用现有本地校园地图与正式发布结果，未手工构造展示计划，也未连接真实模型或地图 API。

[最终今天页](assets/readme/today.png) · [最终时间线](assets/readme/timeline.png) · [北洋园路线](assets/readme/beiyangyuan-route.png) · [卫津路路线](assets/readme/weijinlu-route.png)

本次正式流程实际选择的第一段路线：

| 校区 | 正式起点 → 终点 | 距离 | 预计步行 | 出发 → 抵达 |
| --- | --- | ---: | ---: | --- |
| 北洋园 | 天津大学北洋园校区郑东图书馆 → 天津大学北洋园校区第五学生食堂 | 216 m | 3 分钟 | 16:35 → 16:38 |
| 卫津路 | 图书馆 → 学三食堂 | 490 m | 7 分钟 | 16:35 → 16:42 |

北洋园由当前正式计划选择第五学生食堂，未沿用原型的竹园餐厅路线。截图为 2 倍像素密度；完整时间线增加纵向画布以展示全部条目。

## 验证

- **Focused：136 passed**。新增 40 项展示层检查，覆盖两校区步行/骑行、真实路径与等比例投影、沿用正式用时、失效回退、校区隔离、名称转义、普通导航不改变发布身份/进度/调用次数、保存方案，以及纹理不可用时的启动保护。
- **Full：2611 passed**，沿用仓库离线网络拦截。全量测试按 README 使用独立系统临时目录；部署诊断用例要求测试档案位于源码目录外。
- **浏览器：46 项通过**。覆盖要求的 1440/390 场景、真实校区控件切换后清除旧路线、地图与发布数据一致、标签边界、完整时间线、无移动布局。JS 错误 0、外部页面请求 0、横向溢出 0。
- **compileall、git diff --check 通过**。139 个受保护业务/数据文件的 SHA-256 与迁移前基线一致；既有源文件仅改 `p2_live_main.py`、`p2_main.py`、`workspace_ui.py`、`workspace.css` 四个展示文件。

本地检查明细位于 `artifacts/spacetime-migration/`：`focused.xml`、`full.xml`、`full.log`、`browser-results.json`、`source-scope.json`。

复现 focused：

```powershell
.venv\Scripts\python.exe -B -m pytest -q tests/test_spacetime_ui.py tests/test_workspace_presentation.py tests/test_p2_main.py tests/test_p2_live_main.py
```

全量、编译与 diff：

```powershell
$testRoot = Join-Path $env:TEMP ("campusflow-tests-" + [guid]::NewGuid().ToString("N"))
.venv\Scripts\python.exe -B -m pytest -q --basetemp=$testRoot
.venv\Scripts\python.exe -m compileall -q src tests scripts
git diff --check
```

浏览器阶段性脚本与截图保存在本地 ignored artifacts。公开保留的 `scripts/spacetime_preview.py` 同时是 `tests/test_spacetime_ui.py` 使用的有限响应 fixture，可复现两校区正式规划，不作为 Release 运行依赖。
