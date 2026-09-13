import json
from datetime import datetime, timedelta
from types import SimpleNamespace

from src.campus_map import load_campus_map
from src.p1_dual_planning_pipeline import (
    MAX_MODEL_CALLS_PER_ACTION,
    run_p1_dual_planning,
)
from src.p1_planning_models import PlanningStatus
from src.p1_models import AttentionLevel, LocationRequirement
from src.p1_route_models import CandidateValidationStatus, RouteDataTrust
from src.p1_route_provider import CampusMapRouteProvider
from src.p1_view_models import P1ResultView, QuestionView, QuickOptionView


NOW = datetime(2026, 8, 17, 10, 0)


def empty_view():
    return P1ResultView("strict", "strict", None, None, None, None, None,
                        None, (), (), (), False)


def structured(status, calls=4, validation=None, tasks=(), window=None):
    return SimpleNamespace(
        status=status,
        context_bundle=SimpleNamespace(tasks=tasks, window_document=window),
        final_route_validation=validation,
        call_summary=SimpleNamespace(
            task_attempts=2 if calls >= 4 else 1,
            window_attempts=2 if calls >= 4 else 1,
            candidate_calls=max(0, calls - (4 if calls >= 4 else 2)),
        ),
    )


def estimated_task(minutes=120):
    features = SimpleNamespace(
        estimated_total_minutes=minutes, minimum_slice_minutes=30,
        is_splittable=True,
        location_requirement=LocationRequirement.LOCATION_REQUIREMENT_UNKNOWN,
        location_text=None, attention_required=AttentionLevel.HIGH)
    return SimpleNamespace(
        task_ref="input-task-1", title="完成计组实验3", features=features,
        needs_confirmation=("estimated_total_minutes", "location_requirement"))


def outputs(title, minutes, walk=(10, 20), revise=False, steps=None):
    step_values = ([{"title": item[0], "minutes": item[1],
                     "timing_note": "之后预留步行和缓冲"} for item in steps]
                   if steps is not None else
                   [{"title": title, "minutes": minutes,
                     "timing_note": "之后预留步行和缓冲"}])
    plan = json.dumps({
        "status": "proposed",
        "headline": "先执行当前任务",
        "steps": step_values,
        "estimated_walking_minutes_min": walk[0],
        "estimated_walking_minutes_max": walk[1],
        "assumptions": ["路线为 AI 暂估"], "warnings": [],
    }, ensure_ascii=False)
    review = json.dumps({"decision": "revise" if revise else "accept",
                         "feedback": "安排更短片段"}, ensure_ascii=False)
    if revise:
        revised = json.dumps({
            "status": "proposed", "steps": [{"title": title, "minutes": 60}],
            "assumptions": [], "warnings": []}, ensure_ascii=False)
        return [plan, review, revised, "建议先推进 60 分钟。"]
    return [plan, review, "建议现在先做这项任务。"]


class Caller(object):
    def __init__(self, values):
        self.values = list(values)
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class Provider(object):
    route_data_trust = RouteDataTrust.SYNTHETIC_TEST


class Fixed(object):
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def __call__(self, system, user):
        self.calls += 1
        return self.value


def compact_task_reply(title, fragment, minutes, location="no_specific_location"):
    return json.dumps({
        "schema_version": "p1.task-extraction.v2",
        "tasks": [{
            "title": title, "original_text": fragment,
            "features": {
                "location_requirement": location,
                "estimated_total_minutes": minutes,
                "is_splittable": True, "minimum_slice_minutes": 30,
                "attention_required": "high", "interruption_allowed": True,
                "may_have_open_hours": False,
            },
        }],
    }, ensure_ascii=False)


def compact_window_reply(user, location, commitment_fragment, destination, minutes):
    return json.dumps({
        "schema_version": "p1.window-extraction.v2",
        "current_location": {"text": location, "fragment": location},
        "commitments": [{
            "title": "下一项固定安排", "original_text": commitment_fragment,
            "starts_at": (NOW.replace(second=0) + timedelta(minutes=minutes)).isoformat(),
            "ends_at": None, "location_text": destination,
            "availability_during": None,
        }],
        "free_duration_minutes": None, "ends_at": None,
        "user_buffer_minutes": None,
    }, ensure_ascii=False)


def candidate_reply(task_ref, minutes):
    return json.dumps({
        "schema_version": "p1.candidate.v1", "decision_status": "proposed",
        "primary_candidate": {
            "candidate_ref": "primary", "steps": [{
                "task_ref": task_ref, "planned_minutes": minutes,
                "execution_context": "free_window", "commitment_ref": None}],
            "rationale": "先推进当前任务", "assumptions": [], "warnings": []},
        "alternative_candidate": None, "safe_summary": "暂定建议",
    }, ensure_ascii=False)


