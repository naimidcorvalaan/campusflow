from dataclasses import replace
from datetime import datetime, timedelta

from tests.test_p1_candidate_parser import candidate, multi, payload, rich_bundle
from src.p1_candidate_parser import parse_p1_candidate
from src.p1_models import LocationRequirement
from src.p1_route_models import CandidateValidationStatus, RouteDataTrust
from src.p1_route_models import (LocationResolution, LocationResolutionStatus,
                                 ProviderRoute, ProviderRouteStatus)
from src.p1_route_provider import CampusMapRouteProvider
from src.p1_route_validator import select_validated_candidate, validate_candidate
from src.campus_map import load_campus_map


def provider():
    return CampusMapRouteProvider(load_campus_map("tests/fixtures/sample_campus_map.json"), RouteDataTrust.SYNTHETIC_TEST)


def route_bundle(task_location=LocationRequirement.NO_SPECIFIC_LOCATION, task_text=None,
                 current="宿舍", next_location="教学楼", start=None, free=None):
    bundle = rich_bundle(location_requirement=task_location, total=120, minimum=30)
    task = bundle.tasks[0]
    task = replace(task, features=replace(task.features, location_requirement=task_location, location_text=task_text))
    parsed = replace(bundle.task_result, document=replace(bundle.task_result.document, tasks=(task,)))
    doc = bundle.window_document
    commitment = replace(doc.commitments[0], starts_at=start or doc.commitments[0].starts_at, location_text=next_location)
    constraints = doc.constraints if free is None else replace(doc.constraints, free_duration_minutes=free)
    window = replace(bundle.window_result, document=replace(doc, current_context=replace(doc.current_context, current_location_text=current), commitments=(commitment,), constraints=constraints))
    from src.p1_context_bundle import build_p1_context_bundle
    return build_p1_context_bundle(parsed, window)


def parsed_candidate(bundle, data=None):
    result = parse_p1_candidate(payload(data or candidate()), bundle)
    assert result.status == "ok"
    return result.document


def test_no_specific_location_has_no_walk_and_test_data_trust():
    bundle = route_bundle()
    doc = bundle.window_document
    from src.p1_window_models import WindowConstraints
    from src.p1_context_bundle import build_p1_context_bundle
    window = replace(bundle.window_result, document=replace(doc, commitments=(), constraints=WindowConstraints(40, None, 10, {}, ())))
    bundle = build_p1_context_bundle(bundle.task_result, window)
    validation = validate_candidate(parsed_candidate(bundle).primary_candidate, bundle, provider())
    assert validation.status is CandidateValidationStatus.FEASIBLE
    assert validation.total_walking_minutes == 0 and not validation.segments
    assert validation.route_data_trust is RouteDataTrust.SYNTHETIC_TEST
    assert "测试数据" in validation.safe_reason


def test_single_and_multiple_location_tasks_form_continuous_chain_and_final_leg():
    bundle = route_bundle(LocationRequirement.SPECIFIC_LOCATION, "图书馆", start=datetime(2026, 5, 1, 12, 30))
    validation = validate_candidate(parsed_candidate(bundle).primary_candidate, bundle, provider())
    assert [(x.start_location, x.end_location) for x in validation.segments] == [("测试宿舍", "测试图书馆"), ("测试图书馆", "测试教学楼")]
    assert validation.total_walking_minutes == 18


