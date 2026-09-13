# CampusFlow P1a 技术模型设计

## 0. 文档目的与边界

本文记录 P1a 阶段的架构审查和推荐数据模型，不实现 P1 功能。结论来自当前仓库的 P0 代码和相关测试。

P1 首先解决一个单窗口问题：用户刚下课或临时有一段空闲时间，只说一两句话，系统就结合当前位置、下一项固定安排、候选任务和校园步行时间，给出一个首选方案和一个简短备选。

本文遵守以下边界：

- 保留 P0 已通过验收的管线，不建议一次性重写。
- 不枚举“吃饭、实验、发邮件”等现实任务类型；任务名称继续使用自由文本。
- 大模型负责理解和建议，程序负责严格解析、状态变更和关键事实验证。
- AI 推测始终标记为“AI 暂估”，不能冒充用户确认或系统事实。
- 不在 P1a 引入账号、数据库、真实课表或多设备同步。
- 不把当前虚构演示地图的时间描述为真实北洋园步行时间。

---

## A. 现有数据流概述

### A.1 初始规划

当前初始规划的数据流是：

```text
用户输入自然语言 + Python 提供的当前时间
→ prompt_builder 构造严格的 system/user 消息
→ TJU LLM 返回 JSON
→ natural_language_parser 注入受信任的当前时间
→ StructuredParser 严格检查 JSON 字段、类型和缺失信息
→ InputValidator 校验正式 PlanningRequest
→ TaskSelector 枚举可选任务子集
→ 路线规划和 deadline 检查
→ ResultFormatter 生成确定性中文文本
→ main.py 将有效请求和展示文本写入 Streamlit session
```

`StructuredParser` 只允许固定顶层字段和固定任务字段。字段缺失时返回 `needs_clarification`；字段多出、类型错误或值不合法时拒绝。解析成功后，`ExtractedPlanningRequest` 才会转换成正式 `PlanningRequest`。

`TaskSelector` 保留全部必须任务，枚举可选任务组合，并复用路线规划和日程检查。成功结果包含：

- `kept_tasks`：实际进入路线方案的任务；
- `dropped_tasks`：为满足 deadline 而未进入方案的可选任务；
- `route_plan`：访问顺序和步行/停留时间；
- `schedule_result`：每项任务的到达、完成和 deadline 结果。

### A.2 动态重规划

当前动态重规划的数据流是：

```text
session 中的当前 PlanningRequest + 用户变化文本
→ replanning_prompt_builder 构造可信摘要和变化提取提示词
→ TJU LLM 返回 ReplanningUpdate JSON
→ replanning_parser 严格解析
→ DynamicReplanner 应用位置、延误、取消和新增
→ TaskSelector 再次选择任务并规划路线
→ ResultFormatter 生成确定性中文文本
→ main.py 保存下一轮有效请求和展示历史
```

动态更新目前只支持：新当前位置、延误分钟、按地点取消任务、增加任务。它不自动识别完成，不记录部分完成，也不支持跨天。

### A.3 session 写回的关键不变量

初始规划和动态规划都会构造一个“下一轮有效请求”。有任务选择结果时，下一轮任务必须来自：

```python
task_selection_result.kept_tasks
```

动态规划尤其不能直接保存：

```python
replanning_result.updated_request.tasks
```

原因是 `updated_request.tasks` 是“应用取消和新增之后、任务选择之前”的候选集合，其中仍可能包含本轮已经被选择器放弃的任务。当前页面测试明确锁定了“只保存 `kept_tasks`”的行为。

P1 必须继续保护这个 P0 不变量。P1 如果需要保留“本窗口未安排但以后还可做”的任务，应把它们保存在独立的 P1 任务账本中，而不是重新塞回 P0 的下一轮 `PlanningRequest.tasks`。

### A.4 P1a 可直接复用的部分

可以复用的能力包括：

- system/user 提示词分离；
- 把模型输出当作不可信输入的严格解析边界；
- 缺失信息和拒绝结果的统一状态；
- 受信任时间由程序注入，而不是接受模型改写；
- 校园地点解析、最短路径和路线时间计算；
- deadline/结束时间的程序校验思路；
- 安全、确定性的中文展示；
- 页面依赖可替换、API 可 mock 的测试方式；
- session 中只在完整成功结果产生后更新当前计划；
- P0 `kept_tasks` 写回规则。

需要复用的是这些边界和算法，不是把 P0 `Task` 强行承担全部 P1 语义。路线算法“正确算完”只说明它按输入数据完成了计算，不证明输入数据代表真实校园；P1 必须在算法结果之外携带数据集可信状态。

---

## B. 现有模型与 P1 需求之间的差距

### B.1 代码事实对照

| 待检查假设 | 当前代码结论 | 依据与影响 |
| --- | --- | --- |
| 每个任务都必须有地点 | 是 | `Task.location` 是必填字符串；解析器把空地点列为缺失；校验器要求非空；路线器必须把地点解析到地图，并拒绝同一地点的多个任务。地点无关的学习任务无法自然表达。 |
| 每个任务都必须一次完成 | 是 | 路线器会一次性把全部 `estimated_duration_minutes` 加入停留时间；模型没有总量、已完成量、剩余量、本次投入量或最小有效片段。 |
| 任务只有保留、放弃和取消 | 基本是 | 选择结果只有 `kept_tasks`/`dropped_tasks`；动态更新只有取消和新增，没有跳过、推迟、永久放弃、完成和部分完成。`is_mandatory` 是优先属性，不是生命周期状态。 |
| 固定日程期间完全不能执行任务 | 当前无法表达，效果上按串行占用处理 | P0 没有固定安排或可用程度模型。路线和任务停留按单一时间轴串行推进；无法表达“上课期间低注意力可用”或并行执行原地任务。 |
| 所有字段都被视为同等可信 | 数据模型和展示层中是 | `Task`/`PlanningRequest` 没有来源元数据。当前时间虽由程序注入，路线时间也由程序计算，但进入对象或页面后没有统一来源标签；任务时长和路线时间无法以不同可信度展示。 |
| session 是唯一状态来源 | 对当前 P0 页面是 | `current_planning_request` 是后续动态请求的当前计划来源，另有展示文本和最多 10 条变化历史；没有独立任务账本、持久化仓库或外部状态源。P1a 不需要立即增加数据库，但模型不应绑定 Streamlit session。 |
| 当前窗口未选择等于永久放弃 | P0 没有窗口语义，实际效果接近“从当前 session 消失” | `dropped_tasks` 不进入下一轮有效请求。虽然格式化文案称“暂时放弃”，但后续动态规划不会再看到它，除非用户重新新增。P1 不能沿用这一语义。 |

### B.2 其他重要差距

