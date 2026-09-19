"""P3c 真实路线接入 P2：移动请求 -> 地点解析 -> 真实路线 -> 时间估计 -> 扣容量重规划。

原则（Python 3.8 兼容，全部 mock 可测）：
- 路线距离来自 P3 route provider（程序事实），Qwen 不得修改；
- 移动耗时标记 AI_ESTIMATED，程序兜底时明确 method=program_fallback；
- 地点未解析 / 无路线时不编造节点、不冒充 REAL_MAP；
- 移动分钟数真正扣除对应 DayWindow 容量并参与 P2 allocator 重规划；
- 页面渲染不输出内部 ref / enum / dataclass repr。
"""

import re
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Callable, Optional, Sequence, Tuple

from src.p2_allocator import allocate_tasks_across_windows
from src.p2_day_plan import compact_plan_lines, no_current_task_line
from src.p2_models import DayPlanningState, DayWindow
from src.p2_window_derivation import derive_active_window_ref
from src.p3_location_resolver import (
    LocationResolution,
    LocationResolutionStatus,
    resolve_location,
)
from src.p3_map_schema import CampusMapData, RouteResult, RouteStatus, TransportMode, parse_transport_mode
from src.p3_campus_scope import CrossCampusRouteUnsupportedError
from src.p3_route_provider import plan_route
from src.p3_time_estimator import (
    TravelTimeEstimate,
    TimeEstimateMethod,
    estimate_travel_time_named,
)
from src.p3_class_prep import class_arrival_deadline, class_prep_interval

AgentCaller = Callable[[str, str], str]

# 明确产品规则：任何真实地点切换，移动开始前默认预留 5 分钟“收拾东西”。
# 只在 origin != destination（真实移动）时应用；由程序执行，不由 Qwen 判断。
TRANSITION_BUFFER_MINUTES = 5
_TRANSITION_LABEL = "收拾东西"

# 确定性校园骑行高峰时段（每天固定，左闭右开：08:10 <= t < 08:30 ...）。
# 只影响 bike：Qwen / Travel Time Agent 先估计正常骑行 base 时长，程序再判断
# 实际骑行区间是否与任一高峰窗口重叠；重叠则 effective = base * 2，否则保持 base。
# walk 不受影响；高峰倍率只作用于 travel duration，5 分钟 transition buffer 不乘 2。
BIKE_PEAK_WINDOWS = (
    (time(8, 10), time(8, 30)),
    (time(10, 5), time(10, 25)),
    (time(12, 0), time(12, 20)),
    (time(13, 10), time(13, 30)),
    (time(15, 5), time(15, 25)),
    (time(17, 0), time(17, 20)),
)


def bike_peak_overlap(start: datetime, end: datetime) -> bool:
    """骑行区间 [start, end) 是否与任一高峰窗口交集非空（左闭右开）。"""
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise TypeError("start/end must be datetime")
    if start >= end:
        return False
    for win_start, win_end in BIKE_PEAK_WINDOWS:
        s = start.time()
        e = end.time()
        if s < win_end and win_start < e:
            return True
    return False


def _is_bike_mode(mode: object) -> bool:
    if mode is None:
        return False
    try:
        return parse_transport_mode(mode) is TransportMode.BIKE
    except ValueError:
        return False


def effective_travel_minutes(
    mode: object, base_minutes: int, interval_start: datetime, interval_end: datetime
) -> int:
    """高峰规则：bike 且骑行区间与高峰重叠时有效时长翻倍；walk 不翻倍。"""
    if isinstance(base_minutes, bool) or not isinstance(base_minutes, int):
        raise ValueError("base_minutes must be an integer")
    if base_minutes <= 0:
        return base_minutes
    if not _is_bike_mode(mode):
        return base_minutes
    if bike_peak_overlap(interval_start, interval_end):
        return base_minutes * 2
    return base_minutes

_DESTINATION_RE = re.compile(r"去([^\s，。,\.！？!?、]+)")


class MovementStatus(str, Enum):
    OK = "ok"
    TRIVIAL = "trivial"  # 起点 == 终点，无需移动
    LOCATION_UNRESOLVED = "location_unresolved"
    NO_ROUTE = "no_route"


@dataclass(frozen=True)
class MovementRequest:
    """一次移动请求：起点/终点为自然语言地点，mode 为 walk/bike。

    depart_at / arrive_by 为明确时间移动意图（程序解析后的 datetime）：
    - depart_at：用户明确“几点出发”，移动从该时刻开始；
    - arrive_by：用户明确“几点前到”，程序根据估计耗时倒推出发区间；
    两者互斥，均未提供时保持“下一可用窗口起点”的既有行为。
    """

    origin_text: str
    destination_text: str
    mode: object
    depart_at: Optional[datetime] = None
    arrive_by: Optional[datetime] = None

    def __post_init__(self):
        if not isinstance(self.origin_text, str) or not self.origin_text.strip():
            raise ValueError("origin_text must be non-empty")
        if not isinstance(self.destination_text, str) or not self.destination_text.strip():
            raise ValueError("destination_text must be non-empty")
        parse_transport_mode(self.mode)
        if self.depart_at is not None and not isinstance(self.depart_at, datetime):
            raise ValueError("depart_at must be a datetime or None")
        if self.arrive_by is not None and not isinstance(self.arrive_by, datetime):
            raise ValueError("arrive_by must be a datetime or None")
        if self.depart_at is not None and self.arrive_by is not None:
            raise ValueError("depart_at and arrive_by are mutually exclusive")


@dataclass(frozen=True)
class ResolvedMovement:
    """一次移动的解析结果（含真实路线与时间估计）。"""

    origin_resolution: LocationResolution
    destination_resolution: LocationResolution
    mode: TransportMode
    status: MovementStatus
    route: Optional[RouteResult]
    time_estimate: Optional[TravelTimeEstimate]
    questions: Tuple[str, ...]
    warnings: Tuple[str, ...]
    depart_at: Optional[datetime] = None
    arrive_by: Optional[datetime] = None


