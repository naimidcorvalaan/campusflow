import json
from dataclasses import replace
from datetime import timedelta
import pytest
from tests.test_p1_context_bundle import task_result, window_result
from src.p1_candidate_parser import parse_p1_candidate
from src.p1_context_bundle import build_p1_context_bundle
from src.p1_models import AttentionLevel, LocationRequirement
from src.p1_window_models import AvailabilityLevel, WindowConstraints


def bundle(): return build_p1_context_bundle(task_result(), window_result())
def payload(primary, alternative=None, status="proposed"):
    return json.dumps({"schema_version":"p1.candidate.v1","decision_status":status,"primary_candidate":primary,"alternative_candidate":alternative,"safe_summary":"暂定建议"}, ensure_ascii=False)
def candidate(ref="primary", task="task-1", minutes=30, context="free_window", commitment=None, warnings=None):
    return {"candidate_ref":ref,"steps":[{"task_ref":task,"planned_minutes":minutes,"execution_context":context,"commitment_ref":commitment}],"rationale":"优先完成当前任务","assumptions":["按当前信息暂定"],"warnings":[] if warnings is None else warnings}


def rich_bundle(location_requirement=LocationRequirement.NO_SPECIFIC_LOCATION, splittable=True, total=120, minimum=30, attention=AttentionLevel.MEDIUM, evidence=None, needs=(), availability=AvailabilityLevel.FULLY_AVAILABLE, end_known=True, has_boundary=True, unknown_start=False):
    parsed = task_result()
    task = parsed.document.tasks[0]
    features = replace(task.features, location_requirement=location_requirement, estimated_total_minutes=total, is_splittable=splittable, minimum_slice_minutes=minimum, attention_required=attention)
    task = replace(task, features=features, field_evidence={} if evidence is None else evidence, needs_confirmation=needs)
    parsed = replace(parsed, document=replace(parsed.document, tasks=(task,)))
    window = window_result()
    doc = window.document
    commitment = replace(doc.commitments[0], availability_during=availability, ends_at=(doc.commitments[0].starts_at + timedelta(minutes=60)) if end_known else None)
    commitments = (commitment,)
    if unknown_start:
        commitments = (replace(commitment, commitment_ref="unknown", starts_at=None, needs_confirmation=("starts_at",)), commitment)
    if not has_boundary:
        commitments = ()
    constraints = doc.constraints if has_boundary else WindowConstraints(None, None, 10, {}, ("free_duration_minutes",))
    window = replace(window, document=replace(doc, commitments=commitments, constraints=constraints))
    return build_p1_context_bundle(parsed, window)


def multi(refs, context="free_window", commitment=None):
    data = candidate(context=context, commitment=commitment)
    data["steps"] = [{"task_ref":"task-1","planned_minutes":minutes,"execution_context":context,"commitment_ref":commitment} for minutes in refs]
    return data


def test_location_unknown_task_is_tentative_and_uses_ai_estimates():
    result = parse_p1_candidate(payload(candidate()), bundle())
    assert result.status == "ok"
    item = result.document.primary_candidate
    assert item.uses_ai_estimates and item.requires_route_validation and item.is_tentative


def test_primary_and_optional_alternative_and_same_rejected():
    result = parse_p1_candidate(payload(candidate(), candidate("alt", minutes=60)), bundle())
    assert result.status == "ok" and result.document.alternative_candidate is not None
    assert parse_p1_candidate(payload(candidate(), candidate("alt")), bundle()).status == "rejected"


def test_no_tasks_cannot_propose_and_missing_info_can_be_empty():
    parsed = task_result()
    empty_task = replace(parsed, document=replace(parsed.document, tasks=()))
    empty = build_p1_context_bundle(empty_task, window_result())
    assert parse_p1_candidate(payload(candidate()), empty).status == "rejected"
    result = parse_p1_candidate(payload(None, status="no_executable_tasks"), empty)
    assert result.status == "ok"


def test_minutes_unsplittable_and_unknown_outputs_rejected():
    assert parse_p1_candidate(payload(candidate(minutes=10)), bundle()).status == "rejected"
    bad = json.loads(payload(candidate())); bad["route"] = []
    assert parse_p1_candidate(json.dumps(bad), bundle()).status == "rejected"


def test_fixed_commitment_rules_and_high_attention_warning():
    b = bundle()
    candidate_data = candidate(context="commitment", commitment="class-1", minutes=30)
    assert parse_p1_candidate(payload(candidate_data), b).status == "rejected"


def test_duplicate_candidate_ref_and_bool_minutes_rejected():
    assert parse_p1_candidate(payload(candidate(), candidate("primary", minutes=60)), bundle()).status == "rejected"
    assert parse_p1_candidate(payload(candidate(minutes=True)), bundle()).status == "rejected"


def test_low_attention_high_attention_gets_automatic_warning_without_model_warning():
    b = rich_bundle(attention=AttentionLevel.HIGH, availability=AvailabilityLevel.LOW_ATTENTION)
    result = parse_p1_candidate(payload(candidate(context="commitment", commitment="class-1")), b)
    assert result.status == "ok"
    assert result.document.primary_candidate.warnings.count("这项任务需要较高注意力，在低注意力时段执行可能影响效率。") == 1


