import json
from datetime import datetime, timedelta

from src.campus_map import load_campus_map
from src.p1_models import SourceKind
from src.p1_planning_models import PlanningStatus
from src.p1_planning_pipeline import run_p1_planning
from src.p1_result_formatter import format_p1_result
from src.p1_route_models import RouteDataTrust
from src.p1_route_provider import CampusMapRouteProvider


NOW = datetime(2026, 8, 16, 22, 36)


class FixedCaller(object):
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def __call__(self, system_prompt, user_prompt):
        self.calls += 1
        return self.reply


def compact_task(title, fragment, minutes, estimated_minutes=False, minimum=10,
                 location_requirement="no_specific_location"):
    extracted = [] if estimated_minutes else ["estimated_total_minutes"]
    estimated = ["location_requirement", "is_splittable", "minimum_slice_minutes",
                 "attention_required", "interruption_allowed", "may_have_open_hours"]
    if estimated_minutes:
        estimated.append("estimated_total_minutes")
    return {
        "title": title,
        "original_text": fragment,
        "features": {
            "location_requirement": location_requirement,
            "estimated_total_minutes": minutes,
            "is_splittable": True,
            "minimum_slice_minutes": minimum,
            "attention_required": "low" if title == "背单词" else "high",
            "interruption_allowed": True,
            "may_have_open_hours": False,
        },
        "extracted_fields": extracted,
        "estimated_fields": estimated,
        "confirmation_fields": [],
    }


def task_reply(tasks):
    return json.dumps({"schema_version": "p1.task-extraction.v2", "tasks": tasks}, ensure_ascii=False)


def window_reply(location, commitment_fragment, destination, minutes):
    return json.dumps({
        "schema_version": "p1.window-extraction.v2",
        "current_location": {"text": location, "fragment": location},
        "commitments": [{
            "title": "下一项固定安排",
            "original_text": commitment_fragment,
            "starts_at": (NOW + timedelta(minutes=minutes)).isoformat(),
            "ends_at": None,
            "location_text": destination,
            "availability_during": None,
        }],
        "free_duration_minutes": None,
        "ends_at": None,
        "user_buffer_minutes": None,
    }, ensure_ascii=False)


def candidate_reply(steps, rationale="优先利用当前空档完成任务"):
    return json.dumps({
        "schema_version": "p1.candidate.v1",
        "decision_status": "proposed",
        "primary_candidate": {
            "candidate_ref": "primary",
            "steps": [{"task_ref": task_ref, "planned_minutes": minutes,
                       "execution_context": "free_window", "commitment_ref": None}
                      for task_ref, minutes in steps],
            "rationale": rationale,
            "assumptions": [],
            "warnings": [],
        },
        "alternative_candidate": None,
        "safe_summary": "按当前信息生成暂定建议",
    }, ensure_ascii=False)


def run(user_text, task_output, window_output, candidate_output):
    provider = CampusMapRouteProvider(
        load_campus_map("tests/fixtures/sample_campus_map.json"),
        RouteDataTrust.SYNTHETIC_TEST,
    )
    candidate = FixedCaller(candidate_output)
    result = run_p1_planning(
        user_text, NOW, FixedCaller(task_output), FixedCaller(window_output),
        candidate, provider)
    return result, format_p1_result(result), candidate.calls


def test_real_scenario_one_extracts_two_tasks_and_uses_commitment_as_boundary():
    user = "我现在在宿舍，90分钟后要去教学楼上课，这段时间想背30分钟单词，再整理20分钟实验报告。"
    compact_tasks = [
        compact_task("背单词", "背30分钟单词", 30, minimum=15),
        compact_task("整理实验报告", "整理20分钟实验报告", 20, minimum=10),
    ]
    for task in compact_tasks:
        task.pop("extracted_fields")
        task.pop("estimated_fields")
        task.pop("confirmation_fields")
    tasks = task_reply(compact_tasks)
    window = window_reply("宿舍", "90分钟后要去教学楼上课", "教学楼", 90)
    result, view, calls = run(
        user, tasks, window,
        candidate_reply((("input-task-1", 30), ("input-task-2", 20))))
    assert len(result.context_bundle.tasks) == 2
    assert result.context_bundle.current_context.current_location_text == "宿舍"
    assert result.context_bundle.window_document.earliest_known_commitment.starts_at == NOW + timedelta(minutes=90)
    assert result.context_bundle.has_time_boundary
    assert calls == 1 and result.final_candidate is not None
    assert "多少可用时间" not in (result.primary_question or "")
    assert "多少可用时间" not in view.headline


def test_real_scenario_two_keeps_ai_estimate_and_returns_tentative_candidate():
    user = "我现在在图书馆，2小时后要去教学楼上课，这段时间想完成计组实验3，我没说需要多久，请你先估计。"
    tasks = task_reply([
        compact_task("完成计组实验3", "完成计组实验3", 120,
                     estimated_minutes=True, minimum=30,
                     location_requirement="location_requirement_unknown"),
    ])
    window = window_reply("图书馆", "2小时后要去教学楼上课", "教学楼", 120)
    result, view, calls = run(
        user, tasks, window,
        candidate_reply((("input-task-1", 60),), "完整任务较长，本窗口先推进一部分"))
    task = result.context_bundle.tasks[0]
    assert task.title == "完成计组实验3"
    assert task.field_evidence["estimated_total_minutes"].source is SourceKind.AI_ESTIMATED
    assert result.status is PlanningStatus.TENTATIVE_CANDIDATE
    assert result.tentative_candidate is not None
    assert result.tentative_candidate.steps[0].planned_minutes == 60
    assert "先推进一部分" in result.tentative_candidate.rationale
    assert result.primary_question is not None and "120" in result.primary_question
    assert "多少可用时间" not in result.primary_question
    assert calls == 1 and view.tentative_notice is not None


def test_real_scenario_three_unknown_real_places_keep_candidate_without_fake_route():
    user = "我现在在31教学楼，90分钟后要去北区菜鸟驿站，这段时间想背30分钟单词。"
    tasks = task_reply([compact_task("背单词", "背30分钟单词", 30, minimum=15)])
    window = window_reply("31教学楼", "90分钟后要去北区菜鸟驿站", "北区菜鸟驿站", 90)
    result, view, calls = run(user, tasks, window, candidate_reply((("input-task-1", 30),)))
    assert result.context_bundle.extraction_status.value == "complete"
    assert result.status is PlanningStatus.TENTATIVE_CANDIDATE
    assert result.tentative_candidate is not None and result.final_candidate is None
    assert result.final_route_validation is not None
    assert result.final_route_validation.total_walking_minutes is None
    assert result.final_route_validation.free_window_task_minutes == 30
    assert result.final_route_validation.segments == ()
    assert "location_requirement" not in result.context_bundle.tasks[0].needs_confirmation
    assert view.primary_card.free_window_task_minutes == 30
    assert view.primary_card.total_walking_minutes is None
    assert result.primary_question is not None
    assert view.primary_question is not None
    assert len(view.primary_question.quick_options) == 1
    option = view.primary_question.quick_options[0]
    assert option.label == "填写地点"
    assert option.action == "set_location"
    assert option.field_name in ("current_location_text", "location_text")
    assert option.target_ref is not None
    assert option.target_label is not None
    assert "多少可用时间" not in result.primary_question
    assert "模型返回格式" not in view.headline
    assert calls == 1
