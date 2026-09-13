"""P2a 全天多窗口纯函数派生（Python 3.8 兼容）。

只消费结构化 FixedCommitment 与调用方给定的 travel 分钟事实；
不调用路线 provider、不解析地点、不估计路线、不调用 AI。
"""

from datetime import datetime
from typing import Callable, List, Mapping, Optional, Tuple, Union

from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_models import (
    DayPlanningState,
    DayWindow,
    TaskProgress,
    WindowDerivationResult,
    make_window_ref,
)
from src.p3_class_prep import class_prep_interval

DEFAULT_SAFETY_BUFFER_MINUTES = 10

TravelMinutesInput = Optional[Union[Mapping[str, int], Callable[[str], int]]]


def derive_day_windows(
    now: datetime,
    commitments: Tuple[FixedCommitment, ...],
    day_end: datetime,
    default_safety_buffer_minutes: int = DEFAULT_SAFETY_BUFFER_MINUTES,
    travel_minutes_by_commitment: TravelMinutesInput = None,
) -> WindowDerivationResult:
    """根据 now / 固定安排 / day_end 派生当天剩余可安排窗口。

    - 已过期安排（ends_at <= now）被忽略，不拒绝；
    - 正在进行且 UNAVAILABLE 的安排：结束前不产生自由窗口；
    - 正在进行且 LOW_ATTENTION 的安排（start/end 已知）：产生 LOW_ATTENTION 窗口；
    - 重叠安排按占用区间合并，不 crash、不产生重叠窗口、不产生负容量；
    - starts_at 未知：不参与切分，进入 unresolved；
    - 未来安排 ends_at 未知：只生成到其 starts_at，之后停止派生，进入 unresolved；
    - 当前安排 ends_at 未知：不产生任何未来窗口，进入 unresolved；
    - 只截取到 day_end，不生成跨午夜窗口。
    """
    _validate_derivation_inputs(now, day_end, default_safety_buffer_minutes, commitments)

    unresolved: List[str] = []
    blocks: List[Tuple[datetime, datetime, AvailabilityLevel, str]] = []
    hard_stop = day_end
    hard_stop_ref: Optional[str] = None

    for commitment in commitments:
        starts_at = commitment.starts_at
        ends_at = commitment.ends_at
        if starts_at is None:
            _add_unresolved(unresolved, commitment.commitment_ref)
            continue
        if starts_at >= day_end:
            continue
        if ends_at is None:
            if starts_at <= now:
                _add_unresolved(unresolved, commitment.commitment_ref)
                return WindowDerivationResult(
                    windows=(), unresolved_commitment_refs=tuple(unresolved)
                )
            _add_unresolved(unresolved, commitment.commitment_ref)
            # An unknown class end still has a deterministic arrival/prep
            # interval before its known start.  This must constrain allocator
            # capacity, not be added later by presentation/final audit.
            prep = class_prep_interval(commitment)
            unavailable_start = prep[0] if prep is not None else starts_at
            if unavailable_start < hard_stop:
                hard_stop = unavailable_start
                hard_stop_ref = commitment.commitment_ref
            blocks.append(
                (
                    unavailable_start,
                    day_end,
                    AvailabilityLevel.UNAVAILABLE,
                    commitment.commitment_ref,
                )
            )
            continue
        if ends_at <= now:
            continue
        block_end = min(ends_at, day_end)
        if block_end <= now:
            continue
        if commitment.availability_during == AvailabilityLevel.FULLY_AVAILABLE:
            continue
        prep = class_prep_interval(commitment)
        if prep is not None and prep[1] > now:
            prep_start = max(prep[0], now)
            if prep[1] > prep_start:
                blocks.append((prep_start, prep[1], AvailabilityLevel.UNAVAILABLE, commitment.commitment_ref))
        blocks.append((starts_at, block_end, commitment.availability_during, commitment.commitment_ref))

    blocks.sort(key=lambda item: (item[0], item[1]))
    merged = _merge_blocks(blocks)

    windows: List[DayWindow] = []
    cursor = now
    for position, (start, end, availability, ref) in enumerate(merged):
        if start > cursor:
            gap_end = min(start, hard_stop)
            if gap_end > cursor:
                window = _make_free_window(
                    window_ref=make_window_ref(len(windows)),
                    starts_at=cursor,
                    ends_at=gap_end,
                    next_commitment_ref=ref,
                    safety_buffer_minutes=default_safety_buffer_minutes,
                    travel_minutes=_travel_minutes(travel_minutes_by_commitment, ref),
                )
                if window is not None:
                    windows.append(window)
        cursor = max(cursor, end)
        if cursor >= hard_stop:
            break
        if availability == AvailabilityLevel.LOW_ATTENTION:
            low_start = max(start, now)
            low_end = min(end, hard_stop)
            if low_end > low_start:
                next_ref = merged[position + 1][3] if position + 1 < len(merged) else None
                window = _make_low_attention_window(
                    window_ref=make_window_ref(len(windows)),
                    starts_at=low_start,
                    ends_at=low_end,
                    next_commitment_ref=next_ref,
                    safety_buffer_minutes=default_safety_buffer_minutes,
                    travel_minutes=_travel_minutes(travel_minutes_by_commitment, next_ref),
                )
                if window is not None:
                    windows.append(window)

    if hard_stop > cursor:
        window = _make_free_window(
            window_ref=make_window_ref(len(windows)),
            starts_at=cursor,
            ends_at=hard_stop,
            next_commitment_ref=hard_stop_ref,
            safety_buffer_minutes=default_safety_buffer_minutes,
            travel_minutes=_travel_minutes(travel_minutes_by_commitment, hard_stop_ref),
        )
        if window is not None:
            windows.append(window)

    return WindowDerivationResult(
        windows=tuple(windows), unresolved_commitment_refs=tuple(unresolved)
    )