def test_low_attention_existing_warning_not_duplicated_and_fully_available_passes():
    b = rich_bundle(attention=AttentionLevel.HIGH, availability=AvailabilityLevel.LOW_ATTENTION)
    warning = "这项任务需要较高注意力，在低注意力时段执行可能影响效率。"
    assert parse_p1_candidate(payload(candidate(context="commitment", commitment="class-1", warnings=[warning])), b).document.primary_candidate.warnings.count(warning) == 1
    assert parse_p1_candidate(payload(candidate(context="commitment", commitment="class-1")), rich_bundle()).status == "ok"


@pytest.mark.parametrize("availability,end_known", [(AvailabilityLevel.UNAVAILABLE, True), (AvailabilityLevel.FULLY_AVAILABLE, False)])
def test_unavailable_or_unknown_commitment_duration_rejected(availability, end_known):
    assert parse_p1_candidate(payload(candidate(context="commitment", commitment="class-1")), rich_bundle(availability=availability, end_known=end_known)).status == "rejected"


def test_commitment_location_task_and_cumulative_duration_rules():
    assert parse_p1_candidate(payload(candidate(context="commitment", commitment="class-1")), rich_bundle(location_requirement=LocationRequirement.SPECIFIC_LOCATION)).status == "rejected"
    assert parse_p1_candidate(payload(multi([40, 40], context="commitment", commitment="class-1")), rich_bundle()).status == "rejected"
    assert parse_p1_candidate(payload(multi([30, 30], context="commitment", commitment="class-1")), rich_bundle()).status == "ok"


def test_task_splitting_cumulative_and_unknown_task_rules():
    assert parse_p1_candidate(payload(candidate(minutes=30)), rich_bundle(splittable=False, total=60)).status == "rejected"
    assert parse_p1_candidate(payload(multi([60, 60])), rich_bundle(splittable=False, total=60)).status == "rejected"
    assert parse_p1_candidate(payload(candidate(minutes=20)), rich_bundle(minimum=30)).status == "rejected"
    assert parse_p1_candidate(payload(multi([60, 70])), rich_bundle(total=120)).status == "rejected"
    assert parse_p1_candidate(payload(multi([60, 60])), rich_bundle(total=120)).status == "rejected"
    assert parse_p1_candidate(payload(candidate(task="missing")), rich_bundle()).status == "rejected"


def test_free_window_rules_and_tentative_causes():
    assert parse_p1_candidate(payload(multi([40, 40])), rich_bundle(total=200)).status == "rejected"
    assert parse_p1_candidate(payload(multi([30, 45])), rich_bundle(total=200)).status == "ok"
    assert parse_p1_candidate(payload(candidate(commitment="class-1")), rich_bundle()).status == "rejected"
    assert parse_p1_candidate(payload(candidate(context="commitment", commitment=None)), rich_bundle()).status == "rejected"
    assert parse_p1_candidate(payload(candidate(context="commitment", commitment="missing")), rich_bundle()).status == "rejected"
    assert parse_p1_candidate(payload(candidate()), rich_bundle(needs=("location_requirement",))).document.primary_candidate.is_tentative
    assert parse_p1_candidate(payload(candidate()), rich_bundle(has_boundary=False)).document.primary_candidate.is_tentative
    assert parse_p1_candidate(payload(candidate()), rich_bundle(unknown_start=True)).document.primary_candidate.is_tentative


def test_sufficient_non_location_non_ai_candidate_is_not_tentative():
    result = parse_p1_candidate(payload(candidate()), rich_bundle())
    assert result.status == "ok"
    assert not result.document.primary_candidate.is_tentative


@pytest.mark.parametrize("raw", ["```json\n{}\n```", "说明{}", "{}尾文", '{"schema_version":"p1.candidate.v1","schema_version":"x"}'])
def test_strict_json_fences_text_and_duplicate_key_rejected(raw):
    assert parse_p1_candidate(raw, bundle()).status == "rejected"


@pytest.mark.parametrize("mutator", [
    lambda p: p.update({"unknown": True}),
    lambda p: p["primary_candidate"].update({"unknown": True}),
    lambda p: p["primary_candidate"]["steps"][0].update({"unknown": True}),
    lambda p: p["primary_candidate"]["steps"][0].update({"execution_context": "route"}),
    lambda p: p["primary_candidate"].update({"eta": "11:20"}),
    lambda p: p["primary_candidate"].update({"location_id": "node"}),
    lambda p: p["primary_candidate"].update({"lifecycle": "done"}),
])
def test_unknown_and_route_state_fields_rejected(mutator):
    data = json.loads(payload(candidate()))
    mutator(data)
    assert parse_p1_candidate(json.dumps(data), bundle()).status == "rejected"


@pytest.mark.parametrize("status", ["missing_information", "no_executable_tasks"])
def test_non_proposed_status_cannot_carry_candidates(status):
    assert parse_p1_candidate(payload(candidate(), status=status), bundle()).status == "rejected"


def test_proposed_without_primary_rejected():
    assert parse_p1_candidate(payload(None), bundle()).status == "rejected"
