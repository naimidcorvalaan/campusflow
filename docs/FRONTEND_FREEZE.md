# CampusFlow 正式前端冻结

2026-09-14。基于 `main @ 8dcaa9fc29c1f69ce720f14cdc6cc4ef43fd64d6` 的现有未提交工作区收尾，保留已通过的时空视觉迁移、presentation cleanup 和独立原型。

**CampusFlow 前端正式冻结。除明确bug外，不建议继续整体视觉优化。下一阶段进入 v1.0.0-rc1 Windows Release。**

## 最终处理

- 今天页右下留白复用当前校区的真实路网 SVG，和 Sidebar 使用同一份节点、边投影。640 × 440px 的局部材质使用现有 `--cf-track-node`，整体透明度 0.10，椭圆 mask 向边缘淡出，没有图片框或背景信息结构。
- 无计划、有计划无移动、有真实移动的今天页都有这一层。时间线、任务估时、首次 TJU 配置不添加背景；设置抽屉仍为原来的安静工具界面。Sidebar 的纹理样式、浓度不改。
- 800px 及以下隐藏新背景层，同时取消其桌面最小高度。390px 保持内容优先，不留纹理占位。背景不接受指针事件，无 hover 或动画。
- 未加入照片、在线地图、新卡片、banner 或整页渐变。背景可删除而不影响任务操作，第一视觉仍是当前行动、90 分钟与下一固定安排。
- “本次内容未保存到个人档案”由原有非持久化/无法保存分支产生。保持状态值、身份判断和保存逻辑，仅在主内容隐藏这条泛化说明；Sidebar 底部显示“临时使用 · 不保存到档案”。个人档案模式相应显示“个人档案已启用”或“个人档案 · 本次未保存”。真正保存失败、档案读取异常仍保留原有行动提示。
- “更新变化”用于进度、位置、新情况反馈，置于首位并使用现有 CampusFlow 蓝。“重新规划”实际会重新提交整天输入，改名“重写今日安排”，降为灰色 quiet 折叠入口；无新信息再次计算的“刷新方案”原样保留。三个 handler、原生 widget key、表单身份和提交语义不变。
- 时间线的时间、修整/收拾/准备和抵达辅助文字改用已有 `--cf-ink-soft`；没有整体加深 muted，没有修改出发暖色、移动蓝色、节点或连续时间线结构。
- 字号仍是 12 / 14 / 18 / 30 / 72px，手机大数字 62px，字重 400 / 500 / 600，一个字体栈。原有中等屏幕大数字 56px 适配、SVG viewBox 标签字号保持不变。本轮没有新字号或颜色 token。

## 数据与行为保护

本轮生产修改仅 `src/p2_live_main.py`、`src/workspace_ui.py`、`src/workspace.css`。与本轮开始的源码/数据/原型 SHA-256 快照比较，其他文件逐字节未改；尤其 `src/spacetime_ui.py`、地图算法和数据完全相同。

`p2_live_main.py` AST 仅 `_render_live_page` 的展示部分变化；持久化、身份、planner、P5、progress、refs、What-if、atomic publish、材料估时和模型配置不改。`workspace_ui.py` 仅增加两个只读展示函数，原有函数 AST 不变。

浏览器用正式入口 `p2_live_main.main`，注入已有离线模型响应，读取当前真实地图生成正式计划。页面切换、折叠入口和设置开关保持已发布计划 hash 与调用计数。两校区在 1440 / 390 的 mini map HTML SHA-256 与修改前完全一致：

| 校区 | 浏览器 mock 计划中的真实路线 | 正式计划距离 / 时间 |
| --- | --- | --- |
| 北洋园 | 郑东图书馆 → 天津大学北洋园校区第五学生食堂 | 216m / 步行约 3 分钟 |
| 卫津路 | 图书馆 → 学三食堂 | 490m / 步行约 7 分钟 |

这些只是隔离验证计划，不是产品默认值，也未从 prototype 读取路线。

## 验证

| 检查 | 结果 |
| --- | --- |
| focused UI/state、身份隔离、设置、路线与 What-if | **301 passed**，28.14s |
| full，仅运行一次 | **2615 passed，0 failed**，109.61s |
| compileall src tests scripts | 通过 |
| pip check | No broken requirements found |
| git diff --check | 通过 |
| 离线 secret scan | 非 ignored 工作树文本及敏感文件名扫描；0 真实命中，8 处明确的示例/测试占位值，不读取运行凭据或扫描 Git 历史 |
| 最终浏览器 | **133 项通过**，14 个页面状态；0 横向溢出、0 JS 错误、0 外部页面请求 |

1440 实际查看无计划、无移动、两校区有路线、两校区时间线、任务估时、设置和首次配置。390 实际查看无计划、两校区有路线、独立 mini route 和两校区时间线；另补查从今天页打开/关闭设置的遮罩、按钮和计划状态。所有测试和浏览器验证都使用 mock / 本地数据，没有调用真实 TJU、DeepSeek 或地图 API。

新增三项针对性保护测试：只读身份状态渲染、移除主内容提示后状态/保存错误仍保留、两校区纹理不触碰发布状态且不出现在工具页。

机器记录保存在 ignored `artifacts/frontend-freeze/`，包括前后图片与 route hash、focused/full XML、范围审计和 secret scan。最终代码验收时未 commit / push；本次公开整理单独提交，不重做设计。

## 公开成果整理

正式代码、对应测试、三份长期设计说明与原型必要源文件进入同一笔前端冻结提交。未改业务逻辑，不重复运行已通过的 full。

公开正式截图共 **6 张**，统一存放在 `docs/assets/readme/`：

| 用途 | 最终图片 |
| --- | --- |
| README 第一张：有计划与真实校园移动 | [today.png](assets/readme/today.png) |
| README 第二张：连续时间线 | [timeline.png](assets/readme/timeline.png) |
| 任务估时结果 | [task-estimate.png](assets/readme/task-estimate.png) |
| 个人设置 | [settings.png](assets/readme/settings.png) |
| 北洋园真实路线 | [beiyangyuan-route.png](assets/readme/beiyangyuan-route.png) |
| 卫津路真实路线 | [weijinlu-route.png](assets/readme/weijinlu-route.png) |

另保留原有决策链路 SVG。迁移、cleanup、freeze 的批量图片和旧提案截图统一移至本地 ignored `artifacts/public-curation/`，不提交重复版本、截图核验 JSON 或 git-status 快照。

`prototype/spacetime-language/` 保留 DESIGN、生成代码及必要网页源文件；assets 可由当前地图再生成，previews 与一次性检查脚本不提交。`scripts/spacetime_preview.py` 被正式回归测试导入，因此作为可复用的离线 fixture 保留在源码仓库；Windows ZIP 不包含它。

冻结验收：Focused 301 passed；Full 2615 passed / 0 failed；浏览器 133 项通过；compileall、pip check、diff check、secret scan 通过。完整历史原始记录在本地 ignored artifacts 中保留。
