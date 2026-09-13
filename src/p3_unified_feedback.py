"""P3e 第二轮：统一反馈理解 Agent（Python 3.8 兼容，全部 mock 可测）。

一条用户 feedback 由 TJU Qwen 一次性解析出可能同时存在的：
task_updates / commitment_updates / movement（含 depart_at / arrive_by）/
current_location / questions；程序随后分别执行：

  commitment updates -> 重派生窗口
  -> task updates -> 安全应用到任务台账
  -> movement -> P3 地点解析 + 真实寻路 + 时间估计 + 按明确时间扣容量
  -> 统一 replanning（无移动时走 P2c pipeline，有移动时走确定性分配）

程序只守：
- task / commitment ref 必须真实存在（复用 apply_reconciliation /
  apply_commitment_reconciliation，unknown ref 会被忽略并警告）；
- 地点必须解析到真实 P3 node，距离来自本地地图，运行时绝不调高德；
- lifecycle 安全由 src.p2_task_progress 保证；
- 明确时间移动（depart_at / arrive_by）由程序换算真实时间段；
- 顶层 questions 非空时本轮不应用任何更新（身份不确定则不动状态）；
- 所有模型调用进入同一计数，每轮硬上限 MAX_CALLS_PER_ROUND。
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional, Tuple

from src.p2_agentic_models import MAX_CALLS_PER_ROUND, P2AgenticDayResult, ReconciliationResult
from src.p2_agentic_parser import AgenticParseError, extract_json_object, parse_reconciliation
from src.p2_agentic_pipeline import run_p2_agentic_day_planning
from src.p2_agentic_prompt_builder import (
    build_repair_prompt,
    format_history,
    format_task_ledger,
)
from src.p2_commitment_reconciler import (
    COMMITMENT_SCHEMA_VERSION,
    CommitmentReconciliationResult,
    apply_commitment_reconciliation,
    format_commitments,
    parse_commitment_reconciliation,
)
from src.p2_day_plan import summarize_day_plan
from src.p2_models import DayPlanningState, append_history
from src.p2_state_reconciler import apply_reconciliation
from src.p3_map_schema import CampusMapData, parse_transport_mode
from src.p3_location_resolver import resolve_location
from src.p3_route_planner import (
    MovementRequest,
    P3MovementPlan,
    final_plan_overlap_errors,
    plan_day_with_movements,
    reapply_movement_blocks,
)
from src.p3_class_prep import class_arrival_deadline

AgentCaller = Callable[[str, str], str]

UNIFIED_FEEDBACK_SCHEMA_VERSION = "p3.unified-feedback.v1"

MAX_LOCATION_TEXT_LENGTH = 80
MAX_REASON_LENGTH = 200
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

WARNING_UNIFIED_FAILED = "本轮反馈暂未完整理解，未应用任何更新，请再说明一次。"


@dataclass(frozen=True)
class UnifiedMovement:
    """统一反馈中的移动部分（模型只给原话与时间意图，程序负责真实寻路）。"""

    has_movement: bool
    origin_text: Optional[str]
    destination_text: Optional[str]
    mode: Optional[str]
    depart_at: Optional[str]
    arrive_by: Optional[str]

    def __post_init__(self):
        if not isinstance(self.has_movement, bool):
            raise ValueError("has_movement 必须是布尔值")
        if not self.has_movement:
            return
        for name, value in (
            ("origin_text", self.origin_text),
            ("destination_text", self.destination_text),
        ):
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("{} 必须是非空字符串".format(name))
                if len(value) > MAX_LOCATION_TEXT_LENGTH:
                    raise ValueError("{} 过长".format(name))
        if self.mode is not None:
            parse_transport_mode(self.mode)
        for name, value in (("depart_at", self.depart_at), ("arrive_by", self.arrive_by)):
            if value is not None:
                if not isinstance(value, str) or _TIME_RE.match(value) is None:
                    raise ValueError("{} 必须是 HH:MM 或 null".format(name))
        if self.depart_at is not None and self.arrive_by is not None:
            raise ValueError("depart_at 与 arrive_by 不能同时设置")


@dataclass(frozen=True)
class UnifiedFeedback:
    """统一理解结果：内嵌数组已复用 P2c / P2d 解析器完成校验。"""

    schema_version: str
    task_result: ReconciliationResult
    commitment_result: CommitmentReconciliationResult
    movement: UnifiedMovement
    current_location: Optional[str]
    questions: Tuple[str, ...]
    reason: Optional[str]

    def __post_init__(self):
        if self.schema_version != UNIFIED_FEEDBACK_SCHEMA_VERSION:
            raise ValueError("unified feedback schema_version 不匹配")
        if not isinstance(self.task_result, ReconciliationResult):
            raise TypeError("task_result must be a ReconciliationResult")
        if not isinstance(self.commitment_result, CommitmentReconciliationResult):
            raise TypeError("commitment_result must be a CommitmentReconciliationResult")
        if not isinstance(self.movement, UnifiedMovement):
            raise TypeError("movement must be a UnifiedMovement")
        if self.current_location is not None:
            if not isinstance(self.current_location, str) or not self.current_location.strip():
                raise ValueError("current_location 必须是非空字符串或 null")
            if len(self.current_location) > MAX_LOCATION_TEXT_LENGTH:
                raise ValueError("current_location 过长")
        for question in self.questions:
            if not isinstance(question, str) or not question.strip():
                raise ValueError("questions 中的每一项必须是非空字符串")
        if self.reason is not None:
            if not isinstance(self.reason, str) or not self.reason.strip():
                raise ValueError("reason 必须是非空字符串或 null")
            if len(self.reason) > MAX_REASON_LENGTH:
                raise ValueError("reason 过长")


@dataclass(frozen=True)
class UnifiedTurnOutcome:
    """一轮统一反馈的只读结果；result 恒非 None（全部失败时走安全 fallback 重规划）。"""

    applied: bool
    understood: bool
    result: P2AgenticDayResult
    commitment_questions: Tuple[str, ...] = ()
    commitment_warnings: Tuple[str, ...] = ()
    movement_plan: Optional[P3MovementPlan] = None
    movement_blocks: Tuple[object, ...] = ()
    movement_questions: Tuple[str, ...] = ()
    movement_warnings: Tuple[str, ...] = ()
    # Filled only after the feedback location has resolved in this selected
    # campus.  Raw text alone is never a confirmed current-location fact.
    current_location_text: Optional[str] = None


class _CountingCaller(object):
    """统一计数：理解 / 地点解析 / 时间估计 / pipeline 所有模型调用共用。"""

    def __init__(self, caller: AgentCaller):
        if not callable(caller):
            raise TypeError("caller must be callable")
        self.caller = caller
        self.count = 0

    def __call__(self, system, user):
        self.count += 1
        return self.caller(system, user)


def build_unified_feedback_prompt(state: DayPlanningState, user_text: str) -> Tuple[str, str]:
    """构造统一理解 prompt：列出任务/固定安排（模型据此填 ref），schema 与示例在 system。"""
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")
    if not isinstance(user_text, str) or not user_text.strip():
        raise ValueError("user_text must be a non-empty string")
    system = _unified_system_prompt()
    user = (
        "当前时间：{now}\n"
        "day_end：{day_end}\n\n"
        "任务台账：\n{ledger}\n\n"
        "固定安排：\n{commitments}\n\n"
        "最近历史：\n{history}\n\n"
        "用户最新输入：\n{user_text}\n\n"
        "只输出一个符合 schema 的 JSON 对象。"
    ).format(
        now=state.now.strftime("%Y-%m-%d %H:%M"),
        day_end=state.day_end.strftime("%H:%M"),
        ledger=format_task_ledger(state.tasks),
        commitments=format_commitments(state.commitments),
        history=format_history(state.history),
        user_text=user_text,
    )
    return system, user


def _unified_system_prompt() -> str:
    return (
        "你是 CampusFlow 的统一反馈理解器。用户会用自然语言描述对全天计划的调整，"
        "一句话可能同时包含任务更新、固定安排更新和校园移动，你必须一次性全部解析，不能互相吞掉。\n"
        "你只输出结构化 JSON，不执行任何修改；地点只填写用户原话片段，不编造地点。\n"
        "输出 schema：\n"
        '{"schema_version": "p3.unified-feedback.v1",\n'
        ' "task_updates": [任务更新对象, ...],\n'
        ' "commitment_updates": [固定安排更新对象, ...],\n'
        ' "movement": {"has_movement": bool, "origin_text": string|null, '
        '"destination_text": string|null, "mode": "walk"|"bike"|null, '
        '"depart_at": "HH:MM"|null, "arrive_by": "HH:MM"|null},\n'
        ' "current_location": string|null, "questions": [string, ...], "reason": string|null}\n\n'
        "task_updates 每一项（与任务 reconciliation 的 update 一致）：\n"
        '{"target_task_ref": string|null, "new_task_title": string|null,\n'
        ' "progress_delta_minutes": int|null, "set_total_minutes": int|null,\n'
        ' "set_total_source": "user_text"|"ai_estimate"|null,\n'
        ' "lifecycle_action": "none"|"complete"|"skip_today"|"abandon"|"resume_today",\n'
        ' "is_splittable": bool|null, "minimum_slice_minutes": int|null}\n'
        "- 更新已有任务：target_task_ref 填任务台账中的 ref；新增任务：target_task_ref=null 且 new_task_title 非空。\n"
        "- 同一个任务只能出现一条更新；\"我刚又做了30分钟实验\" → progress_delta_minutes=30。\n"
        "- \"今天先不背单词了\" → lifecycle_action=skip_today；\"又想背单词了\" → resume_today。\n\n"
        "commitment_updates 每一项：\n"
        '{"target_commitment_ref": string|null, "action": "delay"|"add"|"cancel"|"update_time",\n'
        ' "title": string|null, "starts_at": "HH:MM"|null, "ends_at": "HH:MM"|null,\n'
        ' "delay_minutes": int|null, "class_arrival_lead_minutes": int|null}\n'
        "- \"下课晚20分钟\" → delay（目标安排 + delay_minutes=20）；\n"
        "- \"组会改到3点\" → update_time（starts_at=\"15:00\"）；\n"
        "- \"组会取消了\" → cancel；\n"
        "- \"14点临时有个组会，大概1小时\" → add（title + starts_at=\"14:00\" + ends_at=\"15:00\"）。\n"
        "- 时间一律 \"HH:MM\"。\n\n"
        "- 课程提前到楼：只有用户明确说提前/准点到时，向目标 class 的 update_time 写 class_arrival_lead_minutes（0-60）；未提及必须为 null，不能擅自恢复默认。可与 starts_at 和 movement 同时填写。\n\n"
        "movement：\n"
        '{"has_movement": bool, "origin_text": string|null, "destination_text": string|null,\n'
        ' "mode": "walk"|"bike"|null, "depart_at": "HH:MM"|null, "arrive_by": "HH:MM"|null}\n'
        "- has_movement=true 时填起点/终点原话；\n"
        "- depart_at：用户明确\"几点出发\"；arrive_by：用户明确\"几点前到\"；都没有则为 null，两者不能同时非 null；\n"
        "- 未明确交通方式 mode=null；\"我骑车去\" → mode=bike。\n"
        "- 不涉及移动时 has_movement=false，movement 其余字段为 null。\n\n"
        "current_location：用户明确说\"我现在在X/我从X出发\"时填 X，没有则为 null。\n\n"
        "questions：只有当某个任务/安排/地点身份或时间确实不确定、必须向用户确认时才写数组；"
        "必须用自然语言人话，不得出现任何内部编号或状态；其余情况保持空数组。\n\n"
        "示例（一条输入同时三类意图）：\n"
        "输入：\"组会改到3点，我从北菜骑车过去，今天先不背单词了\"\n"
        "输出：commitment_updates=[{\"target_commitment_ref\": \"<组会的ref>\", \"action\": \"update_time\", "
        "\"starts_at\": \"15:00\", \"ends_at\": null, \"title\": null, \"delay_minutes\": null}],\n"
        "movement={\"has_movement\": true, \"origin_text\": \"北菜\", \"destination_text\": \"组会\", "
        "\"mode\": \"bike\", \"depart_at\": null, \"arrive_by\": \"15:00\"},\n"
        "task_updates=[{\"target_task_ref\": \"<背单词的ref>\", \"new_task_title\": null, "
        "\"progress_delta_minutes\": null, \"set_total_minutes\": null, \"set_total_source\": null, "
        "\"lifecycle_action\": \"skip_today\", \"is_splittable\": null, \"minimum_slice_minutes\": null}],\n"
        "current_location=\"北菜\", questions=[], reason=null\n"
        "只输出 JSON。"
    )


def parse_unified_feedback(text: str) -> UnifiedFeedback:
    """解析统一反馈输出；内嵌数组复用现有 task/commitment 解析器严格校验。"""
    if not isinstance(text, str) or not text.strip():
        raise AgenticParseError("统一反馈输出为空")
    payload = extract_json_object(text)
    if not isinstance(payload, dict):
        raise AgenticParseError("输出必须是 JSON 对象")
    if payload.get("schema_version") != UNIFIED_FEEDBACK_SCHEMA_VERSION:
        raise AgenticParseError("schema_version 不匹配")

    task_updates = payload.get("task_updates")
    if task_updates is None:
        task_updates = []
    if not isinstance(task_updates, list):
        raise AgenticParseError("task_updates 必须是数组")
    task_result = _parse_task_batch(task_updates)

    commitment_updates = payload.get("commitment_updates")
    if commitment_updates is None:
        commitment_updates = []
    if not isinstance(commitment_updates, list):
        raise AgenticParseError("commitment_updates 必须是数组")
    commitment_result = _parse_commitment_batch(commitment_updates)

    movement_raw = payload.get("movement")
    if movement_raw is not None and not isinstance(movement_raw, dict):
        raise AgenticParseError("movement 必须是对象或 null")
    movement = _parse_movement(movement_raw)

    current_location = payload.get("current_location")
    if current_location is not None and not isinstance(current_location, str):
        raise AgenticParseError("current_location 必须是字符串或 null")

    questions_raw = payload.get("questions")
    if questions_raw is None:
        questions_raw = []
    if not isinstance(questions_raw, list):
        raise AgenticParseError("questions 必须是数组")
    questions = []
    for item in questions_raw:
        if not isinstance(item, str) or not item.strip():
            raise AgenticParseError("questions 中的每一项必须是非空字符串")
        questions.append(item.strip())

    reason = payload.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise AgenticParseError("reason 必须是字符串或 null")

    try:
        return UnifiedFeedback(
            schema_version=UNIFIED_FEEDBACK_SCHEMA_VERSION,
            task_result=task_result,
            commitment_result=commitment_result,
            movement=movement,
            current_location=current_location,
            questions=tuple(questions),
            reason=reason,
        )
    except (TypeError, ValueError) as exc:
        raise AgenticParseError(str(exc))


def _parse_task_batch(items) -> ReconciliationResult:
    """复用 P2c 任务解析器（含同 target 去重 / new_task schema 校验）。"""
    wrapper = json.dumps(
        {
            "schema_version": "p2.task-reconciliation.v1",
            "updates": list(items),
            "questions": [],
        },
        ensure_ascii=False,
    )
    return parse_reconciliation(wrapper)


def _parse_commitment_batch(items) -> CommitmentReconciliationResult:
    """复用 P2d 固定安排解析器（含 action / 时间 / 同 target 去重校验）。"""
    wrapper = json.dumps(
        {
            "schema_version": COMMITMENT_SCHEMA_VERSION,
            "updates": list(items),
            "questions": [],
        },
        ensure_ascii=False,
    )
    return parse_commitment_reconciliation(wrapper)


def _parse_movement(value) -> UnifiedMovement:
    if value is None:
        return UnifiedMovement(False, None, None, None, None, None)
    has_movement = value.get("has_movement")
    if not isinstance(has_movement, bool):
        raise AgenticParseError("movement.has_movement 必须是布尔值")
    if not has_movement:
        return UnifiedMovement(False, None, None, None, None, None)
    origin = value.get("origin_text")
    destination = value.get("destination_text")
    mode = value.get("mode")
    depart_at = value.get("depart_at")
    arrive_by = value.get("arrive_by")
    for name, item in (
        ("origin_text", origin),
        ("destination_text", destination),
        ("mode", mode),
        ("depart_at", depart_at),
        ("arrive_by", arrive_by),
    ):
        if item is not None and not isinstance(item, str):
            raise AgenticParseError("movement.{} 必须是字符串或 null".format(name))
    try:
        return UnifiedMovement(True, origin, destination, mode, depart_at, arrive_by)
    except ValueError as exc:
        raise AgenticParseError(str(exc))


def detect_unified_feedback(
    state: DayPlanningState,
    user_text: str,
    caller: AgentCaller,
    repair_caller: Optional[AgentCaller] = None,
) -> Tuple[Optional[UnifiedFeedback], Optional[str]]:
    """执行统一理解：1 次调用 + 最多 1 次 repair；全部失败返回 (None, warning)。"""
    if not callable(caller):
        raise TypeError("caller must be callable")
    repair = repair_caller if repair_caller is not None else caller
    system, user = build_unified_feedback_prompt(state, user_text)
    try:
        return parse_unified_feedback(caller(system, user)), None
    except AgenticParseError:
        pass
    repair_system, repair_user = build_repair_prompt(
        "unified_feedback", "", UNIFIED_FEEDBACK_SCHEMA_VERSION
    )
    try:
        return parse_unified_feedback(repair(repair_system, repair_user)), None
    except AgenticParseError:
        return None, WARNING_UNIFIED_FAILED


def make_unified_feedback_handler(
    map_data: CampusMapData,
    caller: AgentCaller,
    repair_caller: Optional[AgentCaller] = None,
    config: Optional[object] = None,
    pipeline_kwargs: Optional[Dict] = None,
    location_alias_resolver=None,
) -> Callable[[str, DayPlanningState, Dict], UnifiedTurnOutcome]:
    """构造 session 可注入的统一反馈处理器（闭包保存地图 / caller / 规划参数）。"""
    if not isinstance(map_data, CampusMapData):
        raise TypeError("map_data must be a CampusMapData")
    if not callable(caller):
        raise TypeError("caller must be callable")
    repair = repair_caller if repair_caller is not None else caller
    cfg = config if config is not None else _default_config()
    kwargs = dict(pipeline_kwargs) if pipeline_kwargs else {}

    def handler(user_text, state, movement_data):
        return run_unified_feedback(
            user_text, state, movement_data, map_data, caller, repair, cfg, kwargs,
            location_alias_resolver=location_alias_resolver,
        )

    return handler


def _default_config():
    from src.p2_session import PlanConfig

    return PlanConfig()


def run_unified_feedback(
    user_text: str,
    state: DayPlanningState,
    movement_data: Dict,
    map_data: CampusMapData,
    caller: AgentCaller,
    repair_caller: AgentCaller,
    config: object,
    pipeline_kwargs: Dict,
    location_alias_resolver=None,
) -> UnifiedTurnOutcome:
    """执行一轮统一反馈：理解 -> commitment -> task -> movement -> 统一重规划。

    不修改输入 state；movement_data 可原地更新（current_location / active_blocks）。
    """
    if not isinstance(user_text, str) or not user_text.strip():
        raise ValueError("user_text must be a non-empty string")
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")
    if not isinstance(map_data, CampusMapData):
        raise TypeError("map_data must be a CampusMapData")

    counter = _CountingCaller(caller)
    feedback, understanding_warning = detect_unified_feedback(state, user_text, counter, counter)
    warnings = [w for w in (understanding_warning,) if w]
    movement_blocks = _existing_blocks(movement_data)

    if feedback is None:
        result = _replan(
            reapply_movement_blocks(state, movement_blocks), state, user_text,
            counter, counter.count, pipeline_kwargs, (), tuple(warnings)
        )
        return UnifiedTurnOutcome(
            applied=False, understood=False, result=result,
            movement_blocks=movement_blocks, movement_warnings=tuple(warnings),
        )

    resolved_current_location = None
    if feedback.current_location:
        location_text = (
            location_alias_resolver(feedback.current_location)
            if callable(location_alias_resolver) else feedback.current_location
        )
        resolution = resolve_location(map_data, location_text, counter, counter)
        if resolution.usable and resolution.campus_id == map_data.campus_id:
            # Keep the stable selected-campus display anchor in the movement
            # snapshot.  A personal alias such as “宿舍” must not be sent back
            # through the generic resolver as if it were a map alias.
            resolved_current_location = resolution.display_name
            movement_data["current_location_text"] = resolution.display_name
        else:
            warnings.append("当前位置暂未识别，已保留原有位置与方案。")

    if feedback.questions:
        # 身份/时间仍不确定：本轮不应用任何更新，只透传问题并基于原 state 重规划
        result = _replan(
            reapply_movement_blocks(state, movement_blocks), state, user_text, counter, counter.count,
            pipeline_kwargs, feedback.questions, tuple(warnings),
        )
        return UnifiedTurnOutcome(
            applied=True, understood=True, result=result,
            movement_blocks=movement_blocks,
            current_location_text=resolved_current_location,
        )

    planning_state = state
    commitment_questions = ()
    commitment_warnings = ()

    # 1) commitment updates（程序校验 ref / 时间 / 窗口重派生）
    if feedback.commitment_result.updates:
        applied_c = apply_commitment_reconciliation(
            state,
            feedback.commitment_result,
            config.default_safety_buffer_minutes,
            config.travel_minutes_by_commitment,
            state.history,
        )
        commitment_questions = applied_c.questions
        commitment_warnings = applied_c.warnings
        if applied_c.applied_entries:
            entry = "固定安排更新：{}。".format(";".join(applied_c.applied_entries))
            planning_state = _with_history(
                applied_c.state, append_history(applied_c.state.history, entry)
            )
        elif applied_c.state is not state:
            planning_state = applied_c.state

    # 2) task updates（程序校验 ref / lifecycle / total 安全）
    if feedback.task_result.updates:
        applied_t = apply_reconciliation(planning_state, feedback.task_result)
        warnings.extend(applied_t.warnings)
        if applied_t.applied_entries:
            entry = "任务更新：{}。".format(";".join(applied_t.applied_entries))
            planning_state = _with_history(
                applied_t.state, append_history(applied_t.state.history, entry)
            )
        else:
            planning_state = applied_t.state

    # A commitment update re-derives windows.  Reapply the already confirmed
    # movement occupancy before every allocator path so it remains capacity, not
    # merely a line rendered after task allocation.
    rebuild_request = None
    if not feedback.movement.has_movement and any(
        update.class_arrival_lead_minutes is not None or update.starts_at is not None
        for update in feedback.commitment_result.updates
    ):
        last = movement_data.get("last_request")
        if last is not None:
            commit = _destination_commitment(getattr(last, "destination_text", None), planning_state)
            if commit is not None and commit.starts_at is not None:
                rebuild_request = MovementRequest(
                    last.origin_text, last.destination_text, last.mode,
                    arrive_by=class_arrival_deadline(commit),
                )
    if not feedback.movement.has_movement and rebuild_request is None:
        planning_state = reapply_movement_blocks(planning_state, movement_blocks)

    # 3) movement（有明确时间时按真实路线耗时放置到正确时间段）
    movement_plan = None
    movement_questions = ()
    movement_warnings = ()
    if feedback.movement.has_movement or rebuild_request is not None:
        is_modification = feedback.movement.origin_text is None and feedback.movement.destination_text is None
        if rebuild_request is not None:
            request, build_question = rebuild_request, None
            is_modification = True
        else:
            request, build_question = _build_unified_request(feedback.movement, planning_state, movement_data)
        if request is None:
            movement_questions = (build_question,)
            result = _replan(
                planning_state, state, user_text, counter, counter.count,
                pipeline_kwargs, (), tuple(warnings),
            )
            return UnifiedTurnOutcome(
                applied=True, understood=True, result=result,
                commitment_questions=commitment_questions,
                commitment_warnings=commitment_warnings,
                movement_blocks=movement_blocks,
                movement_questions=movement_questions,
                movement_warnings=tuple(warnings),
            )

        movement_base = planning_state
        if is_modification:
            last_request = movement_data.get("last_request")
            timed_replay = bool(
                last_request is not None
                and (
                    getattr(last_request, "arrive_by", None) is not None
                    or getattr(last_request, "depart_at", None) is not None
                )
            )
            if timed_replay:
                # 时间锚点移动（含 intake 生成的 arrive_by 移动）重放是幂等的：
                # 直接在当前（含同轮 task/commitment 更新）state 上重放，不丢进度。
                movement_base = planning_state
            else:
                base_state, base_question = _resolve_change_base(state, movement_data)
                if base_state is None:
                    movement_questions = (base_question,)
                    result = _replan(
                        planning_state, state, user_text, counter, counter.count,
                        pipeline_kwargs, (), tuple(warnings),
                    )
                    return UnifiedTurnOutcome(
                        applied=True, understood=True, result=result,
                        commitment_questions=commitment_questions,
                        commitment_warnings=commitment_warnings,
                        movement_blocks=movement_blocks,
                        movement_questions=movement_questions,
                        movement_warnings=tuple(warnings),
                    )
                movement_base = base_state

        plan = plan_day_with_movements(
            movement_base, map_data, [request], counter, counter, counter
        )
        movement_questions = tuple(plan.questions)
        movement_warnings = tuple(plan.warnings)
        if plan.movements:
            merged = _merge_blocks(movement_blocks, plan.movements, is_modification)
            movement_data["active_blocks"] = merged
            movement_data["last_request"] = request
            movement_data["base_state"] = movement_base
            movement_data["last_plan_state"] = plan.state
            movement_data["last_plan"] = plan
            result = _synthesize_movement_result(
                state, plan, counter.count, tuple(plan.questions),
                tuple(plan.warnings) + tuple(warnings),
            )
            return UnifiedTurnOutcome(
                applied=True, understood=True, result=result,
                commitment_questions=commitment_questions,
                commitment_warnings=commitment_warnings,
                movement_plan=plan,
                movement_blocks=merged,
                movement_questions=movement_questions,
                movement_warnings=movement_warnings,
            )

    # 4) 无移动 / 移动未应用：基于最新 state 统一重规划（P2c pipeline，不含 reconciliation）
    result = _replan(
        planning_state, state, user_text, counter, counter.count,
        pipeline_kwargs, (), tuple(warnings), movement_blocks=movement_blocks,
    )
    return UnifiedTurnOutcome(
        applied=True, understood=True, result=result,
        commitment_questions=commitment_questions,
        commitment_warnings=commitment_warnings,
        movement_plan=movement_plan,
        movement_blocks=movement_blocks,
        movement_questions=movement_questions,
        movement_warnings=movement_warnings,
        current_location_text=resolved_current_location,
    )


def _build_unified_request(
    movement: UnifiedMovement, state: DayPlanningState, movement_data: Dict
) -> Tuple[Optional[MovementRequest], Optional[str]]:
    """把统一移动意图转成 MovementRequest；缺起点/终点时返回 (None, 人话问题)。

    终点若命中某固定安排标题，则使用该安排的 location_text 作为真实目的地
    （例如“去组会” -> 组会的 location），由程序校验真实 P3 节点。
    """
    origin = movement.origin_text
    destination = _commitment_location_destination(movement.destination_text, state)
    last = movement_data.get("last_request")
    if not origin and last is not None:
        origin = getattr(last, "origin_text", None)
    if not destination and last is not None:
        destination = getattr(last, "destination_text", None)
    if not origin:
        origin = movement_data.get("current_location_text")
    if not origin or not destination:
        return None, "从哪里出发、要去哪里？请说明移动路线。"
    mode = movement.mode
    if mode is None and last is not None:
        mode = getattr(last, "mode", None)
    if mode is None:
        mode = "walk"
    depart_at = _parse_hhmm(movement.depart_at, state.now) if movement.depart_at else None
    arrive_by = _parse_hhmm(movement.arrive_by, state.now) if movement.arrive_by else None
    if depart_at is None and arrive_by is None and last is not None:
        # 继承最近一次移动的时间锚点；若终点对应某个固定安排，则跟随该安排当前
        # （可能刚被同轮 commitment 更新）的开始时间，避免沿用旧的到达时间。
        commit = _destination_commitment(destination, state)
        if commit is not None and commit.starts_at is not None:
            arrive_by = class_arrival_deadline(commit)
        else:
            depart_at = getattr(last, "depart_at", None)
            arrive_by = getattr(last, "arrive_by", None)
    try:
        request = MovementRequest(origin, destination, mode, depart_at=depart_at, arrive_by=arrive_by)
    except (TypeError, ValueError):
        return None, "移动路线信息不完整，请重新说明起点、终点与交通方式。"
    return request, None


def _destination_commitment(destination_text: Optional[str], state: DayPlanningState):
    """按终点文本定位固定安排：优先 location_text，其次标题（程序校验，不编造）。"""
    if not destination_text:
        return None
    for commitment in state.commitments:
        if commitment.location_text and commitment.location_text.strip() in destination_text:
            return commitment
    for commitment in state.commitments:
        if commitment.title and commitment.title.strip() in destination_text:
            return commitment
    return None


def _commitment_location_destination(
    destination_text: Optional[str], state: DayPlanningState
) -> Optional[str]:
    """终点文本命中固定安排标题时，改用其真实 location_text（程序校验，不编造）。"""
    if not destination_text:
        return destination_text
    for commitment in state.commitments:
        if commitment.title and commitment.title.strip() in destination_text:
            if commitment.location_text:
                return commitment.location_text
    return destination_text


def _parse_hhmm(value: str, now: datetime) -> Optional[datetime]:
    match = _TIME_RE.match(value)
    if match is None:
        return None
    return now.replace(hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0)


def _resolve_change_base(
    state: DayPlanningState, movement_data: Dict
) -> Tuple[Optional[DayPlanningState], Optional[str]]:
    """修改最近一次移动时基于其基础 state 重规划，防止重复扣容量。"""
    base = movement_data.get("base_state")
    last_plan_state = movement_data.get("last_plan_state")
    if base is None or last_plan_state is None or last_plan_state != state:
        return None, "最近的移动安排已被其他反馈更新，无法直接修改，请重新说明移动路线。"
    return base, None


def _merge_blocks(
    existing: Tuple[object, ...], new_blocks: Tuple[object, ...], is_modification: bool
) -> Tuple[object, ...]:
    if is_modification:
        previous = list(existing)
        if not previous:
            return tuple(new_blocks)
        return tuple(previous[:-1]) + tuple(new_blocks)
    return tuple(existing) + tuple(new_blocks)


def _existing_blocks(movement_data: Dict) -> Tuple[object, ...]:
    blocks = movement_data.get("active_blocks", ())
    if not isinstance(blocks, tuple):
        return ()
    return blocks


def _replan(
    planning_state: DayPlanningState,
    original_state: DayPlanningState,
    user_text: str,
    counter: _CountingCaller,
    prefix_calls: int,
    pipeline_kwargs: Dict,
    extra_questions: Tuple[str, ...],
    extra_warnings: Tuple[str, ...],
    movement_blocks: Tuple[object, ...] = (),
) -> P2AgenticDayResult:
    """基于给定 state 重新执行 P2c 规划（跳过 reconciliation），并统一计数。"""
    budget = max(1, MAX_CALLS_PER_ROUND - prefix_calls)
    pipeline_result = run_p2_agentic_day_planning(
        planning_state,
        user_text,
        counter,
        skip_reconciliation=True,
        max_calls_per_round=budget,
        **pipeline_kwargs,
    )
    total_calls = prefix_calls + pipeline_result.call_count
    if total_calls > MAX_CALLS_PER_ROUND:
        raise RuntimeError("unified feedback call budget exceeded: {}".format(total_calls))
    result = P2AgenticDayResult(
        original_state=original_state,
        updated_state=pipeline_result.updated_state,
        reconciliation_result=None,
        day_plan_intent=pipeline_result.day_plan_intent,
        allocation_plan=pipeline_result.allocation_plan,
        day_summary=pipeline_result.day_summary,
        review_result=pipeline_result.review_result,
        revision_used=pipeline_result.revision_used,
        call_count=total_calls,
        warnings=pipeline_result.warnings + tuple(extra_warnings),
        questions=pipeline_result.questions + tuple(extra_questions),
    )
    overlap_errors = final_plan_overlap_errors(
        result.updated_state, result.allocation_plan, movement_blocks
    )
    if overlap_errors:
        # This is an interim P3 snapshot.  P4's assumed-location correction
        # immediately rebuilds it from the saved pre-movement base; raising
        # here prevented that safe rebuild from ever running.  The final P4
        # renderer still fail-closes on the same invariant.
        result = P2AgenticDayResult(
            original_state=result.original_state,
            updated_state=result.updated_state,
            reconciliation_result=result.reconciliation_result,
            day_plan_intent=result.day_plan_intent,
            allocation_plan=result.allocation_plan,
            day_summary=result.day_summary,
            review_result=result.review_result,
            revision_used=result.revision_used,
            call_count=result.call_count,
            warnings=result.warnings + ("中间路线快照需要重建。",),
            questions=result.questions,
        )
    return result


def _synthesize_movement_result(
    original_state: DayPlanningState,
    plan: P3MovementPlan,
    call_count: int,
    questions: Tuple[str, ...],
    warnings: Tuple[str, ...],
) -> P2AgenticDayResult:
    """把统一移动计划转成会话可提交的结果快照（不含 raw model JSON）。"""
    if isinstance(call_count, bool) or not isinstance(call_count, int):
        raise ValueError("call_count must be an integer")
    if call_count > MAX_CALLS_PER_ROUND:
        raise RuntimeError("unified movement call budget exceeded: {}".format(call_count))
    return P2AgenticDayResult(
        original_state=original_state,
        updated_state=plan.state,
        reconciliation_result=None,
        day_plan_intent=None,
        allocation_plan=plan.allocation_plan,
        day_summary=summarize_day_plan(plan.allocation_plan, plan.state),
        review_result=None,
        revision_used=False,
        call_count=call_count,
        warnings=tuple(warnings),
        questions=tuple(questions),
    )


def _with_history(state: DayPlanningState, history: Tuple[str, ...]) -> DayPlanningState:
    return DayPlanningState(
        reference_datetime=state.reference_datetime,
        now=state.now,
        day_end=state.day_end,
        commitments=state.commitments,
        tasks=state.tasks,
        windows=state.windows,
        active_window_ref=state.active_window_ref,
        unresolved_commitment_refs=state.unresolved_commitment_refs,
        history=history,
    )