1. **没有稳定任务标识。** 当前动态取消按地点匹配，而且路线层拒绝同一地点多个任务。P1 中“在图书馆看书”和“在图书馆写实验”必须能分别更新，因此需要 `task_id`。
2. **`destination` 不是固定安排。** 它只有地点，没有安排标题、开始时间、可用程度或来源，不能表示“11:30 在 46 教学楼上课”。
3. **只有任务 deadline，没有窗口结束锚点和缓冲。** P1 需要先到达下一安排地点，并明确展示预留缓冲。
4. **必需信息策略负担较高。** P0 提示词禁止估算，缺少任务必选性或时长就追问。P1 则要允许先做明确标注的 AI 暂估。
5. **选择策略不适合作为 P1 推荐大脑。** P0 选择器优先保留更多可选任务，再比较总耗时；它不理解用户意愿、生活需求、精力或主观偏好。P1 应由大模型提出候选，程序验证关键事实，而不是继续扩展枚举评分规则。
6. **没有首选/备选层。** 当前只有一个任务子集和一条路线。
7. **没有假设、风险和验证状态。** 页面无法分别展示“用户说的”“系统算的”“AI 猜的”“尚未验证的”。
8. **无显式危险覆盖。** P0 能显示不可行，但没有“程序劝阻—用户确认—仍允许执行”的执行确认模型。
9. **没有区分路线计算状态和数据真实性。** 当前演示路线来自 `tests/fixtures/sample_campus_map.json` 的虚构测试图；`data/beiyangyuan_locations.json` 只有真实地点目录，没有步行 edges 和真实步行时间。P1 不能因算法计算成功就把测试或暂估数据称为真实校园路线。

### B.3 现有设计中应保留的优点

`Task.description` 本身是自由文本，并没有封闭的现实任务类型枚举。这是 P1 支持任意个性化任务的重要基础。P1 应增加通用约束和状态，而不是增加 `EAT`、`HOMEWORK`、`PARCEL` 等具体任务类型。

---

## C. 推荐的数据模型

以下类定义是用于说明字段关系的**概念性伪代码**，不是待直接复制到 `src/models.py` 的实现代码，名称也可在实现切片中调整。

> **Python 3.8.10 兼容性是实现硬约束。** 文中为便于阅读保留了少量 `str | None`、`tuple[str, ...]`、`dict[str, ...]` 等现代简写，它们不能直接复制到当前项目。正式实现必须优先遵循仓库现有风格，使用已经确认可在 Python 3.8.10 下运行的 `Optional`、`List`、`Tuple`、`Dict` 等写法；不得为了类型提示升级 Python。新增测试也必须使用项目的 Python 3.8.10 虚拟环境执行。

### C.1 设计原则

- 使用稳定 ID 连接任务、窗口决策、候选方案和反馈。
- 任务保存长期事实；窗口结果保存“这一次怎么安排”。
- 用户原始信息、来源元数据、AI 建议、程序计算结果和底层数据可信状态分层保存。
- 每个任务都有地点要求三态，但不要求每个任务都有具体地点。
- 只枚举有限的通用约束，例如注意力级别和时间段可用程度；不枚举现实任务名称。
- 规划结果不得反向覆盖用户原始信息或已经确认的任务事实。

### C.2 来源元数据

```python
class SourceKind(str, Enum):
    USER_STATED = "user_stated"          # 用户本次明确说出
    USER_CONFIRMED = "user_confirmed"    # 用户确认过默认值或 AI 暂估
    SYSTEM_DEFAULT = "system_default"    # CampusFlow 默认策略
    SYSTEM_VERIFIED = "system_verified"  # 程序核验的一般事实；不单独代表路线底层数据真实
    AI_EXTRACTED_FROM_USER_TEXT = "ai_extracted_from_user_text"  # 模型从用户原话识别的结构化属性
    AI_ESTIMATED = "ai_estimated"        # 大模型暂估

@dataclass(frozen=True)
class FieldEvidence:
    source: SourceKind
    explanation: str | None = None
    confirmed_at: datetime | None = None
```

`USER_STATED` 只由程序为经过验证的用户原文事实建立。`AI_EXTRACTED_FROM_USER_TEXT` 表示模型从用户原话识别出结构化属性；`AI_ESTIMATED` 表示用户没有明确给出、模型主动暂估。首切片中模型只能输出后两种来源，且二者的 `explanation` 都必填，例如“用户明确说需要背半小时单词”或“用户未提供用时，按课程实验任务暂估”。不要使用看似精确的百分比置信度。

### C.3 任意个性化任务及通用特征

```python
class AttentionLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class TaskImportance(str, Enum):
    MUST_DO = "must_do"
    PREFERRED = "preferred"
    UNSPECIFIED = "unspecified"

class TaskLifecycle(str, Enum):
    ACTIVE = "active"
    DEFERRED = "deferred"
    ABANDONED = "abandoned"
    CANCELLED = "cancelled"
    COMPLETED = "completed"

class LocationRequirement(str, Enum):
    NO_SPECIFIC_LOCATION = "no_specific_location"
    SPECIFIC_LOCATION = "specific_location"
    LOCATION_REQUIREMENT_UNKNOWN = "location_requirement_unknown"

@dataclass
class LocationConstraint:
    requirement: LocationRequirement
    location_text: str | None
    location_id: str | None

@dataclass
class TaskProgress:
    estimated_total_minutes: int | None
    completed_minutes: int = 0

    # 只读派生属性，不是可独立写入或反序列化的状态字段。
    @property
    def remaining_minutes(self):
        if self.estimated_total_minutes is None:
            return None
        return max(self.estimated_total_minutes - self.completed_minutes, 0)

@dataclass
class P1Task:
    task_id: str
    title: str                         # 自由文本，不做现实任务类型枚举
    original_text: str                 # 保留用户原始表达
    importance: TaskImportance         # 必须/希望/未说明；不是现实任务类型
    deadline: datetime | None
    location: LocationConstraint         # 始终存在，用 requirement 区分三种地点情况
    environment_requirements: tuple[str, ...]
    equipment_requirements: tuple[str, ...]
    is_splittable: bool | None
    minimum_slice_minutes: int | None
    attention_required: AttentionLevel | None
    interruption_allowed: bool | None
    may_have_open_hours: bool | None
    progress: TaskProgress
    lifecycle: TaskLifecycle
    evidence: dict[str, FieldEvidence]  # 只覆盖决策关键字段
    needs_confirmation: tuple[str, ...]
```

说明：

- `title` 和 `original_text` 允许“完成计组实验3”“给导师发表格”等任意内容。
- `environment_requirements` 和 `equipment_requirements` 是自由文本约束，例如“电脑”“安静环境”“实验设备”，不是任务类型。
- `LocationRequirement` 只描述通用地点约束，不是现实任务类型枚举。三种状态不能用 `location=None` 混在一起：
  - `NO_SPECIFIC_LOCATION`：已明确任务不要求特定地点，例如“背单词”；`location_text` 和 `location_id` 均为空。
  - `SPECIFIC_LOCATION`：任务需要特定地点。“去北区菜鸟驿站取快递”可同时有地点文本和系统解析后的 ID；“找老师签字”可以只有“老师办公室”这一待确认文本，具体地图节点仍为空。
  - `LOCATION_REQUIREMENT_UNKNOWN`：目前连是否需要特定地点都不确定，例如“完成计组实验3”可能需要机房，也可能可在个人电脑上完成；不得静默按地点不限处理。
