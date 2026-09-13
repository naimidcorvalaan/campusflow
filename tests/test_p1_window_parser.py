import copy
import json
from datetime import datetime

import pytest

from src.p1_window_models import AvailabilityLevel, DEFAULT_SAFETY_BUFFER_MINUTES
from src.p1_window_parser import parse_window_context


REFERENCE = datetime(2026, 5, 1, 10, 5)
USER = "我现在在31教学楼，11:30要去46教学楼上课。"


def evidence(value=True):
    return {"source": "ai_extracted_from_user_text", "explanation": "用户原话明确提供该信息"} if value else None


def current(location="31教学楼", assumed=None, needs=None):
    return {"current_location_text": location, "current_location_fragment": location, "assumed_current_datetime": assumed, "assumed_current_datetime_fragment": None, "field_evidence": ({"current_location_text": evidence()} if location else {}), "needs_confirmation": [] if needs is None else needs}


def commitment(ref="class-1", start="2026-05-01T11:30:00", end=None, location="46教学楼", availability=None, needs=None):
    values = {"starts_at": start, "ends_at": end, "location_text": location, "availability_during": availability}
    field_evidence = {key: evidence() for key, value in values.items() if value is not None}
    return {"commitment_ref": ref, "title": "上课", "original_text": "11:30要去46教学楼上课", "starts_at": start, "ends_at": end, "location_text": location, "availability_during": availability, "field_evidence": field_evidence, "needs_confirmation": [] if needs is None else needs}


def constraints(duration=None, ends=None, buffer=None, needs=None):
    values = {"free_duration_minutes": duration, "ends_at": ends, "user_buffer_minutes": buffer}
    return {"free_duration_minutes": duration, "ends_at": ends, "user_buffer_minutes": buffer, "field_evidence": {key: evidence() for key, value in values.items() if value is not None}, "needs_confirmation": [] if needs is None else needs}


def payload(current_value=None, commitments=None, constraints_value=None, questions=None):
    return {"schema_version": "p1.window-context.v1", "current_context": current() if current_value is None else current_value, "commitments": [commitment()] if commitments is None else commitments, "window_constraints": constraints() if constraints_value is None else constraints_value, "clarification_questions": [] if questions is None else questions}


def parse(value, user=USER):
    return parse_window_context(json.dumps(value, ensure_ascii=False), user, REFERENCE)


def rejected(value, kind=None, user=USER):
    result = parse(value, user)
    assert result.status == "rejected"
    if kind: assert result.error_type == kind


def test_default_program_time_location_next_commitment_and_defaults():
    result = parse(payload())
    assert result.status == "ok"
    assert result.document.current_context.current_datetime == REFERENCE
    item = result.document.earliest_known_commitment
    assert item.location_text == "46教学楼"
    assert item.availability_during is AvailabilityLevel.UNAVAILABLE
    assert item.field_evidence["availability_during"].source.value == "system_default"
    assert result.document.nominal_window_end == datetime(2026, 5, 1, 11, 30)
    assert result.document.arrival_deadline == datetime(2026, 5, 1, 11, 20)
    assert result.document.constraints.field_evidence["safety_buffer_minutes"].source.value == "system_default"
    assert result.document.constraints.safety_buffer_minutes == DEFAULT_SAFETY_BUFFER_MINUTES


def test_user_assumed_time_duration_and_ends_at_boundaries():
    user = "假设现在是10:05，我刚从31楼下课。我接下来空闲两个小时，下午一点前都有空。"
    c = current("31楼", "2026-05-01T10:05:00")
    c["assumed_current_datetime_fragment"] = "假设现在是10:05"
    c["field_evidence"]["assumed_current_datetime"] = evidence()
    result = parse(payload(c, [], constraints(120, "2026-05-01T13:00:00")), user)
    assert result.status == "ok"
    assert result.document.current_context.current_datetime == datetime(2026, 5, 1, 10, 5)
    assert result.document.nominal_window_end == datetime(2026, 5, 1, 12, 5)


def test_multiple_and_next_day_commitments_are_preserved_and_sorted_by_full_datetime():
    user = "我现在在31教学楼，11:30要去46教学楼上课。下午1:30去45楼，明天9点去图书馆。"
    first = commitment("today-late", "2026-05-01T13:30:00", location="45楼")
    second = commitment("tomorrow", "2026-05-02T09:00:00", location="图书馆", )
    third = commitment("today-early", "2026-05-01T11:30:00", location="46楼")
    result = parse(payload(commitments=[first, second, third]), user)
    assert result.status == "ok"
    assert len(result.document.commitments) == 3
    assert result.document.earliest_known_commitment.commitment_ref == "today-early"
    assert result.document.next_commitment_is_confirmed


@pytest.mark.parametrize("availability", ["unavailable", "low_attention", "fully_available"])
def test_all_availability_levels_are_accepted(availability):
    user = "我现在在31教学楼，11:30要去46教学楼上课，期间可以做原地任务。"
    item = commitment(availability=availability, needs=["ends_at"] if availability != "unavailable" else [])
    result = parse(payload(commitments=[item]), user)
    assert result.status == "ok"
    assert result.document.commitments[0].availability_during.value == availability


def test_user_buffer_and_missing_information_degrade_safely():
    user = "我接下来空闲两个小时，缓冲15分钟。"
    c = current(None, needs=["current_location_text"])
    con = constraints(120, buffer=15)
    result = parse(payload(c, [], con), user)
    assert result.status == "ok"
    assert result.document.nominal_window_end == datetime(2026, 5, 1, 12, 5)
    assert result.document.current_context.current_location_text is None
    assert result.document.constraints.safety_buffer_minutes == 15
    assert result.document.constraints.field_evidence["safety_buffer_minutes"].source.value == "ai_extracted_from_user_text"