def test_two_location_tasks_use_actual_a_to_b_to_commitment_chain():
    bundle = route_bundle(LocationRequirement.SPECIFIC_LOCATION, "图书馆", start=datetime(2026, 5, 1, 12, 30))
    first = bundle.tasks[0]
    second = replace(first, task_ref="task-2", title="取餐", original_text="取餐",
                     features=replace(first.features, location_text="食堂"))
    parsed = replace(bundle.task_result, document=replace(bundle.task_result.document, tasks=(first, second)))
    from src.p1_context_bundle import build_p1_context_bundle
    bundle = build_p1_context_bundle(parsed, bundle.window_result)
    data = candidate(minutes=30)
    data["steps"].append({"task_ref":"task-2", "planned_minutes":30, "execution_context":"free_window", "commitment_ref":None})
    validation = validate_candidate(parsed_candidate(bundle, data).primary_candidate, bundle, provider())
    assert [(x.start_location, x.end_location) for x in validation.segments] == [("测试宿舍", "测试图书馆"), ("测试图书馆", "测试食堂"), ("测试食堂", "测试教学楼")]
    assert validation.total_walking_minutes == 29
    assert validation.free_window_task_minutes == 60
    assert validation.expected_finish_at == bundle.current_context.current_datetime + timedelta(minutes=89)
    assert validation.status is CandidateValidationStatus.FEASIBLE


def test_arrival_deadline_not_commitment_start_and_equality_is_feasible():
    base = route_bundle()
    doc = base.window_document
    commitment = replace(doc.commitments[0], starts_at=doc.current_context.current_datetime + timedelta(minutes=40), location_text="宿舍")
    window = replace(base.window_result, document=replace(doc, commitments=(commitment,)))
    from src.p1_context_bundle import build_p1_context_bundle
    bundle = build_p1_context_bundle(base.task_result, window)
    validation = validate_candidate(parsed_candidate(bundle).primary_candidate, bundle, provider())
    assert bundle.window_document.arrival_deadline == doc.current_context.current_datetime + timedelta(minutes=30)
    assert validation.status is CandidateValidationStatus.FEASIBLE
    assert validation.total_walking_minutes == 0


def test_information_shortage_keeps_free_minutes_but_walking_total_unknown():
    bundle = route_bundle(current="31教学楼", next_location="北区菜鸟驿站")
    validation = validate_candidate(parsed_candidate(bundle).primary_candidate, bundle, provider())
    assert validation.status is CandidateValidationStatus.LOCATION_UNRESOLVED
    assert validation.free_window_task_minutes == 30
    assert validation.total_candidate_task_minutes == 30
    assert validation.total_walking_minutes is None


def test_time_limit_uses_stricter_constraint_and_reports_infeasible():
    bundle = route_bundle(free=35)
    validation = validate_candidate(parsed_candidate(bundle).primary_candidate, bundle, provider())
    assert validation.status is CandidateValidationStatus.TIME_INFEASIBLE


def test_missing_current_task_or_commitment_location_is_information_not_infeasible():
    for kwargs in ({"current": None}, {"task_location": LocationRequirement.SPECIFIC_LOCATION, "task_text": None}, {"next_location": None}):
        bundle = route_bundle(**kwargs)
        validation = validate_candidate(parsed_candidate(bundle).primary_candidate, bundle, provider())
        assert validation.status is CandidateValidationStatus.LOCATION_UNRESOLVED


def test_missing_boundary_and_unreachable_route_are_distinct():
    bundle = route_bundle()
    doc = bundle.window_document
    from src.p1_window_models import WindowConstraints
    from src.p1_context_bundle import build_p1_context_bundle
    no_boundary = build_p1_context_bundle(bundle.task_result, replace(bundle.window_result, document=replace(doc, commitments=(), constraints=WindowConstraints(None, None, 10, {}, ()))))
    assert validate_candidate(parsed_candidate(no_boundary).primary_candidate, no_boundary, provider()).status is CandidateValidationStatus.INSUFFICIENT_TIME_BOUNDARY
    unreachable = route_bundle(LocationRequirement.SPECIFIC_LOCATION, "不存在地点")
    assert validate_candidate(parsed_candidate(unreachable).primary_candidate, unreachable, provider()).status is CandidateValidationStatus.LOCATION_UNRESOLVED