- AI 可以暂估 `location.requirement`，但必须在 `evidence` 中标记 `AI_ESTIMATED` 并说明理由。需要特定地点却缺少可解析地点时不能虚构地图节点；地点要求仍不确定时可以给暂定建议和快捷确认，但不能生成完整正式路线结论。
- 所有重要的非空任务属性必须有来源：`location_requirement` 始终如此；非空 `location_text`、环境/设备数组、时长、最小片段、注意力和布尔值（包括 `false`）也必须如此。`null` 属性和空数组不得声明来源。属性可标记为 `AI_EXTRACTED_FROM_USER_TEXT` 或 `AI_ESTIMATED`，两者都要有中文解释。
- `estimated_total_minutes`、`is_splittable`、`minimum_slice_minutes`、注意力和可中断性都可由大模型暂估，但必须在 `evidence` 中标记 `AI_ESTIMATED`。
- `field_evidence` 回答“这个值从哪里来”；`needs_confirmation` 只回答“这个值现在是否值得重点询问”。二者不能混用：并非每个 AI 暂估都要追问，未来的一键确认可扫描 `AI_ESTIMATED`，不依赖 `needs_confirmation`。
- 若 `is_splittable=false`，已知总时长时 `minimum_slice_minutes` 必须等于 `estimated_total_minutes`；未知总时长时最小片段必须为 `null`。可拆分任务的已知最小片段不得超过已知总时长。
- `TaskProgress` 只保存 `estimated_total_minutes` 和 `completed_minutes` 两个事实字段。`remaining_minutes` 是只读派生属性：总时长已知时由程序计算 `max(total - completed, 0)`，总时长未知时它也为未知；序列化或展示可以输出该值，但反序列化和状态更新不能接受它作为独立输入。
- 用户反馈只能增加或纠正 `completed_minutes`；只有用户明确说明或确认新的总时长时才可更新 `estimated_total_minutes`。规划结果不能回写总时长，AI 也不能猜用户实际完成分钟。
- 新任务的 `completed_minutes=0` 可以是系统默认；部分完成分钟必须来自用户反馈或明确的执行记录。
- `needs_confirmation` 只记录会明显影响当前建议的问题，避免生成完整表单。

### C.4 固定安排、单个窗口和可用程度

```python
class AvailabilityLevel(str, Enum):
    UNAVAILABLE = "unavailable"
    LOW_ATTENTION = "low_attention"
    FULLY_AVAILABLE = "fully_available"

@dataclass
class FixedCommitment:
    commitment_id: str
    title: str
    starts_at: datetime | None
    ends_at: datetime | None
    location_text: str | None
    location_id: str | None
    availability_during: AvailabilityLevel
    evidence: dict[str, FieldEvidence]

@dataclass
class AvailabilitySegment:
    starts_at: datetime
    ends_at: datetime
    level: AvailabilityLevel
    fixed_commitment_id: str | None = None

@dataclass
class PlanningWindow:
    window_id: str
    starts_at: datetime
    start_location_text: str | None
    start_location_id: str | None
    ends_at: datetime | None
    end_location_text: str | None
    end_location_id: str | None
    next_commitment: FixedCommitment | None
    availability_segments: tuple[AvailabilitySegment, ...]
    required_buffer_minutes: int
    evidence: dict[str, FieldEvidence]
```

P1 通常创建一个“现在到下一项固定安排开始前”的窗口。`ends_at` 和 `end_location` 可由下一安排的开始时间和地点提供。保留 `AvailabilitySegment` 是为了表达三种可用程度，并为以后多个时间段留出演进空间；P1 页面仍只规划一个窗口，不需要一次规划完整一天。

规则：

- `UNAVAILABLE` 不安排任务。
- `LOW_ATTENTION` 只允许不会要求用户离开当前地点的任务：`NO_SPECIFIC_LOCATION` 可以安排；`SPECIFIC_LOCATION` 只有在已解析地点就是当前地点时才可以安排；`LOCATION_REQUIREMENT_UNKNOWN` 不能静默按地点不限处理，须先确认或只给不进入正式方案的暂定建议。高注意力任务可以作为带效率警告的候选，不能被程序静默描述为高效或无风险。
- `FULLY_AVAILABLE` 可安排符合地点、时间和设备约束的任务。
- 用户没有说明固定安排中能否做其他任务时，当前实现切片采用保守的 `UNAVAILABLE` 系统默认，并给出快捷确认；不得把默认值说成用户选择。

### C.5 本次窗口的任务决策和执行反馈

长期任务状态与当前窗口结果必须拆开：

```python
class WindowDisposition(str, Enum):
    PLANNED = "planned"
    NOT_SCHEDULED = "not_scheduled"
    SKIPPED_BY_USER = "skipped_by_user"

class WindowExecution(str, Enum):
    NOT_STARTED = "not_started"
    PARTIALLY_COMPLETED = "partially_completed"
    COMPLETED = "completed"

@dataclass
class WindowTaskRecord:
    window_id: str
    task_id: str
    disposition: WindowDisposition
    execution: WindowExecution
    planned_minutes: int
    completed_minutes_this_window: int
    reason: str | None
```

`NOT_SCHEDULED` 表示算法或候选方案本窗口没有选它；`SKIPPED_BY_USER` 表示用户只在本窗口不想做。两者都不能自动把长期任务改成 `ABANDONED` 或 `CANCELLED`。

### C.6 大模型候选、程序计算与路线数据可信度

应区分两层对象：

```python
@dataclass(frozen=True)
class ProposedStep:
    task_id: str
    proposed_minutes: int
    proposed_location_text: str | None

@dataclass(frozen=True)
class ModelPlanCandidate:
    candidate_id: str
    steps: tuple[ProposedStep, ...]
    rationale: str
    assumption_ids: tuple[str, ...]
    model_warnings: tuple[str, ...]

class PlanComputationStatus(str, Enum):
    NOT_COMPUTED = "not_computed"
    COMPUTED_FEASIBLE = "computed_feasible"
    COMPUTED_INFEASIBLE = "computed_infeasible"
    COMPUTATION_INCOMPLETE = "computation_incomplete"
    COMPUTATION_FAILED = "computation_failed"

class RouteDataTrust(str, Enum):
    SYNTHETIC_TEST = "synthetic_test"
    ESTIMATED_UNVERIFIED = "estimated_unverified"
    REAL_VERIFIED = "real_verified"

@dataclass(frozen=True)
class RouteDataProvenance:
    trust: RouteDataTrust
    dataset_name: str
    has_walk_edges: bool
    explanation: str

@dataclass(frozen=True)
class ComputedRouteFact:
    from_location_id: str
    to_location_id: str
    walking_minutes: int
    path_location_ids: tuple[str, ...]

@dataclass(frozen=True)
class CheckedPlanOption:
    candidate_id: str
    rank: str                         # primary 或 alternative
    steps: tuple[ProposedStep, ...]
    route_facts: tuple[ComputedRouteFact, ...]
    route_data: RouteDataProvenance | None
    expected_arrival_at_commitment: datetime | None
    achieved_buffer_minutes: int | None
    computation_status: PlanComputationStatus
    assumptions_used: tuple[str, ...]
    warnings: tuple[str, ...]
    requires_explicit_override: bool
```