def actual_dual(user, task_output, window_output, candidate_output, agent_values):
    agent = Caller(agent_values)
    result = run_p1_dual_planning(
        user, NOW, Fixed(task_output), Fixed(window_output), Fixed(candidate_output),
        CampusMapRouteProvider(
            load_campus_map("tests/fixtures/sample_campus_map.json"),
            RouteDataTrust.SYNTHETIC_TEST),
        agent_caller=agent)
    return result, agent


def run_with(original, strict, caller, interaction=None):
    return run_p1_dual_planning(
        original, NOW, lambda s, u: "", lambda s, u: "", lambda s, u: "",
        Provider(), agent_caller=caller, interaction_context=interaction,
        planning_function=lambda *args: strict, formatter=lambda value: empty_view())


def test_strict_final_candidate_keeps_channel_a_without_agent_calls():
    caller = Caller([])
    result = run_with("想背单词", structured(PlanningStatus.FINAL_CANDIDATE, 3), caller)
    assert not result.used_agent_fallback and result.total_model_calls == 3
    assert caller.calls == [] and result.view.status_title == "strict"


def test_task_extraction_failure_uses_agent_fallback_for_real_scenario_one():
    text = "我现在在宿舍，90分钟后要去教学楼上课，这段时间想背30分钟单词，再整理20分钟实验报告。"
    caller = Caller(outputs("背单词，再整理实验报告", 50))
    result = run_with(text, structured(PlanningStatus.PARTIAL_EXTRACTION, 4), caller)
    assert result.used_agent_fallback and result.agent_result.call_count == 3
    assert result.view.primary_card.total_task_minutes == 50
    assert text in caller.calls[0][1]
    assert "模型输出" not in result.view.headline


def test_candidate_failure_uses_agent_fragment_for_real_scenario_two():
    text = "我现在在图书馆，2小时后要去教学楼上课，这段时间想完成计组实验3，我没说需要多久，请你先估计。"
    caller = Caller(outputs("推进计组实验3", 75))
    result = run_with(text, structured(
        PlanningStatus.CANDIDATE_FAILED, 4, tasks=(estimated_task(),)), caller)
    assert result.view.primary_card.steps[0].planned_minutes == 75
    assert result.view.tentative_notice is not None
    assert result.view.primary_question is not None
    assert "120" in result.view.primary_question.text
    assert result.view.primary_question.quick_options[0].action == "confirm_estimate"
    assert "2小时后" in caller.calls[0][1]


def test_unresolved_test_map_location_uses_estimated_route_without_reasking_current_location():
    text = "我现在在31教学楼，90分钟后要去北区菜鸟驿站，这段时间想背30分钟单词。"
    validation = SimpleNamespace(
        status=CandidateValidationStatus.LOCATION_UNRESOLVED,
        route_data_trust=RouteDataTrust.SYNTHETIC_TEST,
        total_walking_minutes=None, free_window_task_minutes=30,
        safe_reason="演示地图无法识别地点")
    caller = Caller(outputs("背单词", 30, walk=(15, 25)))
    result = run_with(text, structured(
        PlanningStatus.TENTATIVE_CANDIDATE, 3, validation=validation), caller)
    assert "AI 暂估步行约 15～25 分钟" in result.view.route_data_notice.message
    assert result.view.primary_question is None
    assert "31教学楼" in caller.calls[0][1]


def test_confirm_120_and_adjust_60_are_independent_interaction_context_not_rewritten_input():
    original = "我想完成计组实验3，请你先估计。"
    for value in (120, 60):
        interaction = {
            "original_user_input": original,
            "previous_suggestion": {"headline": "先推进一部分", "steps": []},
            "confirmation": {"action": "adjust_duration", "field_name": "estimated_total_minutes",
                             "target_label": "完成计组实验3", "selected_value": None,
                             "user_value": value},
        }
        caller = Caller(outputs("推进计组实验3", min(value, 60)))
        result = run_with(original, structured(
            PlanningStatus.CANDIDATE_FAILED, 4, tasks=(estimated_task(),)), caller, interaction)
        prompt = caller.calls[0][1]
        assert prompt.count(original) == 1
        assert str(value) in prompt
        assert result.view.primary_card is not None
        assert result.view.primary_question is None


def test_reviewer_revision_respects_total_ten_call_hard_limit():
    caller = Caller(outputs("推进实验", 120, revise=True))
    result = run_with("完成实验", structured(PlanningStatus.CANDIDATE_FAILED, 6), caller)
    assert result.agent_result.call_count == 4
    assert result.total_model_calls == MAX_MODEL_CALLS_PER_ACTION == 10
    assert len(caller.calls) == 4


