from dataclasses import replace
from datetime import datetime

from src.p2_day_intake import DayIntakeProposal, IntakeApplied, IntakeCommitment, IntakeTask, apply_day_intake
from src.p2_models import TaskState
from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
from src.p3_location_resolver import resolve_location
from src.p4_execution_context import (
    CurrentLocationSource,
    ExecutionConfirmationKind,
    ExecutionLocationSource,
)
from src.p4_execution_enrichment import (
    DURATION_SOURCE_MEAL_DEFAULT,
    DURATION_SOURCE_SEMANTIC_ESTIMATE,
    DURATION_SOURCE_USER_EXPLICIT,
    MEAL_DEFAULT_MINUTES,
    enrich_intake_execution_context,
    earliest_start_overrides,
    reconcile_execution_context,
)
from src.p4_meal_rules import choose_canteen_nearest_to_destination, choose_nearest_canteen


REFERENCE = datetime(2026, 9, 1, 9, 0)


def proposal(*tasks, commitments=(), transport_mode=None):
    return DayIntakeProposal(
        schema_version="p2.day-intake.v1",
        commitments=tuple(commitments),
        tasks=tasks,
        day_end="22:00",
        questions=(),
        transport_mode=transport_mode,
    )


def enrich(tasks, campus_id="weijinlu", current_location_text=None, commitments=(), transport_mode=None):
    parsed = proposal(*tasks, commitments=commitments, transport_mode=transport_mode)
    applied = apply_day_intake(REFERENCE, parsed)
    return parsed, applied, enrich_intake_execution_context(
        parsed,
        applied,
        DEFAULT_CAMPUS_REGISTRY.get_campus_map(campus_id),
        current_location_text=current_location_text,
    )


def test_intake_task_refs_are_joined_by_apply_day_intake_order_not_title():
    tasks = (
        IntakeTask("写报告", location_text="图书馆", activity_kind="generic"),
        IntakeTask("吃饭", activity_kind="meal"),
    )
    parsed, applied, context = enrich(tasks)
    assert applied.new_task_refs == ("day_task_001", "day_task_002")
    assert context.binding_for(applied.new_task_refs[0]).activity_kind == parsed.tasks[0].activity_kind
    assert context.binding_for(applied.new_task_refs[1]).activity_kind == parsed.tasks[1].activity_kind


def test_after_commitment_semantic_becomes_a_real_allocator_earliest_start():
    commitment = IntakeCommitment(
        "上课", starts_at="21:00", relative_end_minutes=90,
        location_text="9教", commitment_kind="class",
    )
    parsed, applied, context = enrich((
        IntakeTask("写作业", total_minutes=60, activity_kind="generic", after_commitment_index=1),
        IntakeTask("背单词", total_minutes=20, activity_kind="generic", after_commitment_index=1),
    ), commitments=(commitment,))
    assert context.binding_for(applied.new_task_refs[0]).not_before_commitment_ref == "day_commitment_001"
    assert earliest_start_overrides(context, applied.state) == {
        "day_task_001": datetime(2026, 9, 1, 22, 30),
        "day_task_002": datetime(2026, 9, 1, 22, 30),
    }


def test_meal_temporal_semantics_are_bound_to_stable_commitment_refs():
    commitment = IntakeCommitment(
        "上课", starts_at="17:00", ends_at="18:30",
        location_text="第九教学楼", commitment_kind="class",
    )
    parsed, applied, context = enrich((
        IntakeTask(
            "课前吃饭", activity_kind="meal", meal_period="unspecified",
            meal_before_commitment_index=1,
        ),
        IntakeTask(
            "下课后吃饭", activity_kind="meal", meal_period="dinner",
            after_commitment_index=1,
        ),
    ), commitments=(commitment,))
    before = context.binding_for(applied.new_task_refs[0])
    after = context.binding_for(applied.new_task_refs[1])
    assert before.meal_before_commitment_ref == applied.new_commitment_refs[0]
    assert before.meal_temporal_source == "narrative_order"
    assert after.not_before_commitment_ref == applied.new_commitment_refs[0]
    assert after.meal_temporal_source == "narrative_order"
    assert earliest_start_overrides(context, applied.state)[after.task_ref] == datetime(2026, 9, 1, 18, 30)