`PlanComputationStatus` 和 `RouteDataTrust` 是两个独立维度：

- `PlanComputationStatus` 只回答“程序是否使用给定输入完成计算，以及按这些输入是否可行”。
- `RouteDataTrust` 只回答“步行边和分钟数据来自哪里、能否代表真实校园”。
- `COMPUTED_FEASIBLE + SYNTHETIC_TEST` 是合法组合，含义是“在虚构测试图上程序计算可行”，绝不能展示为“真实校园路线已验证”。
- `COMPUTED_FEASIBLE + ESTIMATED_UNVERIFIED` 只能展示为“基于暂估路线数据计算可行”。
- 只有 `REAL_VERIFIED` 才可把路线数据描述为真实且已经验证；即使如此，可行性仍必须由程序另行计算。

当前仓库中，`tests/fixtures/sample_campus_map.json` 必须标为 `SYNTHETIC_TEST`。`data/beiyangyuan_locations.json` 只有真实地点目录，没有步行 edges 和真实步行分钟，因此不能据此构造 `ComputedRouteFact`，也不能产生真实路线或真实可行性结论；相应计算状态应保持 `COMPUTATION_INCOMPLETE`，直到存在合格的路线数据。

首选和备选都必须经过程序检查后才成为 `CheckedPlanOption`。模型可以建议任务顺序和本次投入时长，但不能提供正式步行分钟、路径、到达时间、缓冲、计算状态或路线数据可信状态。

当用户坚持执行程序计算为可能迟到的方案时：

- 保留 `COMPUTED_INFEASIBLE`；
- 展示预计迟到或缓冲不足的后果；
- 同时提供安全备选；
- 设置 `requires_explicit_override=True`；
- 用户确认只表示允许执行，不得把计算状态改写为 `COMPUTED_FEASIBLE`，也不得提升 `RouteDataTrust`。

### C.7 字段来源和写回规则

| 字段类别 | 谁提供/计算 | 可否默认 | 可否 AI 暂估 | 能否由规划结果回写任务事实 |
| --- | --- | --- | --- | --- |
| 当前时间 | 系统时钟或用户确认 | 可使用系统当前时间 | 否 | 否 |
| 当前地点 | 用户或可信定位系统 | 不建议猜；缺失时追问 | 可理解别名，但不能虚构已验证地图点 | 否 |
| 下一固定安排开始时间 | 用户、用户确认或未来可信课表 | 缺失时不能声称可按时 | 否 | 否 |
| 下一固定安排地点 | 用户、用户确认或未来可信课表 | 缺失时不能生成正式路线 | 可提候选解释，但不能标为地图事实 | 否 |
| 固定安排中可用程度 | 用户明确说明/确认 | 可保守默认为完全不可用并标记系统默认 | 不应替用户决定 | 否 |
| 缓冲分钟 | 用户或系统默认 | 推荐默认 10 分钟 | 否 | 否 |
| 任务标题/原文 | 用户 | 否 | 模型可归纳标题，但必须保留原文 | 否 |
| 任务总时长、可拆分、最小片段、注意力、可中断性 | 用户、确认、默认或模型 | 个别字段可默认 | 非空值必须标记 `AI_EXTRACTED_FROM_USER_TEXT` 或 `AI_ESTIMATED` 并说明理由 | 候选方案不能覆盖；用户确认后可更新 |
| 地点要求/地点文本/环境/设备约束 | 用户、系统地点解析或模型理解 | 不能用空值代替三态；必须是 `NO_SPECIFIC_LOCATION`、`SPECIFIC_LOCATION` 或 `LOCATION_REQUIREMENT_UNKNOWN` | 非空值必须记录来源；未知时不得假定地点不限 | 否 |
| 已完成分钟 | 用户反馈或可信执行记录 | 新任务可默认 0 | 否 | 只有执行反馈可更新 |
| 剩余分钟 | 程序从总时长和已完成分钟只读派生 | 总时长未知时也未知 | 否 | 不能写回；序列化/展示值也必须在读取后重算 |
| 本次计划分钟、首选/备选排序、理由 | 大模型建议 + 程序整理 | 不适用 | 属于建议 | 否，只写窗口记录 |
| 路径、步行时间、ETA、实际缓冲、计算可行性 | 程序基于当前提供的数据计算 | 否 | 否；拒绝模型提供这些正式值 | 否，只写 `CheckedPlanOption` |
| 路线数据可信状态 | 程序根据数据集元数据设置 | 测试 fixture 固定为 `SYNTHETIC_TEST` | 否；模型不得声明或提升 | 否，保存在 `RouteDataProvenance` |
| 风险、警告、使用的假设 | 模型提出，程序补充/升级 | 不适用 | 可以 | 否 |

如果当前地点、下一安排开始时间、下一安排地点或某个 `SPECIFIC_LOCATION` 任务的必要地点缺失，仍可展示部分建议，但计算状态必须保持 `NOT_COMPUTED` 或 `COMPUTATION_INCOMPLETE`，并把关键追问放在显著位置。`LOCATION_REQUIREMENT_UNKNOWN` 的任务也不能静默进入正式路线。

---

## D. 来源与可信度设计

### D.1 推荐：只为决策关键字段记录字段级来源

不建议把每个标量都包装成 `SourcedValue[T]`。那会让业务代码、JSON 和页面读取都变得笨重。推荐：

1. 核心对象保留普通字段，便于计算和校验。
2. 每个任务、窗口和固定安排带一个 `evidence` 映射。
3. 只为会影响推荐、可行性或用户信任的字段记录来源。
4. 程序派生结果使用专门的检查结果对象，不与任务 `evidence` 混放；路线结果还必须单独携带 `RouteDataProvenance`。

建议记录来源的任务字段包括：重要性、deadline、地点约束、预计总时长、可拆分性、最小执行时长、注意力、可中断性和开放时间风险。`task_id`、内部 schema 版本等技术字段不需要来源。

### D.2 五类来源的展示语义

| 来源 | 含义 | 页面建议标签 |
| --- | --- | --- |
| `USER_STATED` | 用户本次明确说出，尚未额外确认 | 用户提供 |
| `USER_CONFIRMED` | 用户通过按钮或自然语言确认过 | 用户已确认 |
| `SYSTEM_DEFAULT` | CampusFlow 的可修改默认策略 | 系统默认 |
| `SYSTEM_VERIFIED` | 程序核验的一般事实或确定性派生值；对路线不能替代 `RouteDataTrust` | 系统已核验 |
| `AI_EXTRACTED_FROM_USER_TEXT` | 模型从用户原话识别的结构化属性 | 从用户原话识别 |
| `AI_ESTIMATED` | 模型根据自然语言临时推测 | AI 暂估 |