def test_reflected_confirmation_keeps_strict_result_and_removes_duplicate_question():
    task = estimated_task()
    strict = structured(PlanningStatus.FINAL_CANDIDATE, 3, tasks=(task,))
    question = QuestionView("120 分钟合适吗？", (
        QuickOptionView("就按这个", "confirm_estimate", "120", "input-task-1",
                        "estimated_total_minutes", "完成计组实验3"),))
    view = P1ResultView("严格方案", "先推进实验", None, None, None, None, None,
                        question, (), (), (), False)
    interaction = {"confirmation": {
        "action": "confirm_estimate", "selected_value": "120", "user_value": None,
        "target_ref": "input-task-1", "field_name": "estimated_total_minutes",
        "target_label": "完成计组实验3"}}
    caller = Caller([])
    result = run_p1_dual_planning(
        "完成计组实验3", NOW, lambda s, u: "", lambda s, u: "", lambda s, u: "",
        Provider(), agent_caller=caller, interaction_context=interaction,
        planning_function=lambda *args: strict, formatter=lambda value: view)
    assert not result.used_agent_fallback
    assert result.view.primary_question is None
    assert caller.calls == []


def test_all_fallback_calls_fail_only_then_show_safe_failure():
    caller = Caller([RuntimeError("Authorization Bearer https://invalid raw JSON")])
    result = run_with("想背单词，半小时后出发", structured(
        PlanningStatus.EXTRACTION_FAILED, 4), caller)
    assert result.view.status_title == "暂时无法完成规划"
    visible = result.view.status_title + result.view.headline
    assert "Authorization" not in visible and "https://" not in visible


def test_actual_scenario_one_task_schema_failure_still_returns_executable_agent_plan():
    user = "我现在在宿舍，90分钟后要去教学楼上课，这段时间想背30分钟单词，再整理20分钟实验报告。"
    window = compact_window_reply(user, "宿舍", "90分钟后要去教学楼上课", "教学楼", 90)
    result, agent = actual_dual(
        user, "not-json", window, "not-used",
        outputs("", 50, steps=(("背单词", 30), ("整理实验报告", 20))))
    assert result.used_agent_fallback and result.view.primary_card is not None
    assert result.view.primary_card.total_task_minutes == 50
    assert [(item.title, item.planned_minutes) for item in result.view.primary_card.steps] == [
        ("背单词", 30), ("整理实验报告", 20)]
    assert result.total_model_calls <= 10 and len(agent.calls) == 3


def test_actual_scenario_two_candidate_schema_failure_still_plans_task_fragment():
    user = "我现在在图书馆，2小时后要去教学楼上课，这段时间想完成计组实验3，我没说需要多久，请你先估计。"
    result, _ = actual_dual(
        user,
        compact_task_reply("完成计组实验3", "完成计组实验3", 120,
                           "location_requirement_unknown"),
        compact_window_reply(user, "图书馆", "2小时后要去教学楼上课", "教学楼", 120),
        "not-json", outputs("推进计组实验3", 75))
    assert result.view.primary_card.steps[0].planned_minutes == 75
    assert result.view.primary_question is not None
    assert "120" in result.view.primary_question.text
    assert result.total_model_calls <= 10


def test_actual_scenario_three_unknown_map_places_get_ai_route_range_not_location_reask():
    user = "我现在在31教学楼，90分钟后要去北区菜鸟驿站，这段时间想背30分钟单词。"
    result, _ = actual_dual(
        user, compact_task_reply("背单词", "背30分钟单词", 30),
        compact_window_reply(user, "31教学楼", "90分钟后要去北区菜鸟驿站", "北区菜鸟驿站", 90),
        candidate_reply("input-task-1", 30), outputs("背单词", 30, walk=(15, 25)))
    assert result.view.primary_question is None
    assert "AI 暂估步行约 15～25 分钟" in result.view.route_data_notice.message
    assert result.view.primary_card.free_window_task_minutes == 30
    assert result.view.primary_card.total_walking_minutes is None
    assert result.agent_result.status.value == "proposed"
    assert result.agent_result.draft.steps[0].title == "背单词"


def test_dual_channel_refuses_to_render_empty_agent_title_as_tentative_plan():
    caller = Caller([
        '{"status":"proposed","headline":"方案待补充信息","steps":[]}',
        '{"status":"needs_information","headline":"继续补充"}',
    ])
    result = run_with("我有 90 分钟想背单词", structured(
        PlanningStatus.PARTIAL_EXTRACTION, 6), caller)
    assert result.agent_result.view is None
    assert result.view.primary_card is None
    assert result.view.status_title == "暂时无法完成规划"
    assert result.total_model_calls <= MAX_MODEL_CALLS_PER_ACTION


