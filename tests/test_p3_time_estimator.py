"""P3c 移动时间估计测试：Qwen 钳制 + 程序兜底 + walk/bike 差异（Python 3.8 兼容，全 mock）。"""
import pytest

from src.p2_agentic_parser import AgenticParseError
from src.p3_map_schema import TransportMode
from src.p3_time_estimator import (
    TIME_ESTIMATE_SCHEMA_VERSION,
    TimeEstimateMethod,
    build_time_estimate_prompt,
    estimate_travel_time,
    fallback_travel_time,
    parse_time_estimate,
)


def time_json(min_m, max_m, reason=None):
    r = "null" if reason is None else '"' + reason + '"'
    return (
        '{"schema_version": "' + TIME_ESTIMATE_SCHEMA_VERSION + '", '
        '"min_minutes": ' + str(min_m) + ', "max_minutes": ' + str(max_m) + ', "reason": ' + r + '}'
    )


class FakeCaller:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        if not self.outputs:
            return "{}"
        return self.outputs.pop(0)


def test_build_prompt_contains_distance_and_mode():
    system, user = build_time_estimate_prompt("平园", "31教", 1044, TransportMode.WALK)
    assert "1044" in user
    assert "walk" in user
    assert "不得修改" in system
    assert "高德" not in system or "不得声称" in system


def test_parse_valid():
    proposal = parse_time_estimate(time_json(10, 13, "ok"))
    assert proposal.min_minutes == 10
    assert proposal.max_minutes == 13


def test_parse_rejects_bool_and_float():
    for bad in ("true", "1.5"):
        with pytest.raises(AgenticParseError):
            parse_time_estimate(time_json(bad, 13))


def test_parse_rejects_negative_and_inverted():
    with pytest.raises(AgenticParseError):
        parse_time_estimate(time_json(-1, 13))
    with pytest.raises(AgenticParseError):
        parse_time_estimate(time_json(15, 13))


def test_parse_rejects_wrong_schema():
    with pytest.raises(AgenticParseError):
        parse_time_estimate(
            '{"schema_version": "p3.location-resolution.v1", "min_minutes": 1, '
            '"max_minutes": 2, "reason": null}'
        )


def test_qwen_absurd_estimate_clamped():
    caller = FakeCaller(time_json(1, 1, "超快"))
    estimate = estimate_travel_time(1044, TransportMode.WALK, caller)
    assert estimate.method is TimeEstimateMethod.QWEN
    assert 1 <= estimate.min_minutes <= estimate.estimated_minutes <= estimate.max_minutes
    assert estimate.max_minutes <= 27  # 1044m 步行物理上限约 27 分钟


def test_qwen_normal_estimate():
    caller = FakeCaller(time_json(10, 13))
    estimate = estimate_travel_time(1044, "walk", caller)
    assert estimate.estimated_minutes == 11
    assert (estimate.min_minutes, estimate.max_minutes) == (10, 13)
    assert estimate.method is TimeEstimateMethod.QWEN


def test_walk_bike_times_differ():
    walk = estimate_travel_time(1044, "walk", FakeCaller(time_json(10, 13)))
    bike = estimate_travel_time(1044, "bike", FakeCaller(time_json(4, 6)))
    assert walk.estimated_minutes > bike.estimated_minutes


def test_distance_enters_prompt():
    caller = FakeCaller(time_json(10, 13))
    estimate_travel_time(1044, "walk", caller)
    assert "1044" in caller.calls[0][1]


def test_fallback_without_caller():
    estimate = fallback_travel_time(1044, "walk")
    assert estimate.method is TimeEstimateMethod.PROGRAM_FALLBACK
    assert estimate.estimated_minutes > 0
    assert estimate.min_minutes <= estimate.estimated_minutes <= estimate.max_minutes


def test_fallback_on_model_failure():
    estimate = estimate_travel_time(1044, "walk", FakeCaller("不是 JSON"))
    assert estimate.method is TimeEstimateMethod.PROGRAM_FALLBACK
    assert "兜底" in (estimate.note or "")


def test_fallback_zero_without_distance():
    estimate = estimate_travel_time(None, "walk")
    assert (estimate.min_minutes, estimate.max_minutes, estimate.estimated_minutes) == (0, 0, 0)


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        estimate_travel_time(1000, "car")
