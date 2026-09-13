import json
from datetime import datetime, timedelta

import pytest

from src.p1_extraction_normalizer import (
    ExtractionNormalizationError,
    normalize_task_extraction,
    normalize_window_extraction,
)
from src.p1_models import SourceKind
from src.p1_parser import parse_task_understanding
from src.p1_window_parser import parse_window_context


NOW = datetime(2026, 8, 16, 22, 36)


def compact_task(title="背单词", fragment="背30分钟单词", minutes=30,
                 estimated_minutes=False):
    extracted = [] if estimated_minutes else ["estimated_total_minutes"]
    estimated = [
        "location_requirement", "is_splittable", "minimum_slice_minutes",
        "attention_required", "interruption_allowed", "may_have_open_hours",
    ]
    if estimated_minutes:
        estimated.append("estimated_total_minutes")
    return {
        "title": title,
        "original_text": fragment,
        "features": {
            "location_requirement": "no_specific_location",
            "estimated_total_minutes": minutes,
            "is_splittable": True,
            "minimum_slice_minutes": 15,
            "attention_required": "low",
            "interruption_allowed": True,
            "may_have_open_hours": False,
        },
        "extracted_fields": extracted,
        "estimated_fields": estimated,
        "confirmation_fields": [],
    }


def task_reply(tasks):
    return json.dumps({"schema_version": "p1.task-extraction.v2", "tasks": tasks}, ensure_ascii=False)


def window_reply(user_text, starts_at, location="宿舍", destination="教学楼"):
    return json.dumps({
        "schema_version": "p1.window-extraction.v2",
        "current_location": None if location is None else {"text": location, "fragment": location},
        "commitments": [{
            "title": "上课",
            "original_text": user_text[user_text.index("90分钟后"):],
            "starts_at": starts_at.isoformat(),
            "ends_at": None,
            "location_text": destination,
            "availability_during": None,
        }],
        "free_duration_minutes": None,
        "ends_at": None,
        "user_buffer_minutes": None,
    }, ensure_ascii=False)


def test_compact_task_v2_normalizes_to_canonical_v1_with_stable_references():
    user = "这段时间想背30分钟单词，再整理20分钟实验报告"
    second = compact_task("整理实验报告", "整理20分钟实验报告", 20)
    canonical = normalize_task_extraction(task_reply([compact_task(), second]), user)
    parsed = parse_task_understanding(canonical, user)
    assert parsed.status == "ok"
    assert [task.task_ref for task in parsed.tasks] == ["input-task-1", "input-task-2"]
    assert [task.title for task in parsed.tasks] == ["背单词", "整理实验报告"]


def test_task_sources_explanations_defaults_and_auto_confirmation_are_deterministic():
    user = "完成计组实验3，我没说需要多久，请你先估计"
    task = compact_task("完成计组实验3", "完成计组实验3", 90, estimated_minutes=True)
    canonical = normalize_task_extraction(task_reply([task]), user)
    parsed = parse_task_understanding(canonical, user)
    item = parsed.tasks[0]
    assert item.features.environment_requirements == ()
    assert item.features.equipment_requirements == ()
    assert item.features.location_text is None
    assert item.field_evidence["estimated_total_minutes"].source is SourceKind.AI_ESTIMATED
    assert "暂估" in item.field_evidence["estimated_total_minutes"].explanation
    assert item.field_evidence["location_requirement"].source is SourceKind.AI_ESTIMATED
    assert "estimated_total_minutes" in item.needs_confirmation


def test_extracted_task_field_becomes_ai_extracted_from_user_text():
    user = "背30分钟单词"
    parsed = parse_task_understanding(
        normalize_task_extraction(task_reply([compact_task()]), user), user)
    evidence = parsed.tasks[0].field_evidence["estimated_total_minutes"]
    assert evidence.source is SourceKind.AI_EXTRACTED_FROM_USER_TEXT
    assert "用户原话" in evidence.explanation


def test_existing_task_v1_output_is_returned_unchanged():
    raw = '{"schema_version":"p1.task-understanding.v1","task_interpretations":[],"clarification_questions":[]}'
    assert normalize_task_extraction(raw, "只有固定安排") == raw


@pytest.mark.parametrize("mutator", [
    lambda payload: payload.update({"unknown": True}),
    lambda payload: payload["tasks"][0].update({"unknown": True}),
    lambda payload: payload["tasks"][0]["features"].update({"unknown": True}),
])
def test_compact_task_unknown_fields_are_rejected(mutator):
    payload = json.loads(task_reply([compact_task()]))
    mutator(payload)
    with pytest.raises(ExtractionNormalizationError):
        normalize_task_extraction(json.dumps(payload), "背30分钟单词")


def test_task_source_arrays_may_be_omitted_and_sources_are_conservatively_inferred():
    payload = json.loads(task_reply([compact_task()]))
    payload["tasks"][0].pop("extracted_fields")
    payload["tasks"][0].pop("estimated_fields")
    payload["tasks"][0].pop("confirmation_fields")
    parsed = parse_task_understanding(
        normalize_task_extraction(json.dumps(payload), "背30分钟单词"),
        "背30分钟单词")
    task = parsed.tasks[0]
    assert task.field_evidence["estimated_total_minutes"].source is SourceKind.AI_EXTRACTED_FROM_USER_TEXT
    assert task.field_evidence["is_splittable"].source is SourceKind.AI_ESTIMATED
    assert task.field_evidence["may_have_open_hours"].source is SourceKind.AI_ESTIMATED