def test_explicit_meal_time_is_a_deterministic_earliest_start_not_a_window_guess():
    parsed, applied, context = enrich((
        IntakeTask("18:30 吃饭", activity_kind="meal", meal_period="dinner", meal_time="18:30"),
    ))
    binding = context.binding_for(applied.new_task_refs[0])
    assert binding.meal_explicit_time == "18:30"
    assert binding.meal_temporal_source == "explicit_user"
    assert earliest_start_overrides(context, applied.state)[binding.task_ref] == datetime(2026, 9, 1, 18, 30)


def test_explicit_generic_location_becomes_campus_scoped_execution_location():
    _, applied, context = enrich((IntakeTask("写报告", location_text="图书馆", activity_kind="generic"),))
    binding = context.binding_for(applied.new_task_refs[0])
    assert binding.execution_location.display_name == "图书馆"
    assert binding.execution_location.campus_id == "weijinlu"
    assert binding.execution_location.node_id
    assert binding.execution_location.source is ExecutionLocationSource.EXPLICIT_TASK_LOCATION


def test_meal_bindings_preserve_explicit_and_missing_locations_without_auto_selection():
    _, applied, context = enrich((IntakeTask("吃饭", activity_kind="meal"),))
    unspecified = context.binding_for(applied.new_task_refs[0])
    assert unspecified.activity_kind == "meal"
    assert unspecified.execution_location is None
    _, applied, context = enrich((
        IntakeTask("去学三食堂吃饭", location_text="学三食堂", activity_kind="meal"),
    ))
    explicit = context.binding_for(applied.new_task_refs[0])
    assert explicit.activity_kind == "meal"
    assert explicit.execution_location.display_name == "学三食堂"
    assert explicit.execution_location.source is ExecutionLocationSource.EXPLICIT_TASK_LOCATION


def test_unresolved_location_never_creates_a_guessed_execution_location_or_cross_campus_fallback():
    # 9 斋 is resolvable in Beiyangyuan but not in the selected Weijinlu map.
    assert resolve_location(DEFAULT_CAMPUS_REGISTRY.get_campus_map("beiyangyuan"), "9斋").usable
    _, applied, context = enrich((IntakeTask("写报告", location_text="9斋", activity_kind="generic"),))
    binding = context.binding_for(applied.new_task_refs[0])
    assert binding.activity_kind == "generic"
    assert binding.execution_location is None


def test_old_task_without_execution_semantics_remains_unbound():
    _, applied, context = enrich((IntakeTask("背单词"),))
    assert applied.new_task_refs == ("day_task_001",)
    assert context.binding_for("day_task_001") is None


def test_small_integration_intake_to_binding_creates_no_movement_artifacts():
    tasks = (
        IntakeTask("写实验报告", location_text="图书馆", activity_kind="generic"),
        IntakeTask("吃饭", activity_kind="meal"),
    )
    _, applied, context = enrich(tasks)
    assert context.binding_for(applied.new_task_refs[0]).execution_location.display_name == "图书馆"
    assert context.binding_for(applied.new_task_refs[1]).execution_location.source is ExecutionLocationSource.AUTO_SELECTED_MEAL
    assert not hasattr(context, "movement_blocks")
    assert not hasattr(context, "movement_requests")


def test_meal_without_a_user_duration_uses_the_40_minute_execution_default():
    _, applied, context = enrich((IntakeTask("吃饭", activity_kind="meal"),))
    binding = context.binding_for(applied.new_task_refs[0])
    assert binding.effective_duration_minutes == MEAL_DEFAULT_MINUTES
    assert binding.duration_source == DURATION_SOURCE_MEAL_DEFAULT