`SYSTEM_DEFAULT` 不是事实。例如“默认预留 10 分钟”不能显示为“系统已核验需要 10 分钟”。`AI_ESTIMATED` 更不能与程序计算结果放在同一可信层级。路线场景还要额外展示数据可信状态：程序在 synthetic fixture 上正确算出的分钟可以是系统计算结果，但底层路线仍只能标为“虚构测试数据”，不能仅凭 `SYSTEM_VERIFIED` 标签升级成真实校园事实。

### D.3 冲突与升级规则

来源优先顺序为：

```text
用户本次明确说明/用户确认
→ 未来的用户历史习惯
→ CampusFlow 系统默认
→ AI 暂估
```

P1 暂不实现历史习惯，但模型留有扩展空间。发生冲突时：

- 新的用户明确说明可以替换旧的系统默认或 AI 暂估。
- 用户确认 AI 暂估后，来源升级为 `USER_CONFIRMED`，并保留原估计说明以便审计。
- AI 输出不得覆盖 `USER_STATED`、`USER_CONFIRMED` 或 `SYSTEM_VERIFIED`。
- 地点解析、路线分钟和可行性只能由程序计算；程序同时根据数据集元数据写入 `RouteDataTrust`，二者不能合并成一个“已验证”标签。
- 大模型无权输出 `SYSTEM_VERIFIED`、`PlanComputationStatus` 或 `RouteDataTrust`，也无权把 synthetic/暂估数据提升为 `REAL_VERIFIED`。
- 任何来源变更都应由应用服务显式处理，不能靠模型返回一个更高可信度标签完成。

---

## E. 任务状态设计

### E.1 三个正交维度

为了避免一个巨大且互相冲突的状态枚举，任务状态分成三个维度：

1. **长期生命周期 `TaskLifecycle`**：`ACTIVE`、`DEFERRED`、`ABANDONED`、`CANCELLED`、`COMPLETED`。
2. **进度状态**：由分钟数派生为未开始、部分完成、已完成。
3. **窗口结果 `WindowDisposition`**：本窗口已安排、未安排、用户主动跳过。

对应关系：

| 中文语义 | 所属维度 | 是否影响以后窗口 |
| --- | --- | --- |
| 未开始 | 进度：`completed_minutes == 0` | 仍可出现 |
| 部分完成 | `completed_minutes > 0` 且长期状态尚未完成；总时长未知时剩余量也未知 | 保留已有进度并继续出现 |
| 已完成 | 长期 `COMPLETED` + 进度完成 | 默认不再进入候选 |
| 当前窗口未安排 | 窗口 `NOT_SCHEDULED` | 不改变长期状态，未来仍可出现 |
| 当前窗口主动跳过 | 窗口 `SKIPPED_BY_USER` | 不改变长期状态，未来仍可出现 |
| 推迟 | 长期 `DEFERRED`，可附恢复时间/条件 | 条件满足后可重新激活 |
| 永久放弃 | 长期 `ABANDONED` | 不再自动出现 |
| 用户取消 | 长期 `CANCELLED` | 不再自动出现 |

`ABANDONED` 和 `CANCELLED` 都是终止状态，但原因不同：前者表示任务仍成立、用户决定不做；后者表示任务已撤销或不再需要。两者都不得由“本窗口没选中”推导出来。

### E.2 低影响、可恢复的意图映射

模型只提出意图候选，程序按以下保守规则应用：

- “这次先不做”“我现在不想吃饭了”默认映射为 `SKIPPED_BY_USER`。
- “以后再做”“推迟到晚上”映射为 `DEFERRED`，保留任务。
- “这个任务取消了/不需要了”可映射为 `CANCELLED`。
- 只有“我永远不做了”“彻底放弃”“以后不做了”等明确表达，模型才可提出 `ABANDONED` 意图。
- 模糊表达绝不升级为终止状态；必要时给出快捷确认。
- 终止状态更新必须引用稳定 `task_id`，不能只按地点删除。
- 第一版页面不需要同时展示两个容易混淆的“取消”和“永久放弃”按钮；普通用户操作统一显示“取消任务”，内部应用为 `CANCELLED`。`ABANDONED` 主要服务于明确自然语言意图。
- 大模型只能返回待处理的状态更新意图。程序必须校验任务 ID、当前状态、表达是否足够明确以及操作是否幂等，再决定是否应用；模型不能直接写长期状态。

### E.3 进度不变量

程序必须校验：

```text
estimated_total_minutes >= 0（如果已知）
completed_minutes >= 0
remaining_minutes = None（estimated_total_minutes 未知时）
remaining_minutes = max(estimated_total_minutes - completed_minutes, 0)（总时长已知时）
planned_minutes <= remaining_minutes（如果剩余量已知）
可拆分任务：planned_minutes >= minimum_slice_minutes，除非本次完成全部剩余量
不可拆分任务：planned_minutes 必须覆盖全部剩余量
```

更新规则：

- 点击“完成本次”只按本次计划分钟更新 `completed_minutes`；“完成了一部分”需要用户给出分钟或通过快捷值确认；“没有开始”增加 0。AI 不能猜用户实际完成了多少。
- 用户明确说明或确认新的总时长时，可以更新 `estimated_total_minutes`；模型建议和规划结果都不能直接覆盖它。
- `remaining_minutes` 没有 setter，也不是事件载荷。即使序列化或页面展示包含它，读取状态时仍必须从两个事实字段重算并忽略外部传入的剩余值。
- 如果新的总时长小于已经记录的完成时长，程序先暂停更新并请用户确认“这是新的总时长，不是剩余时长”。确认后保留真实的 `completed_minutes`，接受新总时长，派生剩余量为 0，转为已完成并记录这次不一致说明；不得静默减少已完成分钟或产生负数。

### E.4 不复活保证与 P0 `kept_tasks` 不变量

P1 推荐维护两个不同集合：

```text
P1TaskLedger
  保存所有仍有长期意义的任务及状态、进度

P0EffectivePlanningRequest
  只保存当前 P0 路线管线下一轮真正保留的任务
```

生成新窗口候选时：

```python
eligible_tasks = [
    task for task in ledger.tasks
    if task.lifecycle in {ACTIVE, DEFERRED且已到恢复条件}
]
```

`CANCELLED`、`ABANDONED`、`COMPLETED` 永不进入 `eligible_tasks`。`NOT_SCHEDULED` 和 `SKIPPED_BY_USER` 只存在于窗口记录，因此任务仍留在账本。

与 P0 管线交互时仍必须：

```python
p0_effective_request.tasks = list(task_selection_result.kept_tasks)
```

绝不使用：

```python
p0_effective_request.tasks = replanning_result.updated_request.tasks
```

兼容层返回后，P1 账本只应用明确的生命周期/进度事件；选择器的 `dropped_tasks` 只能生成 `WindowDisposition.NOT_SCHEDULED`，不能删除账本任务。这样既保护 P0 中“取消或已放弃任务不复活”的现有行为，也让 P1 的“本窗口未安排”保持可恢复。

---

## F. 与 P0 的兼容策略

### F.1 三种方案比较

