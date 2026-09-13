"""P3c 移动时间估计：Qwen 估计 + 程序兜底（Python 3.8 兼容）。

输入：真实路线 total_distance_m（程序事实，Qwen 不得修改）+ 交通方式。
输出：合理耗时区间（min ~ max 分钟）与用于扣容量的单一 estimated_minutes。
- Qwen 成功：min/max/estimated 全部经过程序合理性钳制，来源标记 AI_ESTIMATED；
- Qwen 失败 / 无 caller：使用确定性程序兜底公式（walk 约 75m/min，bike 约 220m/min），
  明确标记 method=program_fallback，不生成“假精确时间”。
路线距离永远是程序事实；模型只估计时间，不允许声称官方/高德精确耗时。
"""

import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Tuple

from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p3_map_schema import TransportMode

TIME_ESTIMATE_SCHEMA_VERSION = "p3.travel-time.v1"
MAX_REASON_LENGTH = 200

AgentCaller = Callable[[str, str], str]


class TimeEstimateMethod(str, Enum):
    QWEN = "qwen"
    PROGRAM_FALLBACK = "program_fallback"


# 物理合理速度边界（米/分钟）：walk 40~150，bike 100~400。
_SPEED_BOUNDS = {
    TransportMode.WALK: (40.0, 150.0),
    TransportMode.BIKE: (100.0, 400.0),
}
_FALLBACK_SPEED = {
    TransportMode.WALK: 75.0,
    TransportMode.BIKE: 220.0,
}


