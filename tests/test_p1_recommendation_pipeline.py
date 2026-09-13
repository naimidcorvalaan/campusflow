import json
from dataclasses import replace

import pytest

from tests.test_p1_candidate_parser import candidate, multi, payload
from tests.test_p1_route_validator import FakeProvider, provider, route_bundle
from src.p1_candidate_models import CandidateDecisionStatus
from src.p1_models import LocationRequirement
from src.p1_recommendation_models import (QuickAction, RecommendationStatus,
                                          SecondCallReason)
from src.p1_recommendation_pipeline import run_p1_recommendation
from src.p1_route_models import LocationResolutionStatus, ProviderRouteStatus


def sequence(values, captured=None):
    values = list(values)
    def invoke(system, user):
        if captured is not None:
            captured.append((system, user))
        value = values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value
    return invoke


def missing_payload(status):
    return payload(None, status=status)


def test_feasible_primary_calls_once_and_preserves_bundle_objects():
    bundle = route_bundle()
    result = run_p1_recommendation(bundle, sequence([payload(candidate())]), provider())
    assert result.status is RecommendationStatus.SELECTED_PRIMARY and result.call_count == 1
    assert result.first_candidate_result.document is not None
    assert result.final_candidate is result.first_candidate_result.document.primary_candidate
    assert bundle.window_document is bundle.window_result.document


def test_feasible_alternative_is_promoted_without_second_call():
    bundle = route_bundle(free=70)
    first = payload(multi([30, 30]), candidate("alt", minutes=30))
    result = run_p1_recommendation(bundle, sequence([first]), provider())
    assert result.status is RecommendationStatus.SELECTED_ALTERNATIVE and result.call_count == 1
    assert result.final_candidate.candidate_ref == "alt"


def test_two_time_infeasible_candidates_trigger_one_route_regeneration_then_success():
    bundle = route_bundle(free=70)
    captured = []
    result = run_p1_recommendation(bundle, sequence([payload(multi([30, 30])), payload(candidate("new", minutes=30))], captured), provider())
    assert result.status is RecommendationStatus.REGENERATED_SUCCESS and result.call_count == 2
    assert result.second_call_reason is SecondCallReason.ROUTE_REGENERATION and result.regenerated
    feedback = captured[1][1]
    assert "time_infeasible" in feedback and "synthetic_test" in feedback
    assert payload(candidate()) not in feedback and "不要重复原方案" in feedback


def test_second_route_failure_stops_without_third_call_and_offers_actions():
    bundle = route_bundle(free=35)
    result = run_p1_recommendation(bundle, sequence([payload(candidate()), payload(candidate("same-time", minutes=30))]), provider())
    assert result.status is RecommendationStatus.BOTH_CANDIDATES_INFEASIBLE and result.call_count == 2
    assert QuickAction.EXTEND_TIME in result.quick_actions


def test_schema_repair_success_and_two_bad_outputs_stop_at_two_calls():
    bundle = route_bundle()
    good = payload(candidate())
    captured = []
    repaired = run_p1_recommendation(bundle, sequence(["not json", good], captured), provider())
    assert repaired.status is RecommendationStatus.SELECTED_PRIMARY and repaired.call_count == 2
    assert repaired.second_call_reason is SecondCallReason.SCHEMA_REPAIR
    assert "not json" not in captured[1][1] and "严格解析" in captured[1][1]
    failed = run_p1_recommendation(bundle, sequence(["bad", "bad again"]), provider())
    assert failed.status is RecommendationStatus.CANDIDATE_PARSE_FAILED and failed.call_count == 2


def test_schema_repair_uses_second_chance_even_if_repaired_candidate_is_route_bad():
    bundle = route_bundle(free=35)
    result = run_p1_recommendation(bundle, sequence(["bad", payload(candidate())]), provider())
    assert result.status is RecommendationStatus.BOTH_CANDIDATES_INFEASIBLE
    assert result.call_count == 2 and result.second_call_reason is SecondCallReason.SCHEMA_REPAIR


def test_callable_exceptions_retry_once_without_sensitive_leakage():
    bundle = route_bundle()
    secret = RuntimeError("Authorization: Bearer secret https://example.invalid")
    recovered = run_p1_recommendation(bundle, sequence([secret, payload(candidate())]), provider())
    assert recovered.status is RecommendationStatus.SELECTED_PRIMARY and recovered.call_count == 2
    failed = run_p1_recommendation(bundle, sequence([secret, secret]), provider())
    shown = failed.safe_summary + " " + failed.first_candidate_result.message
    assert failed.status is RecommendationStatus.CALL_FAILED and "Bearer" not in shown and "example" not in shown