| 方案 | 优点 | 主要问题 | 结论 |
| --- | --- | --- | --- |
| 1. 直接扩展现有 `Task` | 文件少，短期看似直接 | 会改变构造函数、严格解析字段、校验器、路线器、选择器、格式化器和大量测试；可选地点、进度和来源会让 P0 每层都处理新分支 | 不推荐作为第一步 |
| 2. 新增独立 P1 任务模型 | 语义清晰，可按 P1 正确建模，P0 测试受影响小 | 若完全独立，会重复地点、路线和时间能力 | 单独使用不够 |
| 3. 新 P1 模型 + P0/P1 兼容层 | P1 语义独立，同时复用现有地点解析、路线和时间计算算法；可逐步迁移 | 需要明确转换条件，并另外标记底层路线数据可信度 | **推荐** |

推荐方案本质上是：新增 P1 领域模型，并通过窄兼容层调用仍适用的 P0 能力。不是在旧模型上堆大量可选字段，也不是复制一套路线算法。

### F.2 兼容层职责

建议未来增加类似 `p1_compat.py` 的模块，职责限定为：

- 仅把“有已解析地点、可转换为一次执行片段”的 P1 任务投影为 P0 `Task`。
- 为投影建立 `p1_task_id ↔ p0_task` 映射，不能依赖地点作为身份。
- 用窗口起点构造 P0 `current_time/current_location`。
- 用下一固定安排地点构造 P0 `destination`。
- 将本次片段分钟写入 P0 `estimated_duration_minutes`，而不是总剩余时长。
- 调用 P0 路线/日程能力后，把 `kept_tasks`、路线和时间映射回 P1 验证结果。
- P0 日程检查器只检查任务 deadline，不检查最终 `destination` 的固定安排开始时间；兼容层必须另行验证“到达最终地点时间不晚于固定安排开始时间减缓冲”。
- 地点无关任务不伪造地图地点；它们由 P1 时间段调度逻辑处理。
- 不把 P0 `dropped_tasks` 写成长期取消、永久放弃或完成。

兼容层不负责理解自然语言，也不负责最终推荐排序。

### F.3 对现有文件的未来影响

首个实现切片只应新增：

- `src/p1_models.py`
- `src/p1_extraction_models.py`
- `src/p1_prompt_builder.py`
- `src/p1_parser.py`
- 对应的独立测试文件

后续切片再新增 `src/p1_compat.py`、`src/p1_planning_pipeline.py`，并在需要页面接入时小范围修改：

- `src/main.py`：增加 P1 页面状态和操作，不改变 P0 保存函数语义；
- `src/result_formatter.py` 或新增 `p1_result_formatter.py`：显示来源、假设、风险、首选和备选；
- 可能新增 P1 状态服务，避免业务模型直接依赖 `st.session_state`。

以下 P0 文件初期尽量不改：`models.py`、`structured_parser.py`、`task_selector.py`、`planning_pipeline.py`、`dynamic_replanner.py`、`replanning_parser.py`。

### F.4 避免破坏 693 项测试

- 保留 P0 类名、字段、构造参数、严格 JSON 白名单和返回类型。
- P1 使用新入口，不把 P1 JSON 交给 P0 `StructuredParser`。
- P0 页面路径继续使用现有格式化和 session helper。
- 对 P1 适配器增加契约测试，特别断言下一轮 P0 请求只使用 `kept_tasks`。
- 路线兼容测试必须同时断言计算状态和 `RouteDataTrust`，不能只断言“可行”。
- 每个实现切片先运行新增测试和受影响的 P0 小集合，完成里程碑后再做一次全仓验收；不在每个文档或纯模型切片重复跑全量测试。
- 用 mock/fixture 验证 LLM 输出和地图转换，CI 不调用真实 API。

P0 管线应保留，直到 P1 已覆盖其演示场景且有独立回归证据。迁移可以按“解析层 → 候选层 → 验证层 → 页面层”逐步进行，不做一次性替换。

---

## G. 大模型输出结构建议

### G.1 严格 JSON 示例

首个实现切片使用单独、较小的任务理解 schema。它不包含窗口、候选方案、路线、状态更新或进度反馈：

```json
{
  "schema_version": "p1.task-understanding.v1",
  "task_interpretations": [
    {
      "task_ref": "input-task-1",
      "title": "完成计组实验3",
      "original_text": "完成计组实验3",
      "understood_features": {
        "location_requirement": "location_requirement_unknown",
        "location_text": null,
        "environment_requirements": [],
        "equipment_requirements": ["电脑"],
        "estimated_total_minutes": 90,
        "is_splittable": true,
        "minimum_slice_minutes": 30,
        "attention_required": "high",
        "interruption_allowed": true,
        "may_have_open_hours": false
      },
      "field_evidence": {
        "location_requirement": {
          "source": "ai_estimated",
          "explanation": "暂时无法判断是否必须在机房或实验室完成"
        },
        "equipment_requirements": {
          "source": "ai_estimated",
          "explanation": "实验任务可能需要使用电脑"
        },
        "estimated_total_minutes": {
          "source": "ai_estimated",
          "explanation": "用户未提供时长，按一次课程实验任务暂估"
        },
        "attention_required": {
          "source": "ai_estimated",
          "explanation": "实验任务通常需要持续专注"
        },
        "is_splittable": {
          "source": "ai_estimated",
          "explanation": "暂估可以分阶段完成"
        },
        "minimum_slice_minutes": {
          "source": "ai_estimated",
          "explanation": "暂估少于30分钟难以形成有效进展"
        },
        "interruption_allowed": {
          "source": "ai_estimated",
          "explanation": "暂按中断会影响连续实验处理"
        },
        "may_have_open_hours": {
          "source": "ai_estimated",
          "explanation": "暂不确定是否受实验室开放时间限制"
        }
      },
      "needs_confirmation": [
        "location_requirement",
        "estimated_total_minutes",
        "minimum_slice_minutes"
      ]
    }
  ],
  "clarification_questions": [
    {
      "question_id": "q1",
      "task_ref": "input-task-1",
      "field_name": "location_requirement",
      "question": "计组实验3是否必须在机房或实验室完成？",
      "blocking_for_task_understanding": false,
      "quick_options": ["不需要特定地点", "需要特定地点", "暂不确定"]
    }
  ]
}
```

### G.2 严格解析和交叉验证

首个切片的 P1 解析器至少应：

- 对每个澄清问题使用稳定的 `task_ref` 与 `field_name`，不使用依赖任务数组顺序的 `field_path`；两者分别必须引用已存在任务和该任务 `needs_confirmation` 中的允许特征。任务重排后关联仍保持正确。
- 拒绝“字段有值但无来源”以及“字段为空或数组为空却带来源”的输出；来源完整性不等同于必须逐字段追问。