@dataclass(frozen=True)
class TravelTimeProposal:
    """Qwen 时间估计的解析后提案（分钟区间）。"""

    schema_version: str
    min_minutes: Optional[int]
    max_minutes: Optional[int]
    reason: Optional[str]

    def __post_init__(self):
        if self.schema_version != TIME_ESTIMATE_SCHEMA_VERSION:
            raise ValueError("schema_version 不匹配")
        for name, value in (("min_minutes", self.min_minutes), ("max_minutes", self.max_minutes)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("{} 必须是正整数".format(name))
            if value is not None and value < 1:
                raise ValueError("{} 必须 >= 1".format(name))
        if self.min_minutes is not None and self.max_minutes is not None:
            if self.min_minutes > self.max_minutes:
                raise ValueError("min_minutes 不能大于 max_minutes")
        if self.reason is not None and len(self.reason) > MAX_REASON_LENGTH:
            raise ValueError("reason 过长")


@dataclass(frozen=True)
class TravelTimeEstimate:
    """最终移动耗时估计（程序钳制后的只读结果）。"""

    distance_m: Optional[int]
    mode: TransportMode
    min_minutes: int
    max_minutes: int
    estimated_minutes: int
    method: TimeEstimateMethod
    note: Optional[str] = None

    def __post_init__(self):
        if isinstance(self.min_minutes, bool) or not isinstance(self.min_minutes, int):
            raise ValueError("min_minutes 必须是整数")
        if isinstance(self.max_minutes, bool) or not isinstance(self.max_minutes, int):
            raise ValueError("max_minutes 必须是整数")
        if self.min_minutes < 0 or self.max_minutes < self.min_minutes:
            raise ValueError("耗时区间非法")
        if isinstance(self.estimated_minutes, bool) or not isinstance(self.estimated_minutes, int):
            raise ValueError("estimated_minutes 必须是整数")
        if self.estimated_minutes < self.min_minutes or self.estimated_minutes > self.max_minutes:
            raise ValueError("estimated_minutes 必须在区间内")


def build_time_estimate_prompt(
    origin_name: str, destination_name: str, distance_m: int, mode: object
) -> Tuple[str, str]:
    parsed_mode = _parse_mode(mode)
    system = (
        "你是校园移动时间估计器。路线距离（米）是程序给出的事实，你不得修改它，"
        "也不得声称这是高德或学校官方精确耗时。\n"
        "请根据距离与交通方式估计一个合理的耗时区间（分钟），输出 JSON，不要输出其他内容。\n"
        "规则：步行约每分钟 40~150 米，骑行约每分钟 100~400 米；"
        "区间要合理（例如 800 米步行约 8~14 分钟，不要给出 1 分钟或 600 分钟）。\n"
        '输出格式：{"schema_version": "' + TIME_ESTIMATE_SCHEMA_VERSION + '", '
        '"min_minutes": 整数, "max_minutes": 整数, "reason": 简短理由或null}\n'
    )
    user = "起点={}；终点={}；交通方式={}；距离={}米".format(
        origin_name, destination_name, parsed_mode.value, int(distance_m)
    )
    return system, user


def parse_time_estimate(text: str) -> TravelTimeProposal:
    """解析模型输出；结构/数值非法抛 AgenticParseError。"""
    if not isinstance(text, str) or not text.strip():
        raise AgenticParseError("time estimate 输出为空")
    payload = extract_json_object(text)
    if not isinstance(payload, dict):
        raise AgenticParseError("time estimate 输出不是 JSON 对象")
    try:
        return TravelTimeProposal(
            schema_version=payload.get("schema_version"),
            min_minutes=payload.get("min_minutes"),
            max_minutes=payload.get("max_minutes"),
            reason=payload.get("reason"),
        )
    except (TypeError, ValueError) as exc:
        raise AgenticParseError(str(exc)) from None


def fallback_travel_time(distance_m: Optional[int], mode: object) -> TravelTimeEstimate:
    """确定性程序兜底：无模型 / 模型失败时使用，不生成假精确时间。"""
    parsed_mode = _parse_mode(mode)
    if distance_m is None or distance_m <= 0:
        return TravelTimeEstimate(
            distance_m=distance_m,
            mode=parsed_mode,
            min_minutes=0,
            max_minutes=0,
            estimated_minutes=0,
            method=TimeEstimateMethod.PROGRAM_FALLBACK,
            note="无有效路线距离，不估计移动时间",
        )
    speed = _FALLBACK_SPEED[parsed_mode]
    center = _ceil_minutes(distance_m / speed)
    low = _ceil_minutes(distance_m / (speed * 1.25))
    high = _ceil_minutes(distance_m / (speed * 0.8))
    low = max(low, 1)
    high = max(high, center, low)
    return TravelTimeEstimate(
        distance_m=distance_m,
        mode=parsed_mode,
        min_minutes=low,
        max_minutes=high,
        estimated_minutes=center,
        method=TimeEstimateMethod.PROGRAM_FALLBACK,
        note="模型不可用，使用程序兜底估算",
    )


def estimate_travel_time(
    distance_m: Optional[int],
    mode: object,
    caller: Optional[AgentCaller] = None,
) -> TravelTimeEstimate:
    """估算移动时间；Qwen 成功则钳制后采用，失败则程序兜底。"""
    parsed_mode = _parse_mode(mode)
    if distance_m is None or distance_m <= 0:
        return fallback_travel_time(distance_m, parsed_mode)
    if caller is None:
        return fallback_travel_time(distance_m, parsed_mode)

    origin_name = "起点"
    destination_name = "终点"
    system, user = build_time_estimate_prompt(origin_name, destination_name, distance_m, parsed_mode)
    try:
        text = caller(system, user)
        proposal = parse_time_estimate(text)
    except (AgenticParseError, ValueError):
        return fallback_travel_time(distance_m, parsed_mode)
    if proposal.min_minutes is None or proposal.max_minutes is None:
        return fallback_travel_time(distance_m, parsed_mode)
    return _clamp_to_plausible(distance_m, parsed_mode, proposal.min_minutes, proposal.max_minutes)


def estimate_travel_time_named(
    origin_name: str,
    destination_name: str,
    distance_m: Optional[int],
    mode: object,
    caller: Optional[AgentCaller] = None,
) -> TravelTimeEstimate:
    """带起终点名称的估计入口（名称只进入 prompt，不影响程序事实）。"""
    parsed_mode = _parse_mode(mode)
    if distance_m is None or distance_m <= 0:
        return fallback_travel_time(distance_m, parsed_mode)
    if caller is None:
        return fallback_travel_time(distance_m, parsed_mode)
    system, user = build_time_estimate_prompt(origin_name, destination_name, distance_m, parsed_mode)
    try:
        text = caller(system, user)
        proposal = parse_time_estimate(text)
    except (AgenticParseError, ValueError):
        return fallback_travel_time(distance_m, parsed_mode)
    if proposal.min_minutes is None or proposal.max_minutes is None:
        return fallback_travel_time(distance_m, parsed_mode)
    return _clamp_to_plausible(distance_m, parsed_mode, proposal.min_minutes, proposal.max_minutes)


def _clamp_to_plausible(
    distance_m: int, mode: TransportMode, min_minutes: int, max_minutes: int
) -> TravelTimeEstimate:
    speed_low, speed_high = _SPEED_BOUNDS[mode]
    plausible_min = max(_ceil_minutes(distance_m / speed_high), 1)
    plausible_max = max(_ceil_minutes(distance_m / speed_low), plausible_min)
    low = max(min(min_minutes, max_minutes), 1)
    high = max(max(min_minutes, max_minutes), low)
    low = max(min(low, plausible_max), 1)
    high = max(min(high, plausible_max), low)
    if high < plausible_min:
        high = plausible_min
    low = min(low, high)
    if low < 1:
        low = 1
    estimated = (low + high) // 2
    if estimated < low:
        estimated = low
    if estimated > high:
        estimated = high
    return TravelTimeEstimate(
        distance_m=distance_m,
        mode=mode,
        min_minutes=low,
        max_minutes=high,
        estimated_minutes=estimated,
        method=TimeEstimateMethod.QWEN,
        note="Qwen 估计，经程序合理性钳制",
    )


def _ceil_minutes(value: float) -> int:
    return int(math.ceil(max(value, 0.0)))


def _parse_mode(mode: object) -> TransportMode:
    from src.p3_map_schema import parse_transport_mode

    return parse_transport_mode(mode)
