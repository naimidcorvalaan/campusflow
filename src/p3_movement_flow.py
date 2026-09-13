"""P3e 移动接入会话的编排层（Python 3.8 兼容，全部 mock 可测）。

数据流：用户反馈 -> Qwen 移动意图（add/change_*）-> P3 地点解析
（exact alias 优先，Qwen 兜底，程序校验真实 node）-> 本地真实寻路
-> Qwen 时间估计（AI暂估，程序钳制）-> 扣除窗口容量 -> 确定性重分配
-> 生成 P2AgenticDayResult 快照，由 P2SessionController 提交。

安全边界：
- 模型只能输出意图/地点原话/方式，不能直接写 state；
- 未解析地点 / 无真实路线 / 无足够窗口时绝不编造移动；
- 每次反馈的模型调用进入同一计数，合成结果 call_count <= 10；
- 输入 state 永不修改，全部返回新实例。
"""

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from src.p2_agentic_models import MAX_CALLS_PER_ROUND, P2AgenticDayResult
from src.p2_day_plan import summarize_day_plan
from src.p2_models import DayPlanningState
from src.p3_map_schema import CampusMapData
from src.p3_movement_intent import (
    MOVEMENT_INTENT_SCHEMA_VERSION,
    MovementIntent,
    MovementIntentAction,
    detect_movement_intent,
)
from src.p3_route_planner import (
    MovementBlock,
    MovementRequest,
    P3MovementPlan,
    plan_day_with_movements,
)

AgentCaller = Callable[[str, str], str]


@dataclass(frozen=True)
class MovementTurnOutcome:
    """一轮移动反馈的只读结果。applied=True 时 result/plan/blocks 必填。"""

    applied: bool
    intent_present: bool
    plan: Optional[P3MovementPlan]
    result: Optional[P2AgenticDayResult]
    blocks: Tuple[MovementBlock, ...]
    questions: Tuple[str, ...]
    warnings: Tuple[str, ...]


class _CountingCaller(object):
    """给所有移动流程模型调用统一计数（含 repair），用于合成 result.call_count。"""

    def __init__(self, caller: AgentCaller):
        if not callable(caller):
            raise TypeError("caller must be callable")
        self.caller = caller
        self.count = 0

    def __call__(self, system, user):
        self.count += 1
        return self.caller(system, user)


def make_movement_handler(
    map_data: CampusMapData,
    caller: AgentCaller,
    repair_caller: Optional[AgentCaller] = None,
) -> Callable[[str, DayPlanningState, Dict], MovementTurnOutcome]:
    """构造 session 可注入的移动处理器（闭包，保存地图与 caller）。"""
    if not isinstance(map_data, CampusMapData):
        raise TypeError("map_data must be a CampusMapData")
    repair = repair_caller if repair_caller is not None else caller

    def handler(user_text, state, movement_data):
        return handle_movement_feedback(user_text, state, movement_data, map_data, caller, repair)

    return handler


def handle_movement_feedback(
    user_text: str,
    state: DayPlanningState,
    movement_data: Dict,
    map_data: CampusMapData,
    caller: AgentCaller,
    repair_caller: AgentCaller,
) -> MovementTurnOutcome:
    """执行一轮移动反馈；不修改输入 state，movement_data 可原地更新。"""
    if not isinstance(user_text, str) or not user_text.strip():
        raise ValueError("user_text must be a non-empty string")
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")
    if not isinstance(map_data, CampusMapData):
        raise TypeError("map_data must be a CampusMapData")

    counter = _CountingCaller(caller)
    intent, intent_warning = detect_movement_intent(user_text, counter, counter)
    warnings = [w for w in (intent_warning,) if w]

    if not intent.has_movement:
        return MovementTurnOutcome(
            applied=False,
            intent_present=False,
            plan=None,
            result=None,
            blocks=_existing_blocks(movement_data),
            questions=(),
            warnings=tuple(warnings),
        )

    if intent.action == MovementIntentAction.NONE.value:
        return MovementTurnOutcome(
            applied=False,
            intent_present=True,
            plan=None,
            result=None,
            blocks=_existing_blocks(movement_data),
            questions=(),
            warnings=tuple(warnings),
        )

    request, question = _build_request(intent, movement_data)
    if request is None:
        return MovementTurnOutcome(
            applied=False,
            intent_present=True,
            plan=None,
            result=None,
            blocks=_existing_blocks(movement_data),
            questions=(question,),
            warnings=tuple(warnings),
        )

    base_state, question = _resolve_base_state(intent.action, state, movement_data)
    if base_state is None:
        return MovementTurnOutcome(
            applied=False,
            intent_present=True,
            plan=None,
            result=None,
            blocks=_existing_blocks(movement_data),
            questions=(question,),
            warnings=tuple(warnings),
        )

    plan = plan_day_with_movements(
        base_state,
        map_data,
        [request],
        location_caller=counter,
        location_repair_caller=counter,
        time_caller=counter,
    )

    if not plan.movements:
        return MovementTurnOutcome(
            applied=False,
            intent_present=True,
            plan=plan,
            result=None,
            blocks=_existing_blocks(movement_data),
            questions=tuple(plan.questions),
            warnings=tuple(plan.warnings) + tuple(warnings),
        )

    merged_blocks = _merge_blocks(movement_data.get("active_blocks", ()), plan.movements, intent.action)
    movement_data["active_blocks"] = merged_blocks
    movement_data["last_request"] = request
    movement_data["base_state"] = base_state
    movement_data["last_plan_state"] = plan.state
    movement_data["last_plan"] = plan
    if intent.action == MovementIntentAction.ADD.value:
        movement_data["current_location_text"] = request.origin_text

    result = _synthesize_result(
        state, plan, counter.count, tuple(plan.questions), tuple(plan.warnings) + tuple(warnings)
    )
    return MovementTurnOutcome(
        applied=True,
        intent_present=True,
        plan=plan,
        result=result,
        blocks=merged_blocks,
        questions=tuple(plan.questions),
        warnings=tuple(plan.warnings) + tuple(warnings),
    )