- 拒绝 Markdown、重复 JSON key、未知字段、错误类型、非法枚举和越界分钟；
- 限制数组和文本长度，避免异常大输出；
- 校验 `task_ref` 唯一且只引用本次程序提供的输入，`original_text` 必须与保存的用户原文一致；
- 严格校验地点三态：`NO_SPECIFIC_LOCATION` 不得带地点；`SPECIFIC_LOCATION` 可以地点已知或待确认，但模型不能填写系统地图 ID；`LOCATION_REQUIREMENT_UNKNOWN` 不得被转换为地点不限；
- AI 暂估字段必须使用 `ai_estimated` 并提供非空简短理由；拒绝模型伪造 `system_verified`、`real_verified` 或其他系统可信来源；没有程序提供的确认记录时，也拒绝 `user_confirmed`；
- 对模型声称的 `user_stated`/`user_confirmed` 与原始输入和已有状态做交叉检查；
- schema 不接受 `completed_minutes` 或 `remaining_minutes`；首切片既不采集进度，也不允许模型制造派生剩余量；
- schema 不接受窗口、候选方案、路线分钟、路径、计算状态、`RouteDataTrust`、生命周期更新或意图更新字段；这些属于后续版本。

### G.3 后续切片的输出扩展与程序边界

后续切片可以在**新 schema 版本**中增加：

- `primary_candidate` 和 `alternative_candidate`：首选/备选候选步骤、理由、假设和模型警告；
- `intent_updates`：`skip_current_window`、`defer`、`cancel`、`abandon`、进度反馈等待程序应用的意图；
- `clarification_questions` 的窗口级关键问题。

这些扩展不是 `p1.task-understanding.v1` 的允许字段，也不是首个实现切片的验收要求。未来解析器仍需校验候选引用、分钟不超过程序计算的已知剩余量，以及模型只能提出而不能直接应用长期状态更新。

### G.4 哪些只是模型建议

下列输出都只是建议，必须经过程序处理：

- 对任务特征的理解和 AI 暂估；
- 是否需要追问及追问优先级；
- 首选/备选候选、任务顺序和本次建议分钟；
- 推荐理由和主观风险；
- 用户意图更新。

程序必须独立完成或确认：

- 地点是否存在、别名解析结果以及必要地点是否缺失；
- 路径、步行时间、到达/完成时间；
- 是否满足下一安排时间和缓冲；
- `PlanComputationStatus` 和 `RouteDataTrust`，包括识别 synthetic、暂估和真实已验证路线数据；
- 开放时间是否来自已核验数据；
- 生命周期和进度写回是否合法；
- 危险方案是否需要用户显式覆盖。

只有程序生成的 `CheckedPlanOption` 才能在页面上称为已经计算的首选或备选。页面必须把“计算结果”和“路线数据来源/可信状态”分别展示：synthetic 数据可以显示“测试图上计算可行”，但不能显示“真实校园路线已验证”。计算不完整时可以展示“暂定建议”，但不能使用“可按时”“安全可行”等确定措辞。

---

## H. 后续实现切片

### H.1 切片 1：任意任务理解、AI 暂估、来源标记和严格离线解析

- **唯一验收主链路**：`用户输入“完成计组实验3” → mock/fixture 提供模型结构化输出 → 严格解析器验证 → 得到一张可以展示和纠正的任务理解结果`。
- **任务理解结果覆盖**：自由文本标题和原文、通用任务特征、AI 暂估值及简短理由、地点要求三态、真正需要确认的字段和快捷确认问题。
- **严格边界**：拒绝未知字段、重复 key、错误类型、非法枚举、越界分钟、没有解释的 AI 暂估、虚假的高可信来源，以及不符合地点三态不变量的组合。
- **任务理解卡的验收方式**：先检查解析结果对象或测试 fixture 的确定性文本即可，不要求接入正式 Streamlit 页面。
- **预计文件**：只新增 `src/p1_models.py`、`src/p1_extraction_models.py`、`src/p1_prompt_builder.py`、`src/p1_parser.py`，以及 `tests/test_p1_models.py`、`tests/test_p1_prompt_builder.py`、`tests/test_p1_parser.py` 和必要 fixture；不改 P0 模型与管线。首切片的 `p1_models.py` 只实现任务理解所需类型，本文中的窗口、候选、路线、账本和进度类仍只是后续设计。
- **明确不包含**：固定安排、完整时间窗口、首选/备选、路线兼容层、Streamlit 改造、任务账本、部分完成反馈、数据库和真实 API 调用。
- **真实 API**：不需要；所有验收使用固定 JSON fixture/mock，禁止把真实 API 成功作为通过条件。
- **运行环境**：新增测试必须用项目 Python 3.8.10 虚拟环境；类型提示使用 Python 3.8.10 可执行写法。
- **P0 风险**：低；新模块隔离，不重复全量验收 P0。

### H.2 切片 2：单窗口、固定安排和可用程度

- **用户可见能力**：可以表达“现在到 11:30”“下一安排在 46 教学楼”“上课期间低注意力可用”，并只追问真正阻塞路线的信息。
- **预计文件**：扩展 P1 新模型和管线；新增 P1 窗口构造服务。
- **测试**：三种可用程度、缺少当前位置/开始时间/地点的降级结果、10 分钟默认缓冲来源、低注意力时只有确定无需移动或地点就是当前位置的任务可正式安排、地点要求未知不能静默按地点不限、高注意力任务产生效率警告而非硬禁止。
- **真实 API**：不需要；时间和地点均用 fixture。
- **P0 风险**：低；不改变 P0 `PlanningRequest`。

### H.3 切片 3：候选方案与 P0 路线兼容层

- **用户可见能力**：获得一个首选和一个简短备选；有地图数据时展示程序计算的路线、到达时间和缓冲。
- **预计文件**：新增 `p1_compat.py`、`p1_planning_pipeline.py`，复用校园地图和路线/时间模块；必要时只做小型只读接口抽取。
- **测试**：P1/P0 ID 映射、三种地点要求、片段时长转换、模型伪造路线时间/数据可信状态被拒绝、`kept_tasks` 写回契约；同一算法结果分别搭配 `SYNTHETIC_TEST`、`ESTIMATED_UNVERIFIED`、`REAL_VERIFIED` 的展示和状态断言；`data/beiyangyuan_locations.json` 无 edges 时必须是 `COMPUTATION_INCOMPLETE`。
- **真实 API**：不需要；`tests/fixtures/sample_campus_map.json` 只能产生 `SYNTHETIC_TEST` 结果。没有已验证北洋园步行 edges 和分钟时不得进行真实校园路线验收。
- **P0 风险**：中低；风险集中在适配边界，必须运行路线、日程、任务选择和动态页面相关 P0 测试。

### H.4 切片 4：P1 结果展示和快捷修改

- **用户可见能力**：页面显示首选、备选、用户信息、系统事实、AI 暂估、假设、风险和快捷操作，如“开始首选”“采用备选”“补充情况”；路线区域分别显示计算结果与 `RouteDataTrust`，不得用一个“已验证”标签合并。
- **预计文件**：优先新增 `p1_result_formatter.py` 和 P1 页面组件；小范围修改 `main.py` 增加入口，不改现有 P0 helper 语义。
- **测试**：来源标签、计算状态与路线数据可信标签分别展示、synthetic 路线不出现“真实校园已验证”、敏感信息隐藏、关键追问、按钮事件、错误不覆盖旧状态、首选/备选结构完整。
- **真实 API**：自动测试不需要；页面烟雾测试使用 mock。
- **P0 风险**：中；页面 session key 必须与 P0 隔离，并运行现有 main/formatter 测试。