@dataclass(frozen=True)
class MovementBlock:
    """已进入计划的移动块：占用某个窗口起始的 estimated_minutes 分钟。"""

    window_ref: str
    window_start: datetime
    origin_text: str
    destination_text: str
    origin_node_id: str
    destination_node_id: str
    origin_name: str
    destination_name: str
    mode: TransportMode
    distance_m: int
    estimated_minutes: int
    low_minutes: int
    high_minutes: int
    method: TimeEstimateMethod
    approximate: bool
    destination_short_name: Optional[str] = None
    transition_minutes: int = 0
    peak_bike: bool = False
    # Formal execution-sequence linkage.  Existing P3 movement blocks remain
    # compatible because both fields are optional.
    origin_activity_ref: Optional[str] = None
    destination_activity_ref: Optional[str] = None

    @property
    def end_time(self) -> datetime:
        return self.window_start + timedelta(minutes=self.estimated_minutes)

    @property
    def transition_start(self) -> Optional[datetime]:
        """“收拾东西”buffer 起点；无 buffer 时返回 None。"""
        if self.transition_minutes <= 0:
            return None
        return self.window_start - timedelta(minutes=self.transition_minutes)


@dataclass(frozen=True)
class P3MovementPlan:
    """路线增强的当日计划：移动块 + P2 确定性分配 + 调整后的 state。"""

    movements: Tuple[MovementBlock, ...]
    unhandled: Tuple[ResolvedMovement, ...]
    allocation_plan: object
    state: DayPlanningState
    questions: Tuple[str, ...]
    warnings: Tuple[str, ...]


@dataclass(frozen=True)
class SpatialIntakeOutcome:
    """首次全天计划的空间增强结果：移动块 + 调整后的 state（不新增任务/安排）。"""

    state: DayPlanningState
    blocks: Tuple[MovementBlock, ...]
    questions: Tuple[str, ...]
    warnings: Tuple[str, ...]
    current_location_text: Optional[str]
    call_count: int
    requests: Tuple[MovementRequest, ...] = ()


class _IntakeCounter(object):
    """run_spatial_intake 内部模型调用计数（地点解析 + 时间估计）。"""

    def __init__(self, caller: AgentCaller):
        if not callable(caller):
            raise TypeError("caller must be callable")
        self.caller = caller
        self.count = 0

    def __call__(self, system, user):
        self.count += 1
        return self.caller(system, user)


def run_spatial_intake(
    state: DayPlanningState,
    current_location_text: Optional[str],
    transport_mode: Optional[str],
    map_data: CampusMapData,
    caller: AgentCaller,
    repair_caller: Optional[AgentCaller] = None,
) -> SpatialIntakeOutcome:
    """首次全天计划进入 P3 空间理解：解析当前位置与安排地点 -> 真实移动进入首版计划。

    - current_location 与带 location 的固定安排（按时间排序）之间生成必要移动，
      明确时间用 arrive_by=下一安排开始时间；
    - 地点全部未知 / 无安排地点时不产生移动，保持原 P2 intake state；
    - 未知地点只产生人话待确认问题，不阻断 intake，不新增任务/固定安排；
    - 输入 state 永不修改；返回新 state（移动已扣窗口容量）。
    """
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")
    if not isinstance(map_data, CampusMapData):
        raise TypeError("map_data must be a CampusMapData")
    if not callable(caller):
        raise TypeError("caller must be callable")
    repair = repair_caller if repair_caller is not None else caller
    counter = _IntakeCounter(caller)

    mode = _parse_intake_mode(transport_mode)
    requests, build_questions = _build_intake_movement_requests(state, current_location_text, mode)
    if not requests:
        return SpatialIntakeOutcome(
            state=state,
            blocks=(),
            questions=tuple(build_questions),
            warnings=(),
            current_location_text=current_location_text,
            call_count=counter.count,
            requests=tuple(requests),
        )

    plan = plan_day_with_movements(state, map_data, requests, counter, counter, counter)
    return SpatialIntakeOutcome(
        state=plan.state,
        blocks=tuple(plan.movements),
        questions=tuple(plan.questions) + tuple(build_questions),
        warnings=tuple(plan.warnings),
        current_location_text=current_location_text,
        call_count=counter.count,
        requests=tuple(requests),
    )


def _parse_intake_mode(transport_mode: Optional[str]) -> TransportMode:
    if transport_mode is None:
        return TransportMode.WALK
    try:
        return parse_transport_mode(transport_mode)
    except ValueError:
        return TransportMode.WALK


def _build_intake_movement_requests(
    state: DayPlanningState,
    current_location_text: Optional[str],
    mode: TransportMode,
) -> Tuple[Sequence[MovementRequest], Sequence[str]]:
    """当前位置 -> 首个带地点的安排；相邻带地点安排之间也生成必要移动。"""
    located = [
        commitment
        for commitment in state.commitments
        if commitment.location_text and commitment.starts_at is not None
    ]
    located.sort(key=lambda commitment: commitment.starts_at)
    requests = []
    questions = []
    if not located:
        return requests, questions
    if current_location_text:
        first = located[0]
        requests.append(
            MovementRequest(current_location_text, first.location_text, mode, arrive_by=class_arrival_deadline(first))
        )
    for previous, nxt in zip(located, located[1:]):
        requests.append(
            MovementRequest(previous.location_text, nxt.location_text, mode, arrive_by=class_arrival_deadline(nxt))
        )
    return requests, questions


def suggest_movement_mode(text: str) -> Optional[TransportMode]:
    """从用户文本中提取交通方式（简单关键词，不做位置 fuzzy）。"""
    if not isinstance(text, str):
        return None
    if any(token in text for token in ("骑车", "骑行", "自行车")):
        return TransportMode.BIKE
    if any(token in text for token in ("步行", "走路")):
        return TransportMode.WALK
    return None


def extract_destination_text(text: str) -> Optional[str]:
    """最小意图提取：'去X' 中的 X（demo/测试用；正式意图抽取由后续 Agent 负责）。"""
    if not isinstance(text, str):
        return None
    match = _DESTINATION_RE.search(text)
    if match is None:
        return None
    return match.group(1).strip()


