"""Production protocol and duration provenance regressions; no live requests."""
import json
from dataclasses import replace
from datetime import datetime

import pytest

from src.p1_models import SourceKind
from src.p2_agentic_pipeline import _apply_estimates
from src.p2_agentic_models import DayPlanIntent, TaskEstimate
from src.p2_allocator import allocate_tasks_across_windows
from src.p2_day_intake import DayIntakeProposal, IntakeTask, apply_day_intake, run_day_intake
from src.p2_day_plan import compact_plan_lines
from src.p5_agent_runtime import StructuredValidationError
from src.p5_situation_analyst import (
    analyze_situation, build_situation_prompt, parse_situation_analysis,
    situation_output_example, situation_output_schema,
)
from tests.test_p5_agent_intelligence import _context


@pytest.mark.parametrize("refs", [(), ("stable_a-71",), ("stable_a-71", "stable_b-89")])
def test_prompt_schema_and_example_are_the_formal_parser_contract(refs):
    context = _context()
    tasks = tuple(replace(task, task_ref=ref) for task, ref in zip(context.active_tasks, refs))
    context = replace(context, active_tasks=tasks, ordering_constraints=(), selected_plan=())
    system, user = build_situation_prompt(context)
    schema = situation_output_schema(context)
    example = situation_output_example(context)
    assert json.dumps(schema, ensure_ascii=False) in system
    assert json.dumps(example, ensure_ascii=False) in system
    assert set(example) == set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False
    ref_enum = schema["properties"]["task_priority_assessment"]["items"]["properties"]["task_ref"]["enum"]
    assert ref_enum == list(refs)
    assert json.dumps(list(refs), ensure_ascii=False) in user
    assert "p5.agent-decision-context.v1" not in user
    result = parse_situation_analysis(json.dumps(example), context)
    assert len(result.task_priority_assessment) == min(len(refs), 1)


@pytest.mark.parametrize("field,value,path,code", [
    ("important_constraints", "private text", "$.important_constraints", "list_type"),
    ("timing_pressure", None, "$.timing_pressure", "list_type"),
    ("risk_flags", [{"private_key": "private text"}], "$.risk_flags[0]", "type_mismatch"),
    ("task_priority_assessment", {"private_ref": "high"}, "$.task_priority_assessment", "list_type"),
    ("task_priority_assessment", ["private_ref"], "$.task_priority_assessment[0]", "type_mismatch"),
    ("meal_context", [], "$.meal_context", "type_mismatch"),
    ("schema_version", "old.schema", "$.schema_version", "schema_version"),
    ("clarification_value", "maybe", "$.clarification_value", "enum_value"),
])
def test_invalid_shapes_remain_rejected_with_safe_field_feedback(field, value, path, code):
    context = _context()
    payload = situation_output_example(context)
    payload[field] = value
    with pytest.raises(StructuredValidationError) as error:
        parse_situation_analysis(json.dumps(payload), context)
    assert error.value.issues[0][:2] == (path, code)
    assert "private" not in repr(error.value.issues)
    assert "private" not in str(error.value)


def test_missing_extra_unknown_and_duplicate_refs_are_not_canonicalized_away():
    context = _context()
    payload = situation_output_example(context)
    payload.pop("movement_context")
    payload["private_extra"] = "private value"
    payload["task_priority_assessment"][0]["task_ref"] = "private_ref"
    with pytest.raises(StructuredValidationError) as error:
        parse_situation_analysis(json.dumps(payload), context)
    assert {issue[1] for issue in error.value.issues} == {"missing_field", "field_set", "unknown_task_ref"}
    assert "private" not in repr(error.value.issues)
    payload = situation_output_example(context)
    payload["task_priority_assessment"] *= 2
    with pytest.raises(StructuredValidationError) as error:
        parse_situation_analysis(json.dumps(payload), context)
    assert error.value.issues[0][1] == "duplicate_task_ref"


def test_repair_receives_current_contract_allowed_refs_and_precise_validation_errors():
    context = _context()
    good = situation_output_example(context)
    bad = dict(good, timing_pressure="private text")
    calls = []
    def caller(system, user):
        calls.append((system, user))
        return json.dumps(bad if len(calls) == 1 else good)
    result, trace = analyze_situation(context, caller)
    assert result.generated and trace.call_count == 2 and trace.fallback_count == 0
    assert calls[0][0] in calls[1][0]
    assert "$.timing_pressure" in calls[1][1] and "list_type" in calls[1][1]
    assert "task_homework" in calls[1][1] and "task_vocab" in calls[1][1]
    assert "private text" not in repr(trace)
    assert sum(record.repair for record in trace.records) == 1


def test_genuine_double_failure_is_bounded_and_preserves_both_safe_summaries():
    context = _context()
    payload = situation_output_example(context)
    payload["task_priority_assessment"][0]["task_ref"] = "private_unknown"
    result, trace = analyze_situation(context, lambda *_: json.dumps(payload))
    assert not result.generated and trace.call_count == 2 and trace.fallback_count == 1
    assert [r.validation_issues[0][1] for r in trace.records if not r.fallback] == ["unknown_task_ref"] * 2
    assert "private_unknown" not in repr(trace)