def test_custom_buffer_changes_arrival_deadline_but_not_nominal_boundary():
    result = parse(payload(constraints_value=constraints(buffer=15)))
    assert result.status == "ok"
    assert result.document.nominal_window_end == datetime(2026, 5, 1, 11, 30)
    assert result.document.arrival_deadline == datetime(2026, 5, 1, 11, 15)


def test_no_boundary_is_structured_incomplete_context():
    c = current(None, needs=["current_location_text"])
    result = parse(payload(c, [], constraints(needs=["free_duration_minutes", "ends_at"])), "我刚下课")
    assert result.status == "ok"
    assert not result.document.has_time_boundary


def test_unknown_commitment_start_is_not_silently_confirmed_as_later():
    unknown = commitment("unknown", start=None, needs=["starts_at", "ends_at"])
    unknown["field_evidence"].pop("starts_at", None)
    result = parse(payload(commitments=[unknown, commitment("known")]))
    assert result.status == "ok"
    assert result.document.earliest_known_commitment.commitment_ref == "known"
    assert result.document.has_unresolved_commitment_start
    assert not result.document.next_commitment_is_confirmed
    assert result.document.arrival_deadline is None


def test_missing_commitment_time_location_and_course_end_are_retained_for_followup():
    user = "我现在在31教学楼，11:30要去46教学楼上课，期间可以做原地任务。"
    item = commitment(start=None, end=None, location=None, availability="low_attention", needs=["starts_at", "location_text", "ends_at"])
    item["field_evidence"] = {"availability_during": evidence()}
    result = parse(payload(commitments=[item]), user)
    assert result.status == "ok"
    assert result.document.earliest_known_commitment is None
    assert "ends_at" in result.document.commitments[0].needs_confirmation


@pytest.mark.parametrize("availability", ["low_attention", "fully_available"])
def test_permissive_course_without_end_requires_confirmation(availability):
    item = commitment(availability=availability, needs=[])
    rejected(payload(commitments=[item]), "validation_error")


def test_unavailable_course_without_end_does_not_require_confirmation():
    result = parse(payload(commitments=[commitment(availability="unavailable", needs=[])]))
    assert result.status == "ok"


def test_no_boundary_without_followup_is_rejected():
    c = current(None, needs=["current_location_text"])
    rejected(payload(c, [], constraints(needs=[])), "validation_error", "我刚下课")


def test_questions_use_stable_target_refs():
    question = {"question_id": "q1", "target_ref": "class-1", "field_name": "location_text", "question": "上课地点在哪里？", "blocking": True, "quick_options": ["补充地点"]}
    item = commitment(location=None, needs=["location_text"])
    item["field_evidence"].pop("location_text", None)
    result = parse(payload(commitments=[item], questions=[question]), "我现在在31教学楼，11:30要去46教学楼上课")
    assert result.status == "ok"
    assert result.questions[0].target_ref == "class-1"


@pytest.mark.parametrize("raw", ["```json\n{}\n```", "说明{}", "{} 尾文", "{bad", "[]"])
def test_non_pure_json_rejected(raw):
    result = parse_window_context(raw, USER, REFERENCE)
    assert result.status == "rejected" and result.error_type == "format_error"


@pytest.mark.parametrize("mutator", [
    lambda p: p.update({"route": []}),
    lambda p: p["commitments"][0].update({"eta": "11:20"}),
    lambda p: p["commitments"][0].update({"location_id": "node"}),
    lambda p: p.update({"primary_candidate": {}}),
    lambda p: p.update({"completed_minutes": 1}),
    lambda p: p["commitments"][0]["field_evidence"]["starts_at"].update({"source": "ai_estimated"}),
])
def test_unknown_or_forbidden_output_rejected(mutator):
    value = payload(); mutator(value); rejected(value)


@pytest.mark.parametrize("mutator", [
    lambda p: p.update({"schema_version": "wrong"}),
    lambda p: p["commitments"][0].update({"starts_at": "bad"}),
    lambda p: p["commitments"][0].update({"ends_at": "2026-05-01T10:00:00"}),
    lambda p: p["commitments"][0].update({"commitment_ref": "bad ref"}),
    lambda p: p["window_constraints"].update({"free_duration_minutes": True}),
    lambda p: p["window_constraints"].update({"ends_at": "2026-05-01T09:00:00"}),
])
def test_invalid_time_refs_and_minutes_rejected(mutator):
    value = payload(); mutator(value); rejected(value, "validation_error")


def test_duplicate_keys_refs_fragments_and_questions_rejected():
    raw = '{"schema_version":"p1.window-context.v1","schema_version":"x","current_context":{},"commitments":[],"window_constraints":{},"clarification_questions":[]}'
    assert parse_window_context(raw, USER, REFERENCE).status == "rejected"
    item = commitment(); rejected(payload(commitments=[item, copy.deepcopy(item)]), "validation_error")
    item = commitment(); item["original_text"] = "模型虚构"; rejected(payload(commitments=[item]), "validation_error")
    question = {"question_id": "q1", "target_ref": "missing", "field_name": "starts_at", "question": "何时？", "blocking": False, "quick_options": []}
    rejected(payload(questions=[question]), "validation_error")