def derive_active_window_ref(windows: Tuple[DayWindow, ...], now: datetime) -> Optional[str]:
    """确定性派生：存在 starts_at <= now < ends_at 的窗口时返回其 ref，否则返回 None。"""
    for window in windows:
        if window.starts_at <= now < window.ends_at:
            return window.window_ref
    return None


def derive_day_state(
    now: datetime,
    day_end: datetime,
    commitments: Tuple[FixedCommitment, ...],
    tasks: Tuple[TaskProgress, ...],
    default_safety_buffer_minutes: int = DEFAULT_SAFETY_BUFFER_MINUTES,
    travel_minutes_by_commitment: TravelMinutesInput = None,
    history: Tuple[str, ...] = (),
    reference_datetime: Optional[datetime] = None,
) -> DayPlanningState:
    """构造完整 DayPlanningState：派生窗口、计算 active window、收集 unresolved。"""
    result = derive_day_windows(
        now,
        tuple(commitments),
        day_end,
        default_safety_buffer_minutes,
        travel_minutes_by_commitment,
    )
    return DayPlanningState(
        reference_datetime=reference_datetime if reference_datetime is not None else now,
        now=now,
        day_end=day_end,
        commitments=tuple(commitments),
        tasks=tuple(tasks),
        windows=result.windows,
        active_window_ref=derive_active_window_ref(result.windows, now),
        unresolved_commitment_refs=result.unresolved_commitment_refs,
        history=tuple(history),
    )


def _merge_blocks(blocks):
    merged = []
    for start, end, availability, ref in blocks:
        if merged and start <= merged[-1][1]:
            previous = merged[-1]
            merged[-1] = (
                previous[0],
                max(previous[1], end),
                _stricter_availability(previous[2], availability),
                previous[3],
            )
        else:
            merged.append((start, end, availability, ref))
    return merged


def _stricter_availability(a: AvailabilityLevel, b: AvailabilityLevel) -> AvailabilityLevel:
    if a == AvailabilityLevel.UNAVAILABLE or b == AvailabilityLevel.UNAVAILABLE:
        return AvailabilityLevel.UNAVAILABLE
    return AvailabilityLevel.LOW_ATTENTION


def _make_free_window(
    window_ref: str,
    starts_at: datetime,
    ends_at: datetime,
    next_commitment_ref: Optional[str],
    safety_buffer_minutes: int,
    travel_minutes: int,
) -> Optional[DayWindow]:
    capacity = _capacity_minutes(starts_at, ends_at, safety_buffer_minutes, travel_minutes)
    if capacity <= 0:
        return None
    return DayWindow(
        window_ref=window_ref,
        starts_at=starts_at,
        ends_at=ends_at,
        availability=AvailabilityLevel.FULLY_AVAILABLE,
        safety_buffer_minutes=safety_buffer_minutes,
        travel_minutes=travel_minutes,
        next_commitment_ref=next_commitment_ref,
        capacity_minutes=capacity,
    )


def _make_low_attention_window(
    window_ref: str,
    starts_at: datetime,
    ends_at: datetime,
    next_commitment_ref: Optional[str],
    safety_buffer_minutes: int,
    travel_minutes: int,
) -> Optional[DayWindow]:
    capacity = _capacity_minutes(starts_at, ends_at, safety_buffer_minutes, travel_minutes)
    if capacity <= 0:
        return None
    return DayWindow(
        window_ref=window_ref,
        starts_at=starts_at,
        ends_at=ends_at,
        availability=AvailabilityLevel.LOW_ATTENTION,
        safety_buffer_minutes=safety_buffer_minutes,
        travel_minutes=travel_minutes,
        next_commitment_ref=next_commitment_ref,
        capacity_minutes=capacity,
    )


def _capacity_minutes(
    starts_at: datetime, ends_at: datetime, safety_buffer_minutes: int, travel_minutes: int
) -> int:
    duration = int((ends_at - starts_at).total_seconds() // 60)
    return duration - safety_buffer_minutes - travel_minutes


def _validate_derivation_inputs(now, day_end, default_safety_buffer_minutes, commitments) -> None:
    if not isinstance(now, datetime) or not isinstance(day_end, datetime):
        raise ValueError("now and day_end must be datetimes")
    if not now < day_end:
        raise ValueError("day_end must be after now")
    if now.date() != day_end.date():
        raise ValueError("now and day_end must be on the same planning day")
    if isinstance(default_safety_buffer_minutes, bool) or not isinstance(
        default_safety_buffer_minutes, int
    ):
        raise ValueError("default_safety_buffer_minutes must be an integer")
    if default_safety_buffer_minutes < 0:
        raise ValueError("default_safety_buffer_minutes must be >= 0")
    if not isinstance(commitments, (tuple, list)):
        raise TypeError("commitments must be a tuple or list of FixedCommitment")
    for commitment in commitments:
        if not isinstance(commitment, FixedCommitment):
            raise TypeError("commitments must contain FixedCommitment instances")


def _travel_minutes(travel_input: TravelMinutesInput, commitment_ref: Optional[str]) -> int:
    if commitment_ref is None:
        return 0
    if travel_input is None:
        return 0
    if callable(travel_input):
        value = travel_input(commitment_ref)
    elif isinstance(travel_input, Mapping):
        value = travel_input.get(commitment_ref, 0)
    else:
        raise TypeError("travel_minutes_by_commitment must be a Mapping or callable")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("travel minutes must be integers")
    if value < 0:
        raise ValueError("travel minutes must be >= 0")
    return value


def _add_unresolved(unresolved: List[str], ref: str) -> None:
    if ref not in unresolved:
        unresolved.append(ref)