def _applied_task(title, minutes, source, text):
    proposal = DayIntakeProposal("p2.day-intake.v1", (), (
        IntakeTask(title, minutes, is_splittable=True, duration_source=source),
    ), "20:00", ())
    return apply_day_intake(datetime(2026, 9, 15, 14), proposal, user_text=text).state


@pytest.mark.parametrize("title,minutes,text", [
    ("概率论作业", 60, "还有60分钟的概率论作业没开始，可拆分。"),
    ("阅读材料", 35, "阅读材料需要35分钟，尚未开始。"),
    ("整理笔记", 90, "整理笔记时长为1.5小时。"),
    ("整理笔记", 45, "45分钟的整理笔记，另一门课60分钟。"),
])
@pytest.mark.parametrize("source", ["ai_estimated", "semantic_estimate", "user_explicit"])
def test_explicit_duration_retains_source_through_estimation_and_rendering(title, minutes, text, source):
    state = _applied_task(title, minutes, source, text)
    intent = DayPlanIntent("p2.day-plan-intent.v1", (), False, (
        TaskEstimate("day_task_001", minutes + 70, True, 10),
    ), None)
    updated, _ = _apply_estimates(state, intent)
    task = updated.tasks[0]
    assert task.total_minutes == minutes and task.completed_minutes == 0
    assert task.total_source == SourceKind.AI_EXTRACTED_FROM_USER_TEXT
    plan = allocate_tasks_across_windows(updated)
    assert "AI暂估" not in "\n".join(compact_plan_lines(plan, updated))


@pytest.mark.parametrize("text", [
    "整理笔记。另一门课60分钟。", "整理笔记不是60分钟。",
    "整理笔记已经做了60分钟。", "整理笔记最多60分钟。",
    "整理笔记90分钟，整理笔记60分钟。", "整理笔记160分钟。",
])
def test_unrelated_ambiguous_progress_or_different_duration_is_not_user_evidence(text):
    state = _applied_task("整理笔记", 60, "ai_estimated", text)
    assert state.tasks[0].total_source == SourceKind.AI_ESTIMATED
    assert "AI暂估" in "\n".join(compact_plan_lines(allocate_tasks_across_windows(state), state))


def test_semantic_estimate_is_not_promoted_without_numeric_user_evidence():
    state = _applied_task("复习", 60, "semantic_estimate", "复习一会儿。")
    assert state.tasks[0].total_source == SourceKind.AI_ESTIMATED
    unknown = _applied_task("复习", None, None, "复习。")
    assert unknown.tasks[0].total_minutes is None and unknown.tasks[0].total_source is None


@pytest.mark.parametrize("title,minutes,text", [
    ("概率论作业", 60, "还有60分钟的概率论作业没开始，可拆分。"),
    ("阅读材料", 35, "阅读材料需要35分钟，尚未开始。"),
    ("整理笔记", 90, "整理笔记时长为1.5小时。"),
])
def test_intake_omission_cannot_turn_an_explicit_duration_into_a_later_estimate(title, minutes, text):
    state = _applied_task(title, None, None, text)
    assert state.tasks[0].total_minutes == minutes
    assert state.tasks[0].total_source == SourceKind.AI_EXTRACTED_FROM_USER_TEXT
    intent = DayPlanIntent("p2.day-plan-intent.v1", (), False, (
        TaskEstimate("day_task_001", minutes + 70, True, 10),
    ), None)
    updated, _ = _apply_estimates(state, intent)
    assert updated.tasks[0].total_minutes == minutes
    assert updated.tasks[0].completed_minutes == 0
    assert updated.tasks[0].task_ref == "day_task_001"
    assert "AI暂估" not in "\n".join(compact_plan_lines(allocate_tasks_across_windows(updated), updated))


@pytest.mark.parametrize("text", [
    "整理笔记。另一门课60分钟。", "整理笔记已经做了60分钟。",
    "整理笔记最多60分钟。", "整理笔记90分钟，整理笔记60分钟。",
    "30到60分钟的整理笔记。", "整理笔记60分钟或90分钟。", "整理笔记-60分钟。",
])
def test_missing_duration_stays_unknown_without_unambiguous_task_bound_evidence(text):
    state = _applied_task("整理笔记", None, None, text)
    assert state.tasks[0].total_minutes is None and state.tasks[0].total_source is None


def test_downstream_proposal_and_applied_state_share_preserved_duration_and_source():
    payload = {"schema_version": "p2.day-intake.v1", "commitments": [],
               "tasks": [{"title": "整理笔记", "total_minutes": None,
                          "is_splittable": True, "minimum_slice_minutes": None}],
               "day_end": "20:00", "questions": []}
    calls = []
    def caller(*args):
        calls.append(args)
        return json.dumps(payload)
    outcome = run_day_intake(datetime(2026, 9, 15, 14), "整理笔记需要35分钟。", caller)
    assert len(calls) == 1
    assert outcome.proposal.tasks[0].total_minutes == outcome.applied.state.tasks[0].total_minutes == 35
    assert outcome.proposal.tasks[0].duration_source == "user_explicit"
    assert outcome.applied.state.tasks[0].total_source == SourceKind.AI_EXTRACTED_FROM_USER_TEXT