def test_unknown_commitment_start_is_information_insufficient_not_time_failure():
    bundle = route_bundle()
    doc = bundle.window_document
    unknown = replace(doc.commitments[0], commitment_ref="unknown", starts_at=None, needs_confirmation=("starts_at",))
    from src.p1_context_bundle import build_p1_context_bundle
    window = replace(bundle.window_result, document=replace(doc, commitments=(unknown, doc.commitments[0])))
    uncertain = build_p1_context_bundle(bundle.task_result, window)
    validation = validate_candidate(parsed_candidate(uncertain).primary_candidate, uncertain, provider())
    assert validation.status is CandidateValidationStatus.INSUFFICIENT_TIME_BOUNDARY


def test_selection_keeps_primary_or_promotes_feasible_alternative():
    good = route_bundle()
    first = parsed_candidate(good)
    selected = select_validated_candidate(first, good, provider())
    assert selected.selected_candidate.candidate_ref == "primary" and not selected.needs_regeneration
    bad = route_bundle(free=70)
    alternative = candidate("alternative", minutes=30)
    document = parsed_candidate(bad, multi([30, 30]))
    document = replace(document, alternative_candidate=parsed_candidate(bad, alternative).primary_candidate)
    selected = select_validated_candidate(document, bad, provider())
    assert selected.selected_candidate.candidate_ref == "alternative"


def test_both_time_infeasible_needs_regeneration_but_information_does_not():
    bad = route_bundle(free=35)
    document = parsed_candidate(bad)
    result = select_validated_candidate(document, bad, provider())
    assert result.needs_regeneration
    missing = route_bundle(current=None)
    result = select_validated_candidate(parsed_candidate(missing), missing, provider())
    assert not result.needs_regeneration and result.supplement_question is not None


class FakeProvider(object):
    route_data_trust = RouteDataTrust.ESTIMATED_UNVERIFIED

    def __init__(self, resolution_status=LocationResolutionStatus.RESOLVED,
                 route_status=ProviderRouteStatus.COMPUTED, explode=None):
        self.resolution_status = resolution_status
        self.route_status = route_status
        self.explode = explode

    def resolve_location(self, text):
        if self.explode == "resolve":
            raise RuntimeError("Bearer secret https://private.example")
        return LocationResolution(self.resolution_status, text or "x", text or "x")

    def calculate_route(self, start, end):
        if self.explode == "route":
            raise RuntimeError("Authorization password https://private.example")
        return ProviderRoute(self.route_status, 5 if self.route_status is ProviderRouteStatus.COMPUTED else None, (start, end))


def test_provider_data_insufficient_is_not_unreachable_and_does_not_regenerate():
    bundle = route_bundle(LocationRequirement.SPECIFIC_LOCATION, "图书馆")
    document = parsed_candidate(bundle)
    validation = validate_candidate(document.primary_candidate, bundle, FakeProvider(resolution_status=LocationResolutionStatus.DATA_INSUFFICIENT))
    selected = select_validated_candidate(document, bundle, FakeProvider(route_status=ProviderRouteStatus.DATA_INSUFFICIENT))
    assert validation.status is CandidateValidationStatus.ROUTE_DATA_INSUFFICIENT
    assert not selected.needs_regeneration and selected.supplement_question is not None


def test_location_data_insufficient_is_a_safe_non_regeneration_result():
    bundle = route_bundle(LocationRequirement.SPECIFIC_LOCATION, "图书馆")
    document = parsed_candidate(bundle)
    result = select_validated_candidate(document, bundle, FakeProvider(resolution_status=LocationResolutionStatus.DATA_INSUFFICIENT))
    assert result.primary_validation.status is CandidateValidationStatus.ROUTE_DATA_INSUFFICIENT
    assert not result.needs_regeneration


def test_route_data_insufficient_is_not_reported_as_unreachable():
    bundle = route_bundle(LocationRequirement.SPECIFIC_LOCATION, "图书馆")
    validation = validate_candidate(parsed_candidate(bundle).primary_candidate, bundle, FakeProvider(route_status=ProviderRouteStatus.DATA_INSUFFICIENT))
    assert validation.status is CandidateValidationStatus.ROUTE_DATA_INSUFFICIENT
    assert "不可达" not in validation.safe_reason