def test_explicit_task_source_arrays_still_reject_conflicts():
    payload = json.loads(task_reply([compact_task()]))
    payload["tasks"][0]["estimated_fields"].append("estimated_total_minutes")
    with pytest.raises(ExtractionNormalizationError) as exc:
        normalize_task_extraction(json.dumps(payload), "背30分钟单词")
    assert exc.value.error_type == "source_contract_error"


def test_no_specific_location_ai_estimate_does_not_create_location_confirmation():
    payload = json.loads(task_reply([compact_task()]))
    payload["tasks"][0].pop("extracted_fields")
    payload["tasks"][0].pop("estimated_fields")
    parsed = parse_task_understanding(
        normalize_task_extraction(json.dumps(payload), "背30分钟单词"),
        "背30分钟单词")
    task = parsed.tasks[0]
    assert task.field_evidence["location_requirement"].source is SourceKind.AI_ESTIMATED
    assert "location_requirement" not in task.needs_confirmation
    assert all(question.field_name != "location_requirement" for question in parsed.questions)


@pytest.mark.parametrize("requirement,expected_field", [
    ("location_requirement_unknown", "location_requirement"),
    ("specific_location", "location_text"),
])
def test_unknown_or_specific_without_location_still_requires_confirmation(
        requirement, expected_field):
    payload = json.loads(task_reply([compact_task()]))
    payload["tasks"][0]["features"]["location_requirement"] = requirement
    payload["tasks"][0]["estimated_fields"] = [
        value for value in payload["tasks"][0]["estimated_fields"]
        if value != "location_requirement"]
    canonical = normalize_task_extraction(json.dumps(payload), "背30分钟单词")
    task = parse_task_understanding(canonical, "背30分钟单词").tasks[0]
    assert expected_field in task.needs_confirmation


def test_task_original_fragment_must_be_continuous_user_text():
    with pytest.raises(ExtractionNormalizationError) as exc:
        normalize_task_extraction(task_reply([compact_task(fragment="模型伪造片段")]), "背30分钟单词")
    assert exc.value.error_type == "original_fragment_mismatch"


def test_compact_window_v2_normalizes_to_v1_with_evidence_questions_and_stable_ref():
    user = "我现在在宿舍，90分钟后要去教学楼上课"
    starts = NOW + timedelta(minutes=90)
    canonical = normalize_window_extraction(window_reply(user, starts), user, NOW)
    parsed = parse_window_context(canonical, user, NOW)
    assert parsed.status == "ok"
    document = parsed.document
    assert document.commitments[0].commitment_ref == "input-commitment-1"
    assert document.commitments[0].starts_at == starts
    assert document.current_context.field_evidence["current_location_text"].source is SourceKind.AI_EXTRACTED_FROM_USER_TEXT
    assert document.has_time_boundary
    assert not any(q.field_name in ("free_duration_minutes", "time_boundary") for q in document.clarification_questions)


def test_compact_window_missing_values_get_program_confirmation_questions():
    payload = {
        "schema_version": "p1.window-extraction.v2",
        "current_location": None,
        "commitments": [],
        "free_duration_minutes": None,
        "ends_at": None,
        "user_buffer_minutes": None,
    }
    user = "我想安排一下"
    parsed = parse_window_context(
        normalize_window_extraction(json.dumps(payload), user, NOW), user, NOW)
    assert parsed.status == "ok"
    fields = {question.field_name for question in parsed.questions}
    assert fields == {"current_location_text", "free_duration_minutes"}


def test_relative_iso_time_after_reference_and_cross_midnight_are_preserved():
    user = "我在宿舍，90分钟后去教学楼上课"
    starts = NOW + timedelta(minutes=90)
    parsed = parse_window_context(
        normalize_window_extraction(window_reply(user, starts), user, NOW), user, NOW)
    assert starts.date() > NOW.date()
    assert parsed.document.commitments[0].starts_at == starts


def test_commitment_at_or_before_reference_is_rejected():
    user = "我在宿舍，90分钟后去教学楼上课"
    with pytest.raises(ExtractionNormalizationError) as exc:
        normalize_window_extraction(window_reply(user, NOW), user, NOW)
    assert exc.value.error_type == "time_format_error"


def test_date_without_clock_is_not_a_complete_iso_datetime():
    user = "我在宿舍，90分钟后去教学楼上课"
    payload = json.loads(window_reply(user, NOW + timedelta(minutes=90)))
    payload["commitments"][0]["starts_at"] = "2026-08-17"
    with pytest.raises(ExtractionNormalizationError) as exc:
        normalize_window_extraction(json.dumps(payload), user, NOW)
    assert exc.value.error_type == "time_format_error"


def test_existing_window_v1_output_is_returned_unchanged():
    raw = '{"schema_version":"p1.window-context.v1"}'
    assert normalize_window_extraction(raw, "输入", NOW) == raw


@pytest.mark.parametrize("payload", [
    {"schema_version": "p1.window-extraction.v2", "current_location": None,
     "commitments": [], "free_duration_minutes": None, "ends_at": None,
     "user_buffer_minutes": None, "unknown": True},
    {"schema_version": "p1.window-extraction.v2", "current_location": None,
     "commitments": "bad", "free_duration_minutes": None, "ends_at": None,
     "user_buffer_minutes": None},
])
def test_compact_window_unknown_fields_and_wrong_types_are_rejected(payload):
    with pytest.raises(ExtractionNormalizationError):
        normalize_window_extraction(json.dumps(payload), "输入", NOW)