def plan_movement(
    map_data: CampusMapData,
    origin_res: LocationResolution,
    destination_res: LocationResolution,
    mode: object,
    time_caller: Optional[AgentCaller] = None,
) -> ResolvedMovement:
    """对已解析的起终点执行真实寻路 + 时间估计。"""
    parsed_mode = parse_transport_mode(mode)
    _require_resolutions_in_map(map_data, origin_res, destination_res)
    if not origin_res.usable or not destination_res.usable:
        questions = []
        for res in (origin_res, destination_res):
            if res.question:
                questions.append(res.question)
        if not questions:
            questions.append("起点或终点暂无法确定，请补充说明。")
        return ResolvedMovement(
            origin_resolution=origin_res,
            destination_resolution=destination_res,
            mode=parsed_mode,
            status=MovementStatus.LOCATION_UNRESOLVED,
            route=None,
            time_estimate=None,
            questions=tuple(questions),
            warnings=(),
        )
    if origin_res.node_id == destination_res.node_id:
        return ResolvedMovement(
            origin_resolution=origin_res,
            destination_resolution=destination_res,
            mode=parsed_mode,
            status=MovementStatus.TRIVIAL,
            route=None,
            time_estimate=None,
            questions=(),
            warnings=("起点与终点相同，无需移动：{}".format(origin_res.display_name),),
        )
    route = plan_route(map_data, origin_res.node_id, destination_res.node_id, parsed_mode)
    if route.status is not RouteStatus.COMPUTED or route.total_distance_m is None:
        return ResolvedMovement(
            origin_resolution=origin_res,
            destination_resolution=destination_res,
            mode=parsed_mode,
            status=MovementStatus.NO_ROUTE,
            route=route,
            time_estimate=None,
            questions=(),
            warnings=("暂无可用路线：{} 到 {}".format(
                origin_res.display_name or origin_res.raw_text,
                destination_res.display_name or destination_res.raw_text,
            ),),
        )
    estimate = estimate_travel_time_named(
        origin_res.display_name or origin_res.raw_text,
        destination_res.display_name or destination_res.raw_text,
        route.total_distance_m,
        parsed_mode,
        time_caller,
    )
    return ResolvedMovement(
        origin_resolution=origin_res,
        destination_resolution=destination_res,
        mode=parsed_mode,
        status=MovementStatus.OK,
        route=route,
        time_estimate=estimate,
        questions=(),
        warnings=(),
    )


def _require_resolutions_in_map(
    map_data: CampusMapData,
    origin_res: LocationResolution,
    destination_res: LocationResolution,
) -> None:
    """Reject cross-map resolved node references before Dijkstra starts."""
    campus_id = map_data.campus_id
    scoped = (origin_res.campus_id, destination_res.campus_id)
    if campus_id is not None and any(item is not None and item != campus_id for item in scoped):
        raise CrossCampusRouteUnsupportedError("当前不支持跨校区路线")
    if (
        origin_res.campus_id is not None
        and destination_res.campus_id is not None
        and origin_res.campus_id != destination_res.campus_id
    ):
        raise CrossCampusRouteUnsupportedError("当前不支持跨校区路线")


def plan_day_with_movements(
    state: DayPlanningState,
    map_data: CampusMapData,
    movements: Sequence[MovementRequest],
    location_caller: Optional[AgentCaller] = None,
    location_repair_caller: Optional[AgentCaller] = None,
    time_caller: Optional[AgentCaller] = None,
    include_low_attention: bool = False,
) -> P3MovementPlan:
    """地点解析 + 真实路线 + 时间估计 -> 移动占用窗口容量 -> P2 allocator 重规划。"""
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")
    if not isinstance(map_data, CampusMapData):
        raise TypeError("map_data must be a CampusMapData")
    if not isinstance(include_low_attention, bool):
        raise ValueError("include_low_attention must be a bool")

    resolved: list = []
    for request in movements:
        if not isinstance(request, MovementRequest):
            raise TypeError("movements must contain MovementRequest")
        origin_res = resolve_location(map_data, request.origin_text, location_caller, location_repair_caller)
        destination_res = resolve_location(
            map_data, request.destination_text, location_caller, location_repair_caller
        )
        resolved.append(
            replace(
                plan_movement(map_data, origin_res, destination_res, request.mode, time_caller),
                depart_at=request.depart_at,
                arrive_by=request.arrive_by,
            )
        )
    return _build_plan_from_resolved(state, tuple(resolved), include_low_attention, map_data)


def replan_after_movements(
    state: DayPlanningState,
    resolved_movements: Sequence[ResolvedMovement],
    include_low_attention: bool = False,
    map_data: Optional[CampusMapData] = None,
) -> P3MovementPlan:
    """使用已解析（含时间估计）的移动结果重规划；本函数不调用任何模型，供缓存/rerun 复用。"""
    return _build_plan_from_resolved(
        state, tuple(resolved_movements), include_low_attention, map_data
    )


def reapply_movement_blocks(
    state: DayPlanningState, blocks: Sequence[MovementBlock]
) -> DayPlanningState:
    """Replay already-confirmed P3 movement occupancy onto a freshly-derived state.

    Commitment reconciliation intentionally derives its windows again.  A movement
    block is nevertheless a program fact, not render-only decoration: its packing
    buffer and travel interval must be taken out of the new allocator capacity.
    Replaying the recorded effective duration avoids a second peak multiplier or
    a second model call.
    """
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")

    adjusted = state
    ordered = sorted(
        (block for block in (blocks or ()) if isinstance(block, MovementBlock)),
        key=lambda block: (block.transition_start or block.window_start, block.end_time),
    )
    for block in ordered:
        if block.end_time <= adjusted.now:
            continue
        buffer_start = block.transition_start or block.window_start
        existing = next(
            (window for window in adjusted.windows if window.window_ref == block.window_ref),
            None,
        )
        # The state may already be the P3 result from the previous turn.  In that
        # case its source window is already truncated immediately before the buffer.
        if existing is not None and (
            existing.ends_at <= buffer_start or existing.starts_at >= block.end_time
        ):
            continue
        replayed, applied_ref, movement_start, _ = _apply_movement_to_state_at(
            adjusted,
            block.estimated_minutes,
            depart_at=None,
            arrive_by=block.end_time,
            transition_minutes=block.transition_minutes,
            # estimated_minutes is already the effective duration; never apply
            # bike peak doubling again while replaying a known block.
            mode=None,
        )
        if applied_ref is None or movement_start != block.window_start:
            raise ValueError("confirmed movement block cannot be replayed into current windows")
        adjusted = replayed
    return adjusted