def test_explicit_and_semantic_meal_durations_are_preserved_before_the_default():
    _, applied, context = enrich((
        IntakeTask("吃20分钟饭", total_minutes=20, activity_kind="meal", duration_source="user_explicit"),
        IntakeTask("快速吃点", total_minutes=15, activity_kind="meal", duration_source="semantic_estimate"),
    ))
    explicit = context.binding_for(applied.new_task_refs[0])
    semantic = context.binding_for(applied.new_task_refs[1])
    assert (explicit.effective_duration_minutes, explicit.duration_source) == (20, DURATION_SOURCE_USER_EXPLICIT)
    assert (semantic.effective_duration_minutes, semantic.duration_source) == (15, DURATION_SOURCE_SEMANTIC_ESTIMATE)


def test_untrusted_ai_meal_estimate_does_not_displace_the_product_default():
    _, applied, context = enrich((
        IntakeTask("吃饭", total_minutes=15, activity_kind="meal", duration_source="ai_estimated"),
    ))
    binding = context.binding_for(applied.new_task_refs[0])
    assert (binding.effective_duration_minutes, binding.duration_source) == (40, DURATION_SOURCE_MEAL_DEFAULT)


def test_non_meal_never_receives_a_meal_duration_default():
    _, applied, context = enrich((IntakeTask("写报告", activity_kind="generic"),))
    binding = context.binding_for(applied.new_task_refs[0])
    assert binding.effective_duration_minutes is None
    assert binding.duration_source is None


def test_current_location_is_resolved_independently_from_a_task_execution_location():
    _, applied, context = enrich(
        (IntakeTask("写报告", location_text="图书馆", activity_kind="generic"),),
        current_location_text="31斋",
    )
    assert context.current_location.source is CurrentLocationSource.USER
    assert context.current_location.location.display_name == "31斋"
    assert context.current_location.location.source is ExecutionLocationSource.USER_CURRENT_LOCATION
    assert context.binding_for(applied.new_task_refs[0]).execution_location.display_name == "图书馆"


def test_empty_or_cross_campus_current_location_stays_unknown_without_guessing():
    _, _, empty_context = enrich((IntakeTask("写报告", activity_kind="generic"),))
    assert empty_context.current_location.source is CurrentLocationSource.UNKNOWN
    assert empty_context.current_location.location is None

    # 9 斋 exists in Beiyangyuan but must not be borrowed by Weijinlu.
    _, _, unresolved_context = enrich(
        (IntakeTask("写报告", activity_kind="generic"),), current_location_text="9斋"
    )
    assert unresolved_context.current_location.source is CurrentLocationSource.UNKNOWN
    assert unresolved_context.current_location.location is None


def test_reconcile_removes_only_absent_task_refs_and_keeps_lifecycle_bindings():
    _, applied, context = enrich((
        IntakeTask("写报告", location_text="图书馆", activity_kind="generic"),
        IntakeTask("吃饭", activity_kind="meal"),
    ))
    retained_state = replace(applied.state, tasks=applied.state.tasks[:1])
    reconciled = reconcile_execution_context(context, retained_state)
    assert reconciled.binding_for(applied.new_task_refs[0]) is not None
    assert reconciled.binding_for(applied.new_task_refs[1]) is None


def test_reconcile_keeps_binding_when_lifecycle_changes_but_task_ref_survives():
    _, applied, context = enrich((IntakeTask("写报告", location_text="图书馆", activity_kind="generic"),))
    changed_state = replace(
        applied.state,
        tasks=(replace(applied.state.tasks[0], state=TaskState.SKIPPED_TODAY),),
    )
    assert reconcile_execution_context(context, changed_state).binding_for(applied.new_task_refs[0]) is not None


