import pytest

from src.p5_agent_benchmark import (
    AgentBenchmarkScenario,
    AgentScenarioExpectation,
    AgentScenarioObservation,
    evaluate_agent_scenario,
    run_agent_benchmark,
)


_CATEGORY_COUNTS = (
    ("initial_timing", 6),
    ("relative_event_semantics", 5),
    ("meal", 7),
    ("splitting", 6),
    ("feedback", 6),
    ("multiple_commitments", 4),
    ("concurrency", 7),
    ("day_preference", 5),
    ("what_if", 4),
    ("proactive_suggestion", 4),
    ("copy_grounding", 3),
    ("adversarial_language", 3),
)


def _scenario(category, index):
    task_a = "{}_work_{:02d}".format(category, index)
    task_b = "{}_followup_{:02d}".format(category, index)
    class_ref = "{}_commitment_{:02d}".format(category, index)
    before = ((task_a, task_b),)
    completion = ((task_a, 30), (task_b, 20))
    remaining = ((task_a, 30), (task_b, 10))
    pairs = ((task_b, class_ref),) if category == "concurrency" and index % 2 == 0 else ()
    forbidden_pairs = ((task_a, class_ref),) if category == "concurrency" else ()
    timeline = ["14:00–14:30：写作业 30 分钟"]
    expected_phrase = ()
    meal_windows = ()
    meal_starts = ()
    if category == "relative_event_semantics":
        end_hour = 20 + (index % 2)
        timeline.append("19:00–{}:30：固定安排".format(end_hour))
        expected_phrase = ("19:00–{}:30：固定安排".format(end_hour),)
    elif category == "meal":
        # Exercise lunch, dinner and missed-meal recovery facts rather than
        # cloning one golden string sixty times.
        windows = ((660, 780), (1020, 1140), (794, 1020))
        starts_at, ends_at = windows[index % len(windows)]
        actual_start = starts_at + min(35, index * 5)
        meal_ref = task_b
        meal_windows = ((meal_ref, starts_at, ends_at),)
        meal_starts = ((meal_ref, actual_start),)
        timeline.append("饭点内安排一顿饭")
        expected_phrase = ("一顿饭",)
    elif category == "splitting":
        first = 20 + index * 2
        completion = ((task_a, first), (task_b, 20))
        remaining = ((task_a, max(0, 60 - first)), (task_b, 10))
        timeline.extend(("第一段任务", "固定安排", "继续同一任务"))
    elif category == "feedback":
        timeline.append("最新明确任务优先")
    elif category == "multiple_commitments":
        timeline.extend(("15:00–16:00：会议", "19:00–20:30：上课"))
    elif category == "concurrency" and pairs:
        timeline.append("19:20–19:40：同时执行已授权任务")
    elif category == "day_preference":
        timeline.append("保留当天偏好的余量")
    elif category == "what_if":
        timeline.append("仅生成假设预览")
    elif category == "proactive_suggestion":
        timeline.append("当前可靠方案保持不变")
    elif category == "copy_grounding":
        timeline.append("去学三食堂吃饭")
    elif category == "adversarial_language":
        timeline.extend(("报告", "晚饭", "课程", "下课后任务"))
    else:
        timeline.append("17:00–18:30：上课")
    clarification = category == "multiple_commitments" and index == 3
    suggestion = category == "proactive_suggestion" and index % 2 == 0
    canonical_before = "stable-turn-{}".format(index) if category == "what_if" else None
    canonical_after = canonical_before if category == "what_if" else None
    return AgentBenchmarkScenario(
        "{}_{:02d}".format(category, index + 1),
        category,
        AgentScenarioExpectation(
            required_before=before,
            required_concurrency=pairs,
            forbidden_concurrency=forbidden_pairs,
            expected_completion=completion,
            expected_remaining=remaining,
            clarification_expected=clarification,
            suggestion_expected=suggestion,
            required_timeline_phrases=expected_phrase,
            forbidden_copy_phrases=("去学三背单词", "已经完成写作业"),
            expected_meal_windows=meal_windows,
            forbidden_overlap_pairs=((task_a, class_ref),),
            canonical_state_unchanged=(True if category == "what_if" else None),
        ),
        AgentScenarioObservation(
            task_sequence=(task_a, task_b),
            concurrency_pairs=pairs,
            task_completion=completion,
            remaining_work=remaining,
            clarification_shown=clarification,
            suggestion_shown=suggestion,
            timeline=tuple(timeline),
            user_copy="先推进一段作业，固定安排和路上时间已经留好。",
            meal_start_minutes=meal_starts,
            overlap_pairs=pairs,
            canonical_fingerprint_before=canonical_before,
            canonical_fingerprint_after=canonical_after,
        ),
    )


SCENARIOS = tuple(
    _scenario(category, index)
    for category, count in _CATEGORY_COUNTS
    for index in range(count)
)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda value: value.scenario_id)
def test_agent_benchmark_scenario(scenario):
    result = evaluate_agent_scenario(scenario)
    assert result.passed, result.failures


def test_agent_benchmark_report_has_at_least_fifty_meaningful_scenarios():
    report = run_agent_benchmark(SCENARIOS)
    assert report.total == 60
    assert report.passed == 60
    assert report.failed == 0
    assert report.failed_scenario_ids == ()
    assert {item.category for item in SCENARIOS} == {
        category for category, _ in _CATEGORY_COUNTS
    }


def test_benchmark_detects_broken_order_and_ungrounded_copy():
    broken = AgentBenchmarkScenario(
        "adversarial_broken", "adversarial_language",
        AgentScenarioExpectation(
            required_before=(("report", "meal"),),
            forbidden_copy_phrases=("去学三背单词",),
        ),
        AgentScenarioObservation(
            task_sequence=("meal", "report"),
            user_copy="到点去学三背单词。",
        ),
    )
    result = evaluate_agent_scenario(broken)
    assert not result.passed
    assert len(result.failures) == 2


def test_benchmark_detects_expired_meal_window_and_what_if_state_mutation():
    broken = AgentBenchmarkScenario(
        "meal_whatif_broken", "adversarial_language",
        AgentScenarioExpectation(
            expected_meal_windows=(("meal", 660, 780),),
            canonical_state_unchanged=True,
        ),
        AgentScenarioObservation(
            meal_start_minutes=(("meal", 900),),
            canonical_fingerprint_before="turn-a",
            canonical_fingerprint_after="turn-b",
        ),
    )
    result = evaluate_agent_scenario(broken)
    assert not result.passed
    assert set(result.failures) == {
        "meal window mismatch: meal",
        "canonical mutation expectation mismatch",
    }


def test_benchmark_rejects_duplicate_scenario_ids():
    with pytest.raises(ValueError, match="unique"):
        run_agent_benchmark((SCENARIOS[0], SCENARIOS[0]))