def reserve_timed_movement_interval(
    state: DayPlanningState,
    movement_start: datetime,
    estimated_minutes: int,
    transition_minutes: int = 0,
) -> Tuple[DayPlanningState, Optional[str]]:
    """Reserve one known movement interval while retaining capacity afterwards.

    P3's legacy fixed-commitment helper trims a window at a movement deadline.
    Execution sequences also need capacity *after* a task-to-task movement, so
    this small companion splits the containing free window into before/after
    pieces.  It performs no routing and is only given an already verified,
    campus-local interval.
    """
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")
    if not isinstance(movement_start, datetime):
        raise TypeError("movement_start must be datetime")
    if isinstance(estimated_minutes, bool) or not isinstance(estimated_minutes, int) or estimated_minutes < 1:
        raise ValueError("estimated_minutes 必须是正整数")
    if isinstance(transition_minutes, bool) or not isinstance(transition_minutes, int) or transition_minutes < 0:
        raise ValueError("transition_minutes 必须是非负整数")
    adjusted, refs = reserve_timed_movement_intervals(
        state, ((movement_start, estimated_minutes, transition_minutes),)
    )
    return adjusted, refs[0] if refs else None


def reserve_timed_movement_intervals(state: DayPlanningState, intervals):
    """Reserve several non-overlapping verified intervals in one state rewrite.

    Applying consecutive splits one at a time is subtly unsafe when a later
    interval lands in the suffix generated by an earlier one.  This batch form
    groups intervals by their original free window and emits deterministic free
    segments around every occupied movement interval.
    """
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")
    intervals = tuple(intervals or ())
    prepared = []
    pieces = []
    piece_groups = []
    crosses_boundary = False
    for index, item in enumerate(intervals):
        if not isinstance(item, tuple) or len(item) != 3:
            raise TypeError("intervals 必须为 (movement_start, minutes, transition) 元组")
        movement_start, estimated_minutes, transition_minutes = item
        if not isinstance(movement_start, datetime):
            raise TypeError("movement_start 必须是 datetime")
        if isinstance(estimated_minutes, bool) or not isinstance(estimated_minutes, int) or estimated_minutes < 1:
            raise ValueError("estimated_minutes 必须是正整数")
        if isinstance(transition_minutes, bool) or not isinstance(transition_minutes, int) or transition_minutes < 0:
            raise ValueError("transition_minutes 必须是非负整数")
        occupied_start = movement_start - timedelta(minutes=transition_minutes)
        occupied_end = movement_start + timedelta(minutes=estimated_minutes)
        target = next(
            (window for window in state.windows
             if window.starts_at <= occupied_start and occupied_end <= _movement_end_limit(window, state)),
            None,
        )
        if target is None:
            # An earliest-start split is not a physical obstruction. Reserve
            # the entire occupied interval across contiguous free windows,
            # retaining every original boundary and never crossing a gap or
            # a protected commitment/preparation interval.
            cursor = occupied_start
            group = []
            for window in sorted(state.windows, key=lambda value: value.starts_at):
                limit = _movement_end_limit(window, state)
                if window.starts_at <= cursor < limit:
                    end = min(limit, occupied_end)
                    group.append(len(pieces))
                    pieces.append((cursor, int((end - cursor).total_seconds() // 60), 0))
                    cursor = end
                    if cursor == occupied_end:
                        break
            if cursor != occupied_end:
                return state, ()
            piece_groups.append(group)
            crosses_boundary = True
            continue
        piece_groups.append([len(pieces)])
        pieces.append(item)
        prepared.append((index, target, occupied_start, occupied_end, movement_start))
    if crosses_boundary:
        ordered = sorted((start - timedelta(minutes=transition),
                          start + timedelta(minutes=minutes))
                         for start, minutes, transition in intervals)
        if any(right[0] < left[1] for left, right in zip(ordered, ordered[1:])):
            return state, ()
        adjusted, piece_refs = reserve_timed_movement_intervals(state, tuple(pieces))
        if len(piece_refs) != len(pieces):
            return state, ()
        return adjusted, tuple(piece_refs[group[-1]] for group in piece_groups)
    by_window = {}
    for item in prepared:
        by_window.setdefault(item[1].window_ref, []).append(item)
    for values in by_window.values():
        values.sort(key=lambda value: value[2])
        for left, right in zip(values, values[1:]):
            if right[2] < left[3]:
                return state, ()

    refs = {}
    new_windows = []
    for window in state.windows:
        values = by_window.get(window.window_ref)
        if not values:
            new_windows.append(window)
            continue
        cursor = window.starts_at
        first_segment = True
        for index, _, occupied_start, occupied_end, movement_start in values:
            if cursor < occupied_start:
                segment_ref = window.window_ref if first_segment else "{}__after_movement_{}".format(
                    window.window_ref, previous_start.strftime("%H%M")
                )
                minutes = int((occupied_start - cursor).total_seconds() // 60)
                new_windows.append(DayWindow(
                    window_ref=segment_ref,
                    starts_at=cursor,
                    ends_at=occupied_start,
                    availability=window.availability,
                    safety_buffer_minutes=window.safety_buffer_minutes if first_segment else 0,
                    travel_minutes=window.travel_minutes if first_segment else 0,
                    next_commitment_ref=window.next_commitment_ref,
                    capacity_minutes=max(0, minutes - (window.safety_buffer_minutes if first_segment else 0) - (window.travel_minutes if first_segment else 0)),
                ))
                refs[index] = segment_ref
            cursor = occupied_end
            previous_start = movement_start
            first_segment = False
        if cursor < window.ends_at:
            segment_ref = "{}__after_movement_{}".format(window.window_ref, previous_start.strftime("%H%M"))
            minutes = int((window.ends_at - cursor).total_seconds() // 60)
            new_windows.append(DayWindow(
                window_ref=segment_ref,
                starts_at=cursor,
                ends_at=window.ends_at,
                availability=window.availability,
                safety_buffer_minutes=0,
                travel_minutes=0,
                next_commitment_ref=window.next_commitment_ref,
                capacity_minutes=minutes,
            ))
            # The last interval may render against its following segment.
            refs[values[-1][0]] = segment_ref
        for index, _, occupied_start, occupied_end, movement_start in values:
            if index not in refs:
                marker_ref = "{}__movement_{}".format(window.window_ref, movement_start.strftime("%H%M"))
                new_windows.append(DayWindow(
                    window_ref=marker_ref,
                    starts_at=occupied_start,
                    ends_at=occupied_end,
                    availability=window.availability,
                    safety_buffer_minutes=0,
                    travel_minutes=0,
                    next_commitment_ref=window.next_commitment_ref,
                    capacity_minutes=0,
                ))
                refs[index] = marker_ref
    new_windows.sort(key=lambda item: item.starts_at)
    adjusted = DayPlanningState(
        reference_datetime=state.reference_datetime,
        now=state.now,
        day_end=state.day_end,
        commitments=state.commitments,
        tasks=state.tasks,
        windows=tuple(new_windows),
        active_window_ref=derive_active_window_ref(tuple(new_windows), state.now),
        unresolved_commitment_refs=state.unresolved_commitment_refs,
        history=state.history,
    )
    return adjusted, tuple(refs[index] for index in range(len(prepared)))


def final_plan_overlap_errors(
    state: DayPlanningState, allocation_plan: object, blocks: Sequence[MovementBlock] = ()
) -> Tuple[str, ...]:
    """Return deterministic final-plan overlap violations for the structural plan."""
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")
    intervals = []
    windows = {window.window_ref: window for window in state.windows}
    background_refs = {task.task_ref for task in state.tasks if task.attention_mode == 'background'}
    unknown_refs = [
        item for item in getattr(allocation_plan, "allocations", ())
        if getattr(item, "window_ref", None) not in windows
        and not (item.window_ref is None and item.task_ref in background_refs
                 and not item.occupies_attention and item.starts_at is not None)
    ]
    errors = ["task references an unknown window" for _ in unknown_refs]
    for window in state.windows:
        cursor = window.starts_at
        for allocation in sorted(
            (item for item in getattr(allocation_plan, "allocations", ())
             if item.window_ref == window.window_ref),
            key=lambda item: (item.sequence_index, item.allocation_ref),
        ):
            cursor = allocation.starts_at or cursor
            end = cursor + timedelta(minutes=allocation.planned_minutes)
            task = next((t for t in state.tasks if t.task_ref == allocation.task_ref), None)
            if task is None or task.attention_mode != 'background':
                intervals.append((cursor, end, "task"))
            cursor = end
    for commitment in state.commitments:
        prep = class_prep_interval(commitment)
        if prep is not None:
            intervals.append((prep[0], prep[1], "class_prep"))
        if commitment.starts_at is not None:
            # An unknown ending is occupied for this planning horizon.  It
            # must never be rendered as a free post-class interval.
            intervals.append((
                commitment.starts_at,
                commitment.ends_at or state.day_end,
                "commitment",
            ))
    for block in blocks or ():
        if not isinstance(block, MovementBlock):
            continue
        start = block.transition_start or block.window_start
        intervals.append((start, block.window_start, "transition"))
        intervals.append((block.window_start, block.end_time, "movement"))

    for start, end, kind in intervals:
        if start is None or end is None or end < start:
            errors.append("invalid {} segment".format(kind))
    intervals = sorted(
        (item for item in intervals if item[0] is not None and item[1] is not None),
        key=lambda item: (item[0], item[1], item[2]),
    )
    for index, left in enumerate(intervals):
        for right in intervals[index + 1:]:
            if right[0] >= left[1]:
                break
            # A zero-minute transition is not a real segment.
            if left[0] < right[1] and right[0] < left[1]:
                errors.append("{} overlaps {}".format(left[2], right[2]))
    return tuple(errors)


def _build_plan_from_resolved(
    state: DayPlanningState,
    resolved: Tuple[ResolvedMovement, ...],
    include_low_attention: bool,
    map_data: Optional[CampusMapData] = None,
) -> P3MovementPlan:
    adjusted = state
    blocks: list = []
    unhandled: list = []
    questions: list = []
    warnings: list = []
    for movement in resolved:
        if movement.status is MovementStatus.OK:
            estimate = movement.time_estimate
            if estimate is None or estimate.estimated_minutes <= 0:
                unhandled.append(movement)
                warnings.append("移动时间估计不可用，该移动暂不安排。")
                continue
            adjusted, applied_ref, movement_start, effective_minutes = _apply_movement_timed(
                adjusted,
                estimate.estimated_minutes,
                depart_at=movement.depart_at,
                arrive_by=movement.arrive_by,
                transition_minutes=TRANSITION_BUFFER_MINUTES,
                mode=movement.mode,
            )
            if applied_ref is None:
                unhandled.append(movement)
                warnings.append(
                    "没有足够窗口容纳移动时间（含出发前整理时间），该移动暂不安排。"
                )
                continue
            peak_applied = _is_bike_mode(movement.mode) and effective_minutes > estimate.estimated_minutes
            approximate = (
                movement.origin_resolution.status is LocationResolutionStatus.APPROXIMATE
                or movement.destination_resolution.status is LocationResolutionStatus.APPROXIMATE
            )
            if approximate:
                for resolution in (movement.origin_resolution, movement.destination_resolution):
                    if resolution.question:
                        questions.append(resolution.question)
            blocks.append(MovementBlock(
                window_ref=applied_ref,
                window_start=movement_start,
                origin_text=movement.origin_resolution.raw_text,
                destination_text=movement.destination_resolution.raw_text,
                origin_node_id=movement.origin_resolution.node_id,
                destination_node_id=movement.destination_resolution.node_id,
                origin_name=movement.origin_resolution.display_name or movement.origin_resolution.raw_text,
                destination_name=movement.destination_resolution.display_name
                or movement.destination_resolution.raw_text,
                destination_short_name=_short_node_name(
                    map_data,
                    movement.destination_resolution.node_id,
                    movement.destination_resolution.display_name
                    or movement.destination_resolution.raw_text,
                ),
                mode=movement.mode,
                distance_m=movement.route.total_distance_m if movement.route is not None else 0,
                estimated_minutes=effective_minutes,
                low_minutes=effective_minutes if peak_applied else estimate.min_minutes,
                high_minutes=effective_minutes if peak_applied else estimate.max_minutes,
                method=estimate.method,
                approximate=approximate,
                transition_minutes=TRANSITION_BUFFER_MINUTES,
                peak_bike=peak_applied,
            ))
        elif movement.status is MovementStatus.TRIVIAL:
            unhandled.append(movement)
            warnings.extend(movement.warnings)
        elif movement.status is MovementStatus.LOCATION_UNRESOLVED:
            unhandled.append(movement)
            questions.extend(movement.questions)
        else:
            unhandled.append(movement)
            warnings.extend(movement.warnings)

    plan = allocate_tasks_across_windows(adjusted, include_low_attention=include_low_attention)
    overlap_errors = final_plan_overlap_errors(adjusted, plan, blocks)
    if overlap_errors:
        raise RuntimeError(
            "P3 final-plan overlap invariant failed: {}".format(", ".join(overlap_errors))
        )
    warnings.extend(plan.warnings)
    return P3MovementPlan(
        movements=tuple(blocks),
        unhandled=tuple(unhandled),
        allocation_plan=plan,
        state=adjusted,
        questions=tuple(questions),
        warnings=tuple(warnings),
    )


def _apply_movement_to_state(
    state: DayPlanningState,
    movement_minutes: int,
    transition_minutes: int = 0,
    mode: object = None,
) -> Tuple[DayPlanningState, Optional[str], Optional[datetime], int]:
    """把 movement_minutes（及其前置 transition buffer）从目标窗口起始处扣除。

    - buffer 为“收拾东西”预留时间，真实占用窗口容量（仅真实地点切换时传入）；
    - bike 高峰规则：先用 base 时长得到候选区间，与高峰重叠则用 2x 时长重新放置，
      不再反复倍率判断（确定性两阶段）；walk 不受影响；
    - 返回 (调整后 state, 目标 window_ref, movement_start, effective_minutes)，
      effective_minutes 已含高峰倍率；无法容纳时返回 (原state, None, None, base)。
    """
    if movement_minutes <= 0:
        return state, None, None, movement_minutes
    if transition_minutes < 0:
        raise ValueError("transition_minutes must be >= 0")
    target_ref = None
    movement_start = None
    effective_minutes = movement_minutes
    for window in state.windows:
        if window.ends_at <= state.now:
            continue
        # 候选区间：buffer 之后才开始移动
        buffer_start = max(window.starts_at, state.now)
        candidate_start = buffer_start + timedelta(minutes=transition_minutes)
        candidate_end = candidate_start + timedelta(minutes=movement_minutes)
        effective = effective_travel_minutes(mode, movement_minutes, candidate_start, candidate_end)
        total_minutes = effective + transition_minutes
        if window.capacity_minutes > total_minutes:
            target_ref = window.window_ref
            movement_start = candidate_start
            effective_minutes = effective
            break
    if target_ref is None or movement_start is None:
        return state, None, None, movement_minutes

    new_windows: list = []
    for window in state.windows:
        if window.window_ref != target_ref:
            new_windows.append(window)
            continue
        new_start = movement_start + timedelta(minutes=effective_minutes)
        duration = int((window.ends_at - new_start).total_seconds() // 60)
        capacity = duration - window.safety_buffer_minutes - window.travel_minutes
        if new_start >= window.ends_at or capacity <= 0:
            continue
        new_windows.append(DayWindow(
            window_ref=window.window_ref,
            starts_at=new_start,
            ends_at=window.ends_at,
            availability=window.availability,
            safety_buffer_minutes=window.safety_buffer_minutes,
            travel_minutes=window.travel_minutes,
            next_commitment_ref=window.next_commitment_ref,
            capacity_minutes=capacity,
        ))
    active_ref = derive_active_window_ref(tuple(new_windows), state.now)
    adjusted = DayPlanningState(
        reference_datetime=state.reference_datetime,
        now=state.now,
        day_end=state.day_end,
        commitments=state.commitments,
        tasks=state.tasks,
        windows=tuple(new_windows),
        active_window_ref=active_ref,
        unresolved_commitment_refs=state.unresolved_commitment_refs,
        history=state.history,
    )
    return adjusted, target_ref, movement_start, effective_minutes


def _apply_movement_timed(
    state: DayPlanningState,
    movement_minutes: int,
    depart_at: Optional[datetime] = None,
    arrive_by: Optional[datetime] = None,
    transition_minutes: int = 0,
    mode: object = None,
) -> Tuple[DayPlanningState, Optional[str], Optional[datetime], int]:
    """明确时间移动：depart_at / arrive_by 均未提供时走既有“窗口起始”逻辑。"""
    if depart_at is None and arrive_by is None:
        return _apply_movement_to_state(state, movement_minutes, transition_minutes, mode)
    return _apply_movement_to_state_at(
        state, movement_minutes, depart_at, arrive_by, transition_minutes, mode
    )


def _apply_movement_to_state_at(
    state: DayPlanningState,
    movement_minutes: int,
    depart_at: Optional[datetime],
    arrive_by: Optional[datetime],
    transition_minutes: int = 0,
    mode: object = None,
) -> Tuple[DayPlanningState, Optional[str], Optional[datetime], int]:
    """把明确时间的移动放到 [buffer_start, movement_end]，任务容量限制在 buffer 之前。

    - arrive_by：movement_end = arrive_by，先按 base 倒推候选区间，
      候选区间与高峰重叠则 effective = base * 2 并重新倒推出发；
      buffer_start = movement_start - transition（任务最晚做到 buffer_start）；
    - depart_at：movement_start 保持用户出发时刻，候选区间 = [depart_at, depart_at + base)，
      重叠则 effective = base * 2；出发时刻已过时 buffer 从 now 开始，移动顺延；
    - 移动 + buffer 必须落在某个剩余窗口内，允许占用该窗口末尾（只要不晚于下一固定安排开始）；
    - 窗口保留原 ref（移动块依赖它渲染），只把 ends_at 截到 buffer_start；
    - 返回 (state, ref, movement_start, effective_minutes)。
    """
    if movement_minutes <= 0:
        return state, None, None, movement_minutes
    if transition_minutes < 0:
        raise ValueError("transition_minutes must be >= 0")
    now = state.now
    if depart_at is not None and arrive_by is not None:
        return state, None, None, movement_minutes

    target_ref = None
    movement_start = None
    movement_end = None
    buffer_start = None
    effective_minutes = movement_minutes
    if arrive_by is not None:
        movement_end = arrive_by
        candidate_start = arrive_by - timedelta(minutes=movement_minutes)
        effective_minutes = effective_travel_minutes(mode, movement_minutes, candidate_start, arrive_by)
        movement_start = arrive_by - timedelta(minutes=effective_minutes)
        buffer_start = movement_start - timedelta(minutes=transition_minutes)
        if buffer_start < now:
            return state, None, None, effective_minutes
        for window in state.windows:
            if window.ends_at <= now:
                continue
            if window.starts_at <= buffer_start and movement_end <= _movement_end_limit(window, state):
                target_ref = window.window_ref
                break
    else:
        for window in state.windows:
            if window.ends_at <= now:
                continue
            if window.starts_at <= depart_at < window.ends_at:
                raw_start = max(depart_at, now, window.starts_at)
                buffer_start = max(raw_start - timedelta(minutes=transition_minutes), now)
                movement_start = buffer_start + timedelta(minutes=transition_minutes)
                candidate_end = movement_start + timedelta(minutes=movement_minutes)
                effective_minutes = effective_travel_minutes(mode, movement_minutes, movement_start, candidate_end)
                movement_end = movement_start + timedelta(minutes=effective_minutes)
                if window.starts_at <= buffer_start and movement_end <= _movement_end_limit(window, state):
                    target_ref = window.window_ref
                    break
        if target_ref is None:
            for window in state.windows:
                if window.ends_at <= now:
                    continue
                if window.starts_at >= depart_at:
                    # 出发时刻落在固定安排中：从下一可用窗口开始安排 buffer + 移动
                    buffer_start = max(window.starts_at, now)
                    movement_start = buffer_start + timedelta(minutes=transition_minutes)
                    candidate_end = movement_start + timedelta(minutes=movement_minutes)
                    effective_minutes = effective_travel_minutes(mode, movement_minutes, movement_start, candidate_end)
                    movement_end = movement_start + timedelta(minutes=effective_minutes)
                    if movement_end <= _movement_end_limit(window, state):
                        target_ref = window.window_ref
                        break
    if (
        target_ref is None
        or movement_start is None
        or movement_end is None
        or buffer_start is None
    ):
        return state, None, None, effective_minutes

    new_windows: list = []
    for window in state.windows:
        if window.window_ref != target_ref:
            new_windows.append(window)
            continue
        if buffer_start <= window.starts_at:
            # buffer 从窗口一开始就占用：窗口不再有任务容量，
            # 但保留原窗口（capacity=0）供移动块按 window_ref 渲染。
            new_windows.append(DayWindow(
                window_ref=window.window_ref,
                starts_at=window.starts_at,
                ends_at=window.ends_at,
                availability=window.availability,
                safety_buffer_minutes=window.safety_buffer_minutes,
                travel_minutes=window.travel_minutes,
                next_commitment_ref=window.next_commitment_ref,
                capacity_minutes=0,
            ))
            continue
        available_duration = int((buffer_start - window.starts_at).total_seconds() // 60)
        capacity = available_duration - window.safety_buffer_minutes - window.travel_minutes
        if capacity < 0:
            capacity = 0
        new_windows.append(DayWindow(
            window_ref=window.window_ref,
            starts_at=window.starts_at,
            ends_at=buffer_start,
            availability=window.availability,
            safety_buffer_minutes=window.safety_buffer_minutes,
            travel_minutes=window.travel_minutes,
            next_commitment_ref=window.next_commitment_ref,
            capacity_minutes=capacity,
        ))
    active_ref = derive_active_window_ref(tuple(new_windows), now)
    adjusted = DayPlanningState(
        reference_datetime=state.reference_datetime,
        now=now,
        day_end=state.day_end,
        commitments=state.commitments,
        tasks=state.tasks,
        windows=tuple(new_windows),
        active_window_ref=active_ref,
        unresolved_commitment_refs=state.unresolved_commitment_refs,
        history=state.history,
    )
    return adjusted, target_ref, movement_start, effective_minutes


def _movement_end_limit(window: DayWindow, state: DayPlanningState) -> datetime:
    """移动允许的最晚结束时刻：有后续固定安排时为其开始时间，否则为窗口结束时间。"""
    if window.next_commitment_ref is not None:
        next_commitment = next(
            (
                commitment
                for commitment in state.commitments
                if commitment.commitment_ref == window.next_commitment_ref
            ),
            None,
        )
        if next_commitment is not None:
            return next_commitment.starts_at
    return window.ends_at


_MODE_LABEL = {
    TransportMode.WALK: "步行",
    TransportMode.BIKE: "骑行",
}


def render_movement_plan_lines(plan: P3MovementPlan) -> Tuple[str, ...]:
    """生成“当前方案”文本行：移动行（真实时间段）+ P2 任务/安排行。

    不输出内部 ref / enum / dataclass repr；移动耗时为 AI 暂估时内联标注。
    """
    if not isinstance(plan, P3MovementPlan):
        raise TypeError("plan must be a P3MovementPlan")
    return render_movement_lines(plan.movements, plan.allocation_plan, plan.state)


def render_movement_lines(
    blocks: Sequence[MovementBlock],
    allocation_plan: object,
    state: DayPlanningState,
    execution_context=None,
    concurrent_allocations=(),
) -> Tuple[str, ...]:
    """用当前窗口/计划渲染移动行 + 任务行，按时间合并排序。

    - 第一行优先“现在”动作（移动优先，其次当前任务/固定安排）；
    - 已失效窗口的移动块（window_ref 已不存在）自动丢弃，不显示过期时间；
    - 不输出内部 ref / enum / dataclass repr。
    """
    now = state.now
    valid_blocks = [
        block
        for block in blocks
        if block.window_ref in {window.window_ref for window in state.windows}
        and block.end_time > now
    ]
    valid_blocks.sort(key=lambda item: (item.window_start, item.destination_name))

    movement_now = None
    movement_future: list = []
    for block in valid_blocks:
        display = _movement_display(block, state, execution_context)
        buffer_start = block.transition_start
        if buffer_start is not None and buffer_start <= now < block.window_start:
            # 当前处于“收拾东西”buffer：优先告诉用户现在该做什么
            movement_now = "现在：" + _TRANSITION_LABEL
            movement_future.append(
                "{}–{}：{}".format(
                    block.window_start.strftime("%H:%M"),
                    block.end_time.strftime("%H:%M"),
                    display,
                )
            )
        elif block.window_start <= now < block.end_time:
            movement_now = "现在：" + display
        else:
            if buffer_start is not None and buffer_start > now:
                movement_future.append(
                    "{}–{}：{}".format(
                        buffer_start.strftime("%H:%M"),
                        block.window_start.strftime("%H:%M"),
                        _TRANSITION_LABEL,
                    )
                )
            movement_future.append(
                "{}–{}：{}".format(
                    block.window_start.strftime("%H:%M"),
                    block.end_time.strftime("%H:%M"),
                    display,
                )
            )

    compact = compact_plan_lines(
        allocation_plan,
        state,
        execution_context=execution_context,
        concurrent_allocations=concurrent_allocations,
    )
    compact_now = None
    compact_future: list = []
    for line in compact:
        if compact_now is None and line.startswith("现在"):
            compact_now = line
        else:
            compact_future.append(line)

    lines: list = []
    future = movement_future + compact_future
    future.sort(key=_line_time_key)
    if movement_now is not None:
        lines.append(movement_now)
    elif compact_now is not None:
        if compact_now.startswith("现在暂时没有安排"):
            # 无进行中安排：下一项时间必须来自最终合并计划的第一条真实行（含 movement/buffer）
            lines.append(no_current_task_line(_first_line_start(future)))
        else:
            lines.append(compact_now)
    lines.extend(future)
    return tuple(lines)


def _line_time_key(line: str):
    """提取行首 HH:MM 用于按时间合并排序；无时间行排最后。"""
    match = re.match(r"^(\d{2}):(\d{2})", line)
    if match is None:
        return (10 ** 9,)
    return (int(match.group(1)) * 60 + int(match.group(2)),)


def _first_line_start(lines) -> Optional[str]:
    """返回首条行的行首 HH:MM 字符串；无行或无时间时返回 None。"""
    if not lines:
        return None
    match = re.match(r"^(\d{2}):(\d{2})", lines[0])
    if match is None:
        return None
    return "{}:{}".format(match.group(1), match.group(2))


def _short_node_name(
    map_data: Optional[CampusMapData], node_id: Optional[str], fallback: str
) -> str:
    """取节点最短别名做用户可见显示名；map_data 缺失时用 fallback。"""
    if map_data is None or node_id is None:
        return fallback
    for node in map_data.nodes:
        if node.id == node_id:
            candidates = [alias for alias in node.aliases if len(alias) >= 2]
            if not candidates:
                return node.name or fallback
            return min(candidates, key=lambda item: (len(item), item))
    return fallback


def _movement_display(block: MovementBlock, state=None, execution_context=None) -> str:
    label = _MODE_LABEL.get(block.mode, "步行")
    name = block.destination_short_name or block.destination_name
    # P4 execution routes are deterministic, map-scoped facts.  Legacy P3
    # blocks preserve their historical estimate label for compatibility.
    suffix = "" if execution_context is not None else "（AI暂估）"
    auto_meal = False
    if execution_context is not None:
        from src.p4_execution_presentation import is_auto_selected_meal_destination
        auto_meal = is_auto_selected_meal_destination(block, execution_context)
    destination = name + ("【已为您选择就近食堂】" if auto_meal else "")
    verb = "去" if execution_context is not None else "前往"
    if block.peak_bike:
        text = "高峰期{}{}{}，约{}分钟{}".format(
            label, verb, destination, block.estimated_minutes, suffix
        )
    else:
        text = "{}{}{}，约{}分钟{}".format(
            label, verb, destination, block.estimated_minutes, suffix
        )
    if execution_context is not None:
        if _is_class_building_arrival(block, state):
            text += "，{}到教学楼".format(block.end_time.strftime("%H:%M"))
    if block.approximate:
        text += "（地点待确认）"
    return text


def _is_class_building_arrival(block, state):
    if state is None or not getattr(block, "destination_activity_ref", None):
        return False
    from src.p3_class_prep import is_class_commitment
    commitment = next(
        (item for item in state.commitments
         if item.commitment_ref == block.destination_activity_ref),
        None,
    )
    return bool(is_class_commitment(commitment))
