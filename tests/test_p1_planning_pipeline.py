import json
from datetime import datetime

from src.campus_map import load_campus_map
from src.p1_planning_models import PlanningStatus
from src.p1_planning_pipeline import run_p1_planning
from src.p1_route_models import RouteDataTrust
from src.p1_route_provider import CampusMapRouteProvider
from src.p1_route_models import ProviderRouteStatus
from tests.test_p1_route_validator import FakeProvider
from tests.test_p1_candidate_parser import candidate, multi, payload as candidate_payload


NOW = datetime(2026, 5, 1, 10, 5)
USER = "我在宿舍，12:00去教学楼上课，背半小时单词"


def replies(values):
    values = list(values)
    calls = []
    def caller(system, user):
        calls.append((system, user))
        value = values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value
    caller.calls = calls
    return caller


def task_json(empty=False, ai=False, location_requirement="no_specific_location", location_text=None):
    if empty:
        tasks = []
    else:
        source = "ai_estimated" if ai else "ai_extracted_from_user_text"
        explanation = "用户没有说明用时，暂作合理估计" if ai else "用户明确说背半小时单词"
        fields = {name: {"source": source, "explanation": explanation} for name in (
            "location_requirement", "estimated_total_minutes", "is_splittable", "minimum_slice_minutes",
            "attention_required", "interruption_allowed", "may_have_open_hours")}
        if location_text is not None:
            fields["location_text"] = {"source": source, "explanation": explanation}
        tasks = [{"task_ref":"input-task-1", "title":"背单词", "original_text":"背半小时单词",
                  "understood_features":{"location_requirement":location_requirement,"location_text":location_text,
                  "environment_requirements":[],"equipment_requirements":[],"estimated_total_minutes":120,
                  "is_splittable":True,"minimum_slice_minutes":30,"attention_required":"low",
                  "interruption_allowed":True,"may_have_open_hours":False},
                  "field_evidence":fields,"needs_confirmation":(["location_text"] if location_requirement == "specific_location" and location_text is None else [])}]
    return json.dumps({"schema_version":"p1.task-understanding.v1","task_interpretations":tasks,"clarification_questions":[]}, ensure_ascii=False)


def window_json(current="宿舍", next_location="教学楼", commitment=True, free=None):
    commitments = [] if not commitment else [{"commitment_ref":"class-1","title":"上课","original_text":"12:00去教学楼上课",
       "starts_at":"2026-05-01T12:00:00","ends_at":None,"location_text":next_location,"availability_during":None,
       "field_evidence":{"starts_at":{"source":"ai_extracted_from_user_text","explanation":"用户明确说12点"},
       **({} if next_location is None else {"location_text":{"source":"ai_extracted_from_user_text","explanation":"用户明确说教学楼"}})},
       "needs_confirmation":[] if next_location is not None else ["location_text"]}]
    current_evidence = {} if current is None else {"current_location_text":{"source":"ai_extracted_from_user_text","explanation":"用户明确说在宿舍"}}
    return json.dumps({"schema_version":"p1.window-context.v1","current_context":{"current_location_text":current,
       "current_location_fragment":current,"assumed_current_datetime":None,"assumed_current_datetime_fragment":None,
       "field_evidence":current_evidence,"needs_confirmation":[] if current is not None else ["current_location_text"]},
       "commitments":commitments,"window_constraints":{"free_duration_minutes":free,"ends_at":None,"user_buffer_minutes":None,"field_evidence":({} if free is None else {"free_duration_minutes":{"source":"ai_extracted_from_user_text","explanation":"用户明确说空闲时长"}}),"needs_confirmation":([] if commitment or free is not None else ["free_duration_minutes"])},"clarification_questions":[]}, ensure_ascii=False)


def plan_candidate(ref="primary", minutes=30):
    return candidate(ref, task="input-task-1", minutes=minutes)


def plan_multi(minutes):
    value = multi(minutes)
    for step in value["steps"]:
        step["task_ref"] = "input-task-1"
    return value


def provider(trust=RouteDataTrust.SYNTHETIC_TEST):
    assert trust is RouteDataTrust.SYNTHETIC_TEST
    return CampusMapRouteProvider(load_campus_map("tests/fixtures/sample_campus_map.json"), RouteDataTrust.SYNTHETIC_TEST)


class VerifiedRouteProvider(FakeProvider):
    """专用测试假提供者：不加载 sample fixture，明确模拟已验证真实数据。"""
    route_data_trust = RouteDataTrust.REAL_VERIFIED


def run(task, window, candidates, trust=RouteDataTrust.SYNTHETIC_TEST, route_provider=None):
    return run_p1_planning(USER, NOW, replies(task), replies(window), replies(candidates), provider(trust) if route_provider is None else route_provider)


def test_complete_flow_keeps_synthetic_calculation_tentative_and_counts_calls():
    result = run([task_json()], [window_json()], [candidate_payload(plan_candidate())])
    assert result.status is PlanningStatus.TENTATIVE_CANDIDATE
    assert result.final_candidate is not None and result.is_tentative
    assert result.call_summary.task_attempts == result.call_summary.window_attempts == 1
    assert result.call_summary.candidate_calls == 1


def test_real_verified_without_ai_or_confirmation_can_be_final():
    result = run([task_json()], [window_json()], [candidate_payload(plan_candidate())], route_provider=VerifiedRouteProvider())
    assert result.status is PlanningStatus.FINAL_CANDIDATE and not result.is_tentative