def test_each_new_operation_gets_a_fresh_ten_call_budget():
    for _ in range(2):
        caller = Caller(outputs("推进实验", 120, revise=True))
        result = run_with("完成实验", structured(PlanningStatus.CANDIDATE_FAILED, 6), caller)
        assert result.total_model_calls == 10
        assert len(caller.calls) == 4


def test_strict_parse_failure_and_agent_call_failure_are_recorded_safely():
    class NetworkError(Exception):
        pass

    caller = Caller([NetworkError("https://invalid Authorization Bearer secret"),
                     NetworkError("https://invalid Authorization Bearer secret")])
    result = run_with("我有90分钟，想背30分钟单词", structured(
        PlanningStatus.PARTIAL_EXTRACTION, 4), caller)
    records = result.diagnostics
    assert any(item.stage.value == "task_extraction" for item in records)
    assert any(item.stage.value == "agent_generator" and item.status.value == "call_failed"
               and item.error_category.value == "network" for item in records)
    assert result.total_model_calls == 7
    assert result.view.diagnostics


def test_real_scenario_one_natural_agent_text_never_becomes_generic_failure():
    user = "我现在在宿舍，90分钟后要去教学楼上课，这段时间想背30分钟单词，再整理20分钟实验报告。"
    window = compact_window_reply(user, "宿舍", "90分钟后要去教学楼上课", "教学楼", 90)
    result, _ = actual_dual(
        user, "not-json", window, "not-used", [
            "现在先背单词 30 分钟，再整理实验报告 20 分钟；之后预留 AI 暂估步行和 10 分钟缓冲，前往教学楼。",
            "可以继续", "先完成两项短任务后准备出发。",
        ])
    assert result.view.primary_card is not None
    assert "背单词 30 分钟" in result.view.primary_card.rationale
    assert "整理实验报告 20 分钟" in result.view.primary_card.rationale
    assert result.view.status_title == "当前方案"


def test_real_scenario_two_natural_agent_text_plans_a_long_task_fragment():
    user = "我现在在图书馆，2小时后要去教学楼上课，这段时间想完成计组实验3，我没说需要多久，请你先估计。"
    result, _ = actual_dual(
        user,
        compact_task_reply("完成计组实验3", "完成计组实验3", 120,
                           "location_requirement_unknown"),
        compact_window_reply(user, "图书馆", "2小时后要去教学楼上课", "教学楼", 120),
        "not-json", [
            "AI 暂估计组实验3完整需要约 120 分钟。现在先推进 60 分钟，之后预留出发和缓冲时间。",
            "可以继续", "先推进一部分计组实验3。",
        ])
    assert result.view.primary_card is not None
    assert "推进 60 分钟" in result.view.primary_card.rationale
    assert "120 分钟" in result.view.primary_card.rationale
    assert result.view.primary_question is not None


def test_real_scenario_three_natural_agent_text_keeps_task_when_route_is_unknown():
    user = "我现在在31教学楼，90分钟后要去北区菜鸟驿站，这段时间想背30分钟单词。"
    result, _ = actual_dual(
        user, compact_task_reply("背单词", "背30分钟单词", 30),
        compact_window_reply(user, "31教学楼", "90分钟后要去北区菜鸟驿站", "北区菜鸟驿站", 90),
        candidate_reply("input-task-1", 30), [
            "现在先背单词 30 分钟。AI 暂估从31教学楼到北区菜鸟驿站步行约 15～25 分钟，请提前出发并预留缓冲。",
            "可以继续", "先背 30 分钟单词。",
        ])
    assert result.view.primary_card is not None
    assert "背单词 30 分钟" in result.view.primary_card.rationale
    assert "AI 暂估" in result.view.primary_card.rationale
    assert result.view.primary_question is None
    assert "31教学楼" in result.view.primary_card.rationale


def test_direct_answer_can_use_the_last_slot_after_seven_structured_calls():
    caller = Caller([
        "方案待补充信息", "已审查方案整理完成",
        "立即开始背单词半小时，之后提前出发。",
    ])
    result = run_with(
        "我有90分钟，想背单词", structured(PlanningStatus.CANDIDATE_FAILED, 7), caller)
    assert result.total_model_calls == MAX_MODEL_CALLS_PER_ACTION
    assert result.view.primary_card is not None
    assert "背单词半小时" in result.view.primary_card.rationale
    assert any(item.stage.value == "agent_direct_answer" for item in result.diagnostics)