def test_information_shortage_and_route_data_shortage_do_not_regenerate():
    missing = route_bundle(current=None)
    result = run_p1_recommendation(missing, sequence([payload(candidate())]), provider())
    assert result.status is RecommendationStatus.NEEDS_INFORMATION and result.call_count == 1
    route_data = route_bundle(LocationRequirement.SPECIFIC_LOCATION, "图书馆")
    result = run_p1_recommendation(route_data, sequence([payload(candidate())]), FakeProvider(route_status=ProviderRouteStatus.DATA_INSUFFICIENT))
    assert result.status is RecommendationStatus.NEEDS_INFORMATION and result.call_count == 1


def test_no_executable_tasks_and_missing_information_do_not_call_again():
    bundle = route_bundle()
    no_tasks = run_p1_recommendation(bundle, sequence([missing_payload("no_executable_tasks")]), provider())
    needs = run_p1_recommendation(bundle, sequence([missing_payload("missing_information")]), provider())
    assert no_tasks.status is RecommendationStatus.NO_EXECUTABLE_TASKS and no_tasks.call_count == 1
    assert needs.status is RecommendationStatus.NEEDS_INFORMATION and needs.call_count == 1


def test_route_feedback_never_contains_raw_exception_or_model_json():
    bundle = route_bundle(free=35)
    captured = []
    raw = '{"fake":"Authorization Bearer https://bad"}'
    run_p1_recommendation(bundle, sequence([payload(candidate()), payload(candidate("other", minutes=30))], captured), provider())
    assert raw not in captured[1][1]
    assert "Authorization" not in captured[1][1] and "Bearer" not in captured[1][1]


@pytest.mark.parametrize("first,reason,status", [
    ("bad", "schema_repair", "no_executable_tasks"),
    ("bad", "schema_repair", "missing_information"),
    (None, "route_regeneration", "no_executable_tasks"),
    (None, "route_regeneration", "missing_information"),
])
def test_second_document_decision_status_is_preserved_without_third_call(first, reason, status):
    bundle = route_bundle(free=35 if reason == "route_regeneration" else None)
    initial = payload(candidate()) if first is None else first
    result = run_p1_recommendation(bundle, sequence([initial, missing_payload(status)]), provider())
    expected = RecommendationStatus.NO_EXECUTABLE_TASKS if status == "no_executable_tasks" else RecommendationStatus.NEEDS_INFORMATION
    assert result.status is expected and result.call_count == 2
    assert result.second_call_reason.value == reason
    assert result.supplement_question is None if expected is RecommendationStatus.NO_EXECUTABLE_TASKS else result.supplement_question is not None


def test_supplement_question_is_direct_for_missing_location_and_candidate_missing_info():
    location_missing = run_p1_recommendation(route_bundle(current=None), sequence([payload(candidate())]), provider())
    candidate_missing = run_p1_recommendation(route_bundle(), sequence([missing_payload("missing_information")]), provider())
    selected = run_p1_recommendation(route_bundle(), sequence([payload(candidate())]), provider())
    assert location_missing.supplement_question is not None
    assert candidate_missing.supplement_question is not None
    assert selected.supplement_question is None


def test_route_feedback_has_sanitized_steps_overrun_and_no_raw_candidate_text():
    bundle = route_bundle(free=35)
    sensitive = candidate()
    sensitive["rationale"] = "UNIQUE_RAW_OUTPUT_MARKER Authorization Bearer https://unsafe.invalid"
    sensitive["assumptions"] = ["UNIQUE_RAW_OUTPUT_MARKER"]
    sensitive["warnings"] = ["UNIQUE_RAW_OUTPUT_MARKER"]
    raw = payload(sensitive)
    captured = []
    run_p1_recommendation(bundle, sequence([raw, payload(candidate("new", minutes=30))], captured), provider())
    feedback = captured[1][1]
    assert "UNIQUE_RAW_OUTPUT_MARKER" not in feedback and "Authorization" not in feedback
    assert "Bearer" not in feedback and "unsafe.invalid" not in feedback and raw not in feedback
    assert '"task_ref":"task-1"' in feedback and '"planned_minutes":30' in feedback
    assert '"status":"time_infeasible"' in feedback and '"overrun_minutes":8' in feedback
    assert '"route_data_trust":"synthetic_test"' in feedback and "不要重复原方案" in feedback


@pytest.mark.parametrize("first,second,expected", [
    ("bad", RuntimeError("Authorization Bearer https://one.invalid"), RecommendationStatus.CALL_FAILED),
    (RuntimeError("Authorization Bearer https://two.invalid"), "bad", RecommendationStatus.CANDIDATE_PARSE_FAILED),
])
def test_second_call_failure_is_classified_by_second_outcome(first, second, expected):
    result = run_p1_recommendation(route_bundle(), sequence([first, second]), provider())
    assert result.status is expected and result.call_count == 2


def test_route_regeneration_callable_exception_is_call_failed_without_third_call():
    result = run_p1_recommendation(route_bundle(free=35), sequence([payload(candidate()), RuntimeError("Bearer https://route.invalid")]), provider())
    assert result.status is RecommendationStatus.CALL_FAILED and result.call_count == 2
