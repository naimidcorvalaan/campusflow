# CampusFlow 技术设计

CampusFlow 是本地运行的有状态校园时空规划 Agent 原型。正式入口为 `src/p2_live_main.py`；Windows 启动器创建独立环境，服务默认只监听 `127.0.0.1`。

## 语义与事实的分工

模型负责自然语言理解、开放式任务关系、工作量估计、候选策略及自然表达。程序负责已经确认的事实、身份、时间计算、路线、进度、约束、持久化和发布。程序不根据某个设备名决定是否允许后台运行。

```text
输入 / 澄清 / 反馈
  → Raw Extractor / Semantic Linker
  → 正式任务与固定承诺、来源、关系、稳定引用
  → Agent 候选策略
  → 确定性窗口分配与真实移动预留
  → hard constraints / dependency / progress 校验
  → 同一正式快照的呈现与原子发布
```

用户显式事实、已确认澄清优先于候选和推断。当前位置、任务地点、固定安排地点与推荐目的地分别持有。澄清绑定具体缺失字段；地点审核无法证实某次改动时，只回退对应地点槽，不撤销其他有效修订。

## 身份、进度与发布

- `task_ref` / `commitment_ref` 贯穿改名、反馈、重规划和持久化。
- planned 只代表安排；completed 来自实际反馈，remaining 延续同一任务。取消与完成不是重新生成候选可以覆盖的事实。
- What-if 在隔离状态中计算，采用前核对原状态版本；采用已经展示的候选，不暗中重新规划。
- 候选通过正式校验、持久事务成功后才原子替换当前方案。失败保留旧合法计划。
- SQLite 保存本地档案、偏好、课表、任务及执行状态；模型服务配置独立保存在本地，密钥不属于业务快照。

## 时间、移动与注意力

`data/beiyangyuan_map.json` 与 `data/weijinlu_map.json` 提供本地路网。程序计算路线、距离、出行时间；运行时没有实时交通查询。两校区独立规划。

课程、deadline、用户明确顺序、不可拆任务、课前准备与用餐硬时间共同约束可用窗口。默认当天边界为24:00，用户明确更早结束时沿用用户边界。还有可行窗口时继续尝试安排剩余工作；容量不足保留真实未安排量，不能用解释代替时间线。

同一地点任务可在离开前和真实返程后继续；路线推导每次停留区间，不能把“离开前结束”和“返回后开始”同时当作整项任务的全局边界。后台过程发生地点不等于人的当前位置。

### 通用后台过程

任务仍沿用现有身份与进度模型，以 `attention_mode` 区分主动操作与自主运行，并用正式启动引用、完成依赖和重叠关系连接动作。模型解释自主运行的语义，程序验证引用存在、启动完成、时间依赖和注意力互斥。

后台运行可跨越前台窗口，不独占人的全部注意力；两个主动任务不能因此非法重叠。离开与返回必须有真实路线。用户报告过程正在运行或剩余时间时更新原身份，不重复启动，也不将运行中误写成已完成。当前行动优先展示人的前台动作，完整计划表显示后台区间。

已有任务与固定安排的授权并行仍保留，授权范围、地点、窗口及工作量分别校验。

## 材料识别与估时

Recognition 负责任务身份、scope、requirements、数量与来源；独立 Estimation 负责分钟、区间、工作量 ledger 与调整 policy。

```text
共享正式字段声明
  ├─ stage-owned projection → nullable object anyOf → annotation stripping → tool schema
  └─ 共享字段 / enum / policy 语义 → prompt-side guide

tool arguments → 标准 JSON → 正式 parser / validators
               → actionability / evidence / source support
               → 独立估时与算术、policy、provenance 检查
```

TJU Recognition 使用 `tool_choice=auto`，严格核对唯一目标调用。兼容层只改变 schema 的等价表达；不把字符串猜成对象，不自动纠正非法 enum，不把 unknown support 当作支持。normal 与 repair 共用同一声明；历史 root/item estimate 仍兼容读取，但 Recognition 不负责生成。

工具缺失与语义失败使用有界恢复，旧 content 路径保留兼容能力，不通过反复切换通道绕过正式校验。说明见 [Function Calling Recognition](docs/function-calling-recognition.md)。

文件入口集中限制：图片5MB，DOCX/PDF 10MB、PDF20页、提取文字及材料补充12,000字符。超限拒绝并说明，不静默截断。估时保留用户明确分钟、AI建议与手动采用的来源区别。

## 表达与运行边界

时间线与行动卡消费同一正式快照。模型文案的时间声明可绑定到不含实际钟点的事件角色，由程序比较正式时刻；无效绑定仅降级相关字段，继续遵守调用预算。自由措辞仍可能出现地点表述不准，技术校验不能代替产品审查。

模型配置通过首次配置页或环境变量提供，公开代码不包含项目专属端点或账号。可选服务与视觉能力按 provider 路径处理。[本地运行](docs/local_quickstart.md) · [模型服务](docs/llm_providers.md) · [验证摘要](docs/validation.md)