def test_reconcile_does_not_reuse_a_same_title_binding_for_a_new_task_ref():
    _, applied, context = enrich((IntakeTask("写报告", location_text="图书馆", activity_kind="generic"),))
    replacement_state = replace(
        applied.state,
        tasks=(replace(applied.state.tasks[0], task_ref="day_task_999"),),
    )
    reconciled = reconcile_execution_context(context, replacement_state)
    assert reconciled.binding_for(applied.new_task_refs[0]) is None
    assert reconciled.binding_for("day_task_999") is None


def test_unspecified_meal_uses_current_location_and_real_route_cost():
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu")
    expected = choose_nearest_canteen(map_data, "图书馆")
    _, applied, context = enrich(
        (IntakeTask("吃饭", activity_kind="meal"),),
        current_location_text="图书馆",
    )
    meal = context.binding_for(applied.new_task_refs[0])
    assert meal.execution_location.node_id == expected.node_id
    assert meal.execution_location.source is ExecutionLocationSource.AUTO_SELECTED_MEAL


def test_previous_task_location_takes_sequence_precedence_over_current_location():
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu")
    expected = choose_nearest_canteen(map_data, "图书馆")
    _, applied, context = enrich(
        (
            IntakeTask("写报告", location_text="图书馆", activity_kind="generic"),
            IntakeTask("吃饭", activity_kind="meal"),
        ),
        current_location_text="31斋",
    )
    assert context.binding_for(applied.new_task_refs[1]).execution_location.node_id == expected.node_id


def test_first_meal_uses_next_fixed_commitment_location_without_creating_an_assumption():
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu")
    expected = choose_canteen_nearest_to_destination(map_data, "第九教学楼")
    _, applied, context = enrich(
        (IntakeTask("吃饭", activity_kind="meal"),),
        commitments=(IntakeCommitment("上课", starts_at="15:00", duration_minutes=60, location_text="第九教学楼"),),
    )
    meal = context.binding_for(applied.new_task_refs[0])
    assert meal.execution_location.node_id == expected.node_id
    assert context.current_location.source is CurrentLocationSource.UNKNOWN


def test_meal_without_any_route_anchor_remains_unresolved_with_a_structured_confirmation():
    _, applied, context = enrich((
        IntakeTask("吃饭", activity_kind="meal"),
        IntakeTask("背单词", activity_kind="generic"),
    ))
    meal_ref = applied.new_task_refs[0]
    assert context.binding_for(meal_ref).execution_location is None
    assert context.confirmations[0].task_ref == meal_ref
    assert context.confirmations[0].kind is ExecutionConfirmationKind.MEAL_LOCATION_CONTEXT_REQUIRED


def test_multiple_meals_are_selected_from_their_own_sequence_contexts():
    _, applied, context = enrich((
        IntakeTask("写报告", location_text="图书馆", activity_kind="generic"),
        IntakeTask("吃饭", activity_kind="meal"),
        IntakeTask("复习", location_text="第九教学楼", activity_kind="generic"),
        IntakeTask("吃饭", activity_kind="meal"),
    ))
    first = context.binding_for(applied.new_task_refs[1])
    second = context.binding_for(applied.new_task_refs[3])
    assert first.execution_location is not None
    assert second.execution_location is not None
    assert first.execution_location.source is ExecutionLocationSource.AUTO_SELECTED_MEAL
    assert second.execution_location.source is ExecutionLocationSource.AUTO_SELECTED_MEAL


def test_auto_meal_selection_stays_inside_the_selected_campus():
    _, applied, context = enrich(
        (IntakeTask("吃饭", activity_kind="meal"),),
        campus_id="beiyangyuan",
        current_location_text="9斋",
    )
    meal = context.binding_for(applied.new_task_refs[0])
    assert meal.execution_location.campus_id == "beiyangyuan"
    assert meal.execution_location.source is ExecutionLocationSource.AUTO_SELECTED_MEAL