def _build_request(
    intent: MovementIntent, movement_data: Dict
) -> Tuple[Optional[MovementRequest], Optional[str]]:
    """把意图转成 MovementRequest；字段缺失时返回 (None, 人话问题)。"""
    action = intent.action
    if action == MovementIntentAction.ADD.value:
        origin = intent.origin_text or movement_data.get("current_location_text")
        if not origin:
            return None, "从哪里出发？请说明起点。"
        if not intent.destination_text:
            return None, "要去哪里？请说明终点。"
        mode = intent.mode if intent.mode is not None else "walk"
        return MovementRequest(origin, intent.destination_text, mode), None

    last = movement_data.get("last_request")
    if last is None or not isinstance(last, MovementRequest):
        return None, "最近没有可修改的移动安排，请直接说明新的移动路线。"

    if action == MovementIntentAction.CHANGE_MODE.value:
        return MovementRequest(last.origin_text, last.destination_text, intent.mode), None
    if action == MovementIntentAction.CHANGE_ORIGIN.value:
        return MovementRequest(intent.origin_text, last.destination_text, last.mode), None
    if action == MovementIntentAction.CHANGE_DESTINATION.value:
        return MovementRequest(last.origin_text, intent.destination_text, last.mode), None
    return None, "未识别的移动意图。"


def _resolve_base_state(
    action: str, state: DayPlanningState, movement_data: Dict
) -> Tuple[Optional[DayPlanningState], Optional[str]]:
    """ADD 用当前 state；CHANGE_* 用最近一次移动前的基础 state（防止重复扣容量）。"""
    if action == MovementIntentAction.ADD.value:
        return state, None
    base = movement_data.get("base_state")
    last_plan_state = movement_data.get("last_plan_state")
    if base is None or last_plan_state is None or last_plan_state != state:
        return None, "最近的移动安排已被其他反馈更新，无法直接修改，请重新说明移动路线。"
    return base, None


def _merge_blocks(
    existing: Tuple[MovementBlock, ...], new_blocks: Tuple[MovementBlock, ...], action: str
) -> Tuple[MovementBlock, ...]:
    """ADD 追加新块；CHANGE_* 用重规划后的块替换最后一个旧块。"""
    if action == MovementIntentAction.ADD.value:
        return tuple(existing) + tuple(new_blocks)
    previous = list(existing)
    if not previous:
        return tuple(new_blocks)
    return tuple(previous[:-1]) + tuple(new_blocks)


def _existing_blocks(movement_data: Dict) -> Tuple[MovementBlock, ...]:
    blocks = movement_data.get("active_blocks", ())
    if not isinstance(blocks, tuple):
        return ()
    return blocks


def _synthesize_result(
    original_state: DayPlanningState,
    plan: P3MovementPlan,
    call_count: int,
    questions: Tuple[str, ...],
    warnings: Tuple[str, ...],
) -> P2AgenticDayResult:
    """把移动计划转成会话可提交的结果快照（不含 raw model JSON）。"""
    if isinstance(call_count, bool) or not isinstance(call_count, int):
        raise ValueError("call_count must be an integer")
    if call_count > MAX_CALLS_PER_ROUND:
        raise RuntimeError("movement call budget exceeded: {}".format(call_count))
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