def test_alternative_promotion_and_route_regeneration_are_delegated_to_p1g():
    promoted = run([task_json()], [window_json(free=70)], [candidate_payload(plan_multi([30, 30]), plan_candidate("alt", 30))])
    assert promoted.final_candidate.candidate_ref == "alt" and promoted.call_summary.candidate_calls == 1
    regenerated = run([task_json()], [window_json(free=70)], [candidate_payload(plan_multi([30, 30])), candidate_payload(plan_candidate("new", 30))])
    assert regenerated.final_candidate.candidate_ref == "new" and regenerated.call_summary.candidate_regenerated


def test_two_infeasible_candidates_and_candidate_failures_are_not_hidden():
    result = run([task_json()], [window_json(free=70)], [candidate_payload(plan_multi([30, 30])), candidate_payload(plan_multi([30, 30]))])
    assert result.status is PlanningStatus.CANDIDATES_INFEASIBLE
    failed = run([task_json()], [window_json()], ["bad", "still bad"])
    assert failed.status is PlanningStatus.CANDIDATE_FAILED and failed.call_summary.candidate_calls == 2


def test_partial_or_failed_extraction_never_calls_candidate():
    task_fail = run(["bad", "bad"], [window_json()], [RuntimeError("candidate should not run")])
    window_fail = run([task_json()], ["bad", "bad"], [RuntimeError("candidate should not run")])
    both = run(["bad", "bad"], ["bad", "bad"], [RuntimeError("candidate should not run")])
    assert task_fail.status is PlanningStatus.PARTIAL_EXTRACTION and task_fail.call_summary.candidate_calls == 0
    assert window_fail.status is PlanningStatus.PARTIAL_EXTRACTION and window_fail.call_summary.candidate_calls == 0
    assert both.status is PlanningStatus.EXTRACTION_FAILED and both.call_summary.candidate_calls == 0


def test_empty_tasks_skips_candidate_and_asks_for_task_input():
    result = run([task_json(empty=True)], [window_json()], [RuntimeError("candidate should not run")])
    assert result.status is PlanningStatus.NO_EXECUTABLE_TASKS and result.call_summary.candidate_calls == 0
    assert "想完成什么" in result.primary_question


def test_ai_estimates_do_not_block_and_remain_visible():
    result = run([task_json(ai=True)], [window_json()], [candidate_payload(plan_candidate())], route_provider=VerifiedRouteProvider())
    assert result.final_candidate is not None and result.is_tentative
    assert result.ai_estimated_fields


def test_ai_estimate_without_existing_question_gets_specific_fallback_question():
    result = run([task_json(ai=True)], [window_json()], [candidate_payload(plan_candidate())], route_provider=VerifiedRouteProvider())
    assert result.primary_question is not None
    assert "背单词" in result.primary_question and "120" in result.primary_question
    assert result.context_bundle.ai_estimated_task_fields[0].evidence.source.value == "ai_estimated"


def test_higher_priority_location_question_stays_ahead_of_ai_fallback():
    result = run([task_json(ai=True)], [window_json(current=None)], [candidate_payload(plan_candidate())], route_provider=VerifiedRouteProvider())
    assert result.primary_question is not None
    assert "现在" in result.primary_question or "哪里" in result.primary_question
    assert all("背单词" not in question for question in (result.primary_question,))


def test_sample_fixture_remains_synthetic_in_product_result():
    result = run([task_json()], [window_json()], [candidate_payload(plan_candidate())])
    assert result.final_route_validation.route_data_trust is RouteDataTrust.SYNTHETIC_TEST
    assert result.is_tentative


def test_missing_location_or_commitment_location_keeps_tentative_candidate_and_question():
    current = run([task_json()], [window_json(current=None)], [candidate_payload(plan_candidate())])
    next_location = run([task_json()], [window_json(next_location=None)], [candidate_payload(plan_candidate())])
    assert current.tentative_candidate is not None and current.primary_question is not None
    assert next_location.tentative_candidate is not None and next_location.primary_question is not None


def test_missing_time_boundary_keeps_tentative_candidate_and_questions_are_deduplicated():
    result = run([task_json()], [window_json(commitment=False)], [candidate_payload(plan_candidate())])
    assert result.status is PlanningStatus.TENTATIVE_CANDIDATE and result.tentative_candidate is not None
    assert result.primary_question is not None
    assert result.primary_question not in result.other_questions


def test_missing_task_location_and_route_data_shortage_keep_tentative_candidate():
    task_location = run([task_json(location_requirement="specific_location")], [window_json()], [candidate_payload(plan_candidate())])
    data_shortage = run([task_json(location_requirement="specific_location", location_text="图书馆")], [window_json()], [candidate_payload(plan_candidate())], route_provider=FakeProvider(route_status=ProviderRouteStatus.DATA_INSUFFICIENT))
    assert task_location.status is PlanningStatus.TENTATIVE_CANDIDATE and task_location.tentative_candidate is not None
    assert data_shortage.status is PlanningStatus.TENTATIVE_CANDIDATE and data_shortage.tentative_candidate is not None


def test_candidate_callable_two_failures_and_primary_question_are_safe():
    result = run([task_json()], [window_json()], [RuntimeError("Authorization Bearer https://secret"), RuntimeError("Authorization Bearer https://secret")])
    assert result.status is PlanningStatus.CANDIDATE_FAILED and result.call_summary.candidate_calls == 2
    assert "Bearer" not in result.safe_summary


def test_input_sources_are_not_mutated_and_errors_are_safe():
    task = replies([task_json()])
    window = replies([window_json()])
    candidate_call = replies([RuntimeError("Authorization Bearer https://secret"), RuntimeError("Authorization Bearer https://secret")])
    result = run_p1_planning(USER, NOW, task, window, candidate_call, provider())
    assert result.context_bundle.tasks[0].field_evidence["estimated_total_minutes"].source.value == "ai_extracted_from_user_text"
    shown = result.safe_summary + " " + (result.primary_question or "")
    assert "Bearer" not in shown and "secret" not in shown