### H.5 切片 5：本窗口反馈、部分完成和任务账本

- **用户可见能力**：支持“完成本次”“完成了一部分”“没有开始”，并在下一窗口保留正确剩余量；本窗口跳过的任务以后仍出现。
- **预计文件**：新增 P1 task ledger/应用服务和 session 适配；增加自然语言反馈解析器。
- **测试**：总时长未知则剩余未知、剩余量只读派生、序列化值不能写回、用户反馈只更新完成分钟、用户确认才更新总时长、总时长小于已完成量的确认流程、不可拆分任务、最小片段、重复反馈幂等、未安排/跳过不删除、取消/永久放弃/完成不复活、模糊“不想做”映射为本窗口跳过。
- **真实 API**：不需要；反馈解析使用 fixture/mock。
- **P0 风险**：中；重点回归动态写回只使用 `kept_tasks`，并证明 P1 账本与 P0 当前请求互不覆盖。

### H.6 切片 6：不安全方案的显式用户覆盖

- **用户可见能力**：用户坚持可能迟到的方案时，看到劝阻、预计后果和安全备选；确认后可以继续，但页面仍标记不可行。
- **预计文件**：P1 验证结果、操作事件和页面确认流程。
- **测试**：不可行标记不可被确认改写、必须二次确认、安全备选始终可见、确认事件不修改系统验证事实。
- **真实 API**：不需要。
- **P0 风险**：低到中；功能限定在 P1 执行层。

每个切片都应独立验收，并使用项目 Python 3.8.10 虚拟环境。账号、数据库、真实课表、多设备同步和多天持久化不属于这些 P1 初始切片。

---

## I. 已确认产品默认值

以下规则已经通过产品审查，是后续实现的正式默认值，不再作为未决问题重复询问：

| 已确认事项 | 正式默认值 | 实现说明 |
| --- | --- | --- |
| 安全缓冲 | **10 分钟，可快捷修改** | 标记 `SYSTEM_DEFAULT`，不能说成地图事实；未来可由用户偏好替换。 |
| 未声明固定安排可用程度 | **`UNAVAILABLE`** | 保守验证，同时提供快捷确认。 |
| 当前地点缺失 | **阻塞正式路线，不阻塞明确地点不限任务的暂定建议** | `LOCATION_REQUIREMENT_UNKNOWN` 不算明确地点不限。 |
| 下一安排时间或地点缺失 | **只给计算不完整的部分建议并显著追问** | 不猜关键锚点，不承诺按时到达。 |
| AI 暂估置信表达 | **不使用百分比，只记录来源和简短理由** | 避免虚假精确。 |
| 可拆分任务最小片段缺失 | **允许 AI 暂估并标记** | 不统一硬编码，影响方案时可快捷修改。 |
| “我不想做了”等模糊表达 | **只映射为当前窗口主动跳过** | 明确“取消”才取消；明确“彻底放弃/以后不做”才可提出 `ABANDONED`。 |
| 部分完成但没有分钟 | **追问并提供快捷选项** | AI 不猜实际完成量。 |
| 内部终止状态 | **同时保留 `ABANDONED` 与 `CANCELLED`** | 第一版页面普通操作只显示“取消任务”。 |
| 首选和备选的按时声明 | **都必须由程序计算** | 同时单独展示路线数据可信状态。 |
| 数据库 | **P1 暂不引入** | 先验证模型和交互边界。 |
| P0 页面和管线 | **继续保留** | 直到 P1 覆盖场景并完成回归验收。 |

**首个实现切片目前没有产品阻塞问题。** 它不需要路线数据、窗口默认值、页面按钮或进度策略即可按 H.1 的离线链路实现和验收。

---

## J. 一致性检查清单

- [x] AI 暂估与用户信息、系统默认和系统验证事实分开显示。
- [x] 首切片中每个重要非空任务属性都有来源；模型只能声明 `AI_EXTRACTED_FROM_USER_TEXT` 或 `AI_ESTIMATED`，且二者均有中文解释。
- [x] 澄清问题使用 `task_ref + field_name`，不使用数组索引 `field_path`；不可拆分任务的最小片段与总时长保持一致。
- [x] 路线“程序计算状态”和“底层数据可信状态”是独立字段；synthetic 测试图计算通过不等于真实校园路线已验证。
- [x] `tests/fixtures/sample_campus_map.json` 只标为 `SYNTHETIC_TEST`；没有 edges 的 `data/beiyangyuan_locations.json` 不产生真实路线结论。
- [x] 没有设计封闭的现实任务类型枚举。
- [x] 地点不限、需要地点但地点未知、地点要求本身未知能够明确区分，未知状态不会静默变成地点不限。
- [x] 用户不需要先填写完整任务表单；可先给暂定建议和少量关键追问。
- [x] 大模型不直接决定路线、步行时间、ETA、缓冲、计算可行性或路线数据可信状态。
- [x] 当前窗口未安排和主动跳过都不会变成永久放弃。
- [x] `CANCELLED`、`ABANDONED`、`COMPLETED` 任务不会自动复活。
- [x] P0 下一轮请求继续只使用 `task_selection_result.kept_tasks`。
- [x] `remaining_minutes` 只有程序派生这一个来源；总时长未知时剩余也未知，模型不猜实际进度。
- [x] 首个实现切片只覆盖任务理解、AI 暂估、来源标记和严格离线解析，不包含窗口、路线、页面、账本或进度反馈。
- [x] 正式实现和新增测试明确兼容 Python 3.8.10，不因类型提示升级 Python。
- [x] 已通过产品审查的默认值已列为正式默认值，不再作为未决问题。
- [x] 没有提前实现账号、数据库、真实课表或真实地图服务。
- [x] 推荐渐进兼容，不一次性重写 P0。

## K. 推荐结论

P1a 推荐采用“**P1 新领域模型 + 窄兼容层 + 保留 P0 管线**”。P1 任务账本负责长期状态和进度，窗口记录负责本次未安排/跳过/执行结果，模型候选负责主观建议，程序计算结果负责路线时间和可行性，`RouteDataProvenance` 另行说明这些计算所依赖的数据是否真实可信。各层不能混写。

第一个实现切片严格限定为任意个性化任务理解、AI 暂估、来源标记和严格离线解析。它只需把“完成计组实验3”的 mock/fixture 模型输出解析成可展示、可纠正的任务理解结果，不实现窗口、首选/备选、路线、页面、账本或进度反馈。

根据当前工作环境提供的 Codex 模型说明，建议这个首个实现切片使用 `gpt-5.6-sol`、`high` 推理强度：该切片涉及严格 schema、跨层不变量和兼容边界，优先保证审查与实现质量；常规机械性测试补充可再使用更轻量的配置。本建议没有通过外网查询实时型号或可用性，实际执行时以当时 Codex 界面可选模型为准。