def test_provider_explicit_unreachable_is_distinct():
    bundle = route_bundle(LocationRequirement.SPECIFIC_LOCATION, "图书馆")
    validation = validate_candidate(parsed_candidate(bundle).primary_candidate, bundle, FakeProvider(route_status=ProviderRouteStatus.UNREACHABLE))
    assert validation.status is CandidateValidationStatus.ROUTE_UNREACHABLE


def test_provider_exceptions_are_safe_data_insufficient_without_leakage():
    bundle = route_bundle(LocationRequirement.SPECIFIC_LOCATION, "图书馆")
    document = parsed_candidate(bundle)
    for exploding in ("resolve", "route"):
        validation = validate_candidate(document.primary_candidate, bundle, FakeProvider(explode=exploding))
        selected = select_validated_candidate(document, bundle, FakeProvider(explode=exploding))
        text = validation.safe_reason + " " + selected.safe_summary
        assert validation.status is CandidateValidationStatus.ROUTE_DATA_INSUFFICIENT
        assert not selected.needs_regeneration
        assert "Bearer" not in text and "Authorization" not in text and "private" not in text


def test_no_candidate_decision_statuses_are_not_validated_or_regenerated():
    bundle = route_bundle()
    base = parsed_candidate(bundle)
    from src.p1_candidate_models import CandidateDecisionStatus
    empty = replace(base, decision_status=CandidateDecisionStatus.NO_EXECUTABLE_TASKS, primary_candidate=None)
    missing = replace(base, decision_status=CandidateDecisionStatus.MISSING_INFORMATION, primary_candidate=None)
    assert "没有可执行任务" in select_validated_candidate(empty, bundle, provider()).safe_summary
    result = select_validated_candidate(missing, bundle, provider())
    assert not result.needs_regeneration and result.supplement_question is not None


def test_only_primary_time_failure_needs_regeneration():
    bundle = route_bundle(free=35)
    result = select_validated_candidate(parsed_candidate(bundle), bundle, provider())
    assert result.needs_regeneration


def test_primary_time_failure_and_alternative_data_shortage_does_not_regenerate():
    bundle = route_bundle(LocationRequirement.SPECIFIC_LOCATION, "图书馆", free=70)
    document = parsed_candidate(bundle, multi([30, 30]))
    document = replace(document, alternative_candidate=parsed_candidate(bundle, candidate("alt", minutes=30)).primary_candidate)
    result = select_validated_candidate(document, bundle, FakeProvider(route_status=ProviderRouteStatus.DATA_INSUFFICIENT))
    assert not result.needs_regeneration and result.supplement_question is not None


def test_commitment_steps_are_counted_but_do_not_advance_free_window_clock():
    bundle = rich_bundle(total=120, minimum=30)
    doc = bundle.window_document
    commitment = replace(doc.commitments[0], ends_at=doc.commitments[0].starts_at + timedelta(minutes=60), location_text="教学楼")
    from src.p1_context_bundle import build_p1_context_bundle
    window = replace(bundle.window_result, document=replace(doc, current_context=replace(doc.current_context, current_location_text="宿舍"), commitments=(commitment,)))
    bundle = build_p1_context_bundle(bundle.task_result, window)
    data = multi([30], context="free_window")
    data["steps"].append({"task_ref":"task-1", "planned_minutes":30, "execution_context":"commitment", "commitment_ref":"class-1"})
    validation = validate_candidate(parsed_candidate(bundle, data).primary_candidate, bundle, provider())
    assert validation.total_candidate_task_minutes == 60
    assert validation.free_window_task_minutes == 30
    assert validation.expected_finish_at == bundle.current_context.current_datetime + timedelta(minutes=43)
