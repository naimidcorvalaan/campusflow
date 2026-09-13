"""P4a-b3-3: bounded execution-sequence movement integration."""

from datetime import datetime

import pytest

import src.p4_execution_movement as execution_movement
from src.p2_allocator import allocate_tasks_across_windows
from src.p2_day_intake import DayIntakeProposal, IntakeCommitment, IntakeTask, apply_day_intake
from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
from src.p3_map_schema import TransportMode
from src.p3_route_planner import final_plan_overlap_errors
from src.p4_execution_context import (
    CurrentLocationSource,
    ExecutionConfirmationKind,
    ExecutionLocationSource,
)
from src.p4_execution_enrichment import (
    earliest_start_overrides,
    effective_duration_overrides,
    enrich_intake_execution_context,
    preferred_start_overrides,
    protected_meal_duration_overrides,
)


REFERENCE = datetime(2026, 9, 1, 9, 0)


def _proposal(tasks, commitments=(), transport_mode="walk"):
    return DayIntakeProposal(
        schema_version="p2.day-intake.v1",
        commitments=tuple(commitments),
        tasks=tuple(tasks),
        day_end="22:00",
        questions=(),
        transport_mode=transport_mode,
    )


def _outcome(
    tasks, current=None, commitments=(), transport_mode="walk",
    campus_id="weijinlu", reference=REFERENCE,
):
    proposal = _proposal(tasks, commitments, transport_mode)
    applied = apply_day_intake(reference, proposal)
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map(campus_id)
    context = enrich_intake_execution_context(
        proposal, applied, map_data, current_location_text=current
    )
    overrides = effective_duration_overrides(context, applied.state)
    provisional = allocate_tasks_across_windows(
        applied.state, effective_duration_by_task_ref=overrides
    )
    outcome = execution_movement.apply_execution_sequence_movements(
        applied.state,
        provisional,
        context,
        map_data,
        effective_duration_by_task_ref=overrides,
        protected_duration_by_task_ref=protected_meal_duration_overrides(
            context, applied.state
        ),
        earliest_start_by_task_ref=earliest_start_overrides(
            context, applied.state
        ),
        preferred_start_by_task_ref=preferred_start_overrides(
            context, applied.state
        ),
    )
    return proposal, applied, context, outcome


def test_future_dinner_window_splits_final_capacity_without_delaying_prior_work():
    reference = datetime(2026, 9, 1, 14, 0)
    _, applied, context, outcome = _outcome(
        (
            IntakeTask(
                "写作业", total_minutes=90, is_splittable=True,
                minimum_slice_minutes=15, activity_kind="generic",
            ),
            IntakeTask(
                "背单词", total_minutes=60, is_splittable=True,
                minimum_slice_minutes=15, activity_kind="generic",
            ),
            IntakeTask(
                "吃晚饭", activity_kind="meal", meal_period="dinner",
                meal_before_commitment_index=1,
            ),
        ),
        commitments=(IntakeCommitment(
            "上课", starts_at="19:00", ends_at="20:30",
            location_text="第九教学楼", commitment_kind="class",
        ),),
        reference=reference,
    )
    assert outcome.applied is True
    events = execution_movement.execution_timeline(
        outcome.state, outcome.allocation_plan, context,
        DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu"),
    )
    task_events = {
        item.activity_ref: item for item in events if item.activity_type == "task"
    }
    assert task_events[applied.new_task_refs[0]].starts_at == reference
    assert task_events[applied.new_task_refs[0]].ends_at == reference.replace(hour=15, minute=30)
    assert task_events[applied.new_task_refs[1]].ends_at == reference.replace(hour=16, minute=30)
    meal = task_events[applied.new_task_refs[2]]
    assert meal.starts_at == reference.replace(hour=17, minute=0)
    assert meal.ends_at == reference.replace(hour=17, minute=40)
    assert outcome.allocation_plan.planned_minutes_by_task[meal.activity_ref] == 40


def test_soft_dinner_window_yields_to_a_feasible_preclass_meal_but_explicit_time_does_not():
    reference = datetime(2026, 9, 1, 16, 0)
    commitment = IntakeCommitment(
        "上课", starts_at="17:30", ends_at="19:00",
        location_text="第九教学楼", commitment_kind="class",
    )
    _, applied, context, outcome = _outcome(
        (IntakeTask(
            "吃晚饭", activity_kind="meal", meal_period="dinner",
            meal_before_commitment_index=1,
        ),),
        commitments=(commitment,), reference=reference,
    )
    meal_ref = applied.new_task_refs[0]
    meal = next(
        item for item in execution_movement.execution_timeline(
            outcome.state, outcome.allocation_plan, context,
            DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu"),
        ) if item.activity_ref == meal_ref
    )
    assert meal.starts_at == reference
    assert meal.ends_at == reference.replace(hour=16, minute=40)

    _, explicit_applied, explicit_context, explicit_outcome = _outcome(
        (IntakeTask(
            "17:00以后吃晚饭", activity_kind="meal", meal_period="dinner",
            meal_time="17:00", meal_before_commitment_index=1,
        ),),
        commitments=(commitment,), reference=reference,
    )
    explicit_ref = explicit_applied.new_task_refs[0]
    assert earliest_start_overrides(
        explicit_context, explicit_applied.state
    )[explicit_ref] == reference.replace(hour=17, minute=0)
    assert explicit_ref not in preferred_start_overrides(
        explicit_context, explicit_applied.state
    )
    # The explicit hard start is infeasible before this class, so the formal
    # movement pass must fail closed rather than silently eating before 17:00.
    assert explicit_outcome.applied is False
    assert meal_ref not in earliest_start_overrides(context, applied.state)
    assert preferred_start_overrides(context, applied.state)[meal_ref] == reference.replace(
        hour=17, minute=0
    )


def test_soft_meal_boundary_does_not_move_a_single_session_task_across_1700():
    reference = datetime(2026, 9, 1, 16, 0)
    _, applied, context, outcome = _outcome(
        (
            IntakeTask(
                "一次写完学习任务", total_minutes=120,
                is_splittable=False, activity_kind="generic",
            ),
            IntakeTask("吃晚饭", activity_kind="meal", meal_period="dinner"),
        ),
        current="学三食堂",
        commitments=(IntakeCommitment(
            "上课", starts_at="21:00", ends_at="22:00",
            location_text="第九教学楼", commitment_kind="class",
        ),),
        reference=reference,
    )
    events = execution_movement.execution_timeline(
        outcome.state, outcome.allocation_plan, context,
        DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu"),
    )
    task = next(item for item in events if item.activity_ref == applied.new_task_refs[0])
    meal = next(item for item in events if item.activity_ref == applied.new_task_refs[1])
    assert (task.starts_at, task.ends_at) == (
        reference, reference.replace(hour=18, minute=0)
    )
    assert (meal.starts_at, meal.ends_at) == (
        reference.replace(hour=18, minute=0),
        reference.replace(hour=18, minute=40),
    )


def test_current_task_meal_commitment_form_one_campus_local_route_sequence():
    _, _, context, outcome = _outcome(
        (
            IntakeTask("写报告", total_minutes=20, is_splittable=False, location_text="图书馆", activity_kind="generic"),
            IntakeTask("吃饭", activity_kind="meal"),
        ),
        current="31斋",
        commitments=(IntakeCommitment("上课", starts_at="15:00", ends_at="16:00", location_text="第九教学楼", commitment_kind="class"),),
    )
    assert outcome.applied is True
    assert [(item.origin_name, item.destination_name) for item in outcome.blocks] == [
        ("31斋", "图书馆"), ("图书馆", context.bindings[1].execution_location.display_name),
        (context.bindings[1].execution_location.display_name, "第九教学楼"),
    ]
    assert all(item.origin_node_id != item.destination_node_id and item.distance_m > 0 for item in outcome.blocks)
    assert all(item.destination_activity_ref for item in outcome.blocks)
    assert final_plan_overlap_errors(outcome.state, outcome.allocation_plan, outcome.blocks) == ()


def test_narrative_preclass_meal_is_routed_to_its_bound_commitment_not_reinterpreted_as_dinner():
    """A stable meal-before-class fact produces the real outbound class route."""
    _, applied, context, outcome = _outcome(
        (
            IntakeTask(
                "写报告", total_minutes=200, is_splittable=True, minimum_slice_minutes=10,
                location_text="图书馆", activity_kind="generic",
            ),
            IntakeTask(
                "吃饭", activity_kind="meal", meal_period="unspecified",
                meal_before_commitment_index=1,
            ),
        ),
        current="31斋",
        commitments=(IntakeCommitment(
            "上课", starts_at="15:00", ends_at="16:30", location_text="第九教学楼",
            commitment_kind="class",
        ),),
    )
    meal_ref = applied.new_task_refs[1]
    assert context.binding_for(meal_ref).meal_before_commitment_ref == "day_commitment_001"
    assert outcome.applied is True
    assert any(
        block.destination_activity_ref == "day_commitment_001"
        and block.origin_activity_ref == meal_ref
        for block in outcome.blocks
    )
    assert outcome.allocation_plan.planned_minutes_by_task.get(meal_ref, 0) in (0, 40)


def test_unlocated_task_keeps_the_previous_place_and_creates_no_fake_leg():
    _, _, context, outcome = _outcome(
        (
            IntakeTask("在图书馆写报告", total_minutes=20, is_splittable=False, location_text="图书馆", activity_kind="generic"),
            IntakeTask("背单词", total_minutes=15, is_splittable=False, activity_kind="generic"),
            IntakeTask("吃饭", activity_kind="meal"),
        )
    )
    canteen = context.bindings[2].execution_location.display_name
    assert outcome.applied is True
    assert [(item.origin_name, item.destination_name) for item in outcome.blocks] == [("图书馆", canteen)]


def test_same_node_and_shared_physical_anchor_do_not_create_movement():
    _, _, _, same_node = _outcome((
        IntakeTask("第一段", total_minutes=20, is_splittable=False, location_text="图书馆", activity_kind="generic"),
        IntakeTask("第二段", total_minutes=20, is_splittable=False, location_text="图书馆", activity_kind="generic"),
    ))
    assert same_node.blocks == ()

    _, _, _, colocated = _outcome((
        IntakeTask("出版社事务", total_minutes=20, is_splittable=False, location_text="出版社", activity_kind="generic"),
        IntakeTask("期刊中心事务", total_minutes=20, is_splittable=False, location_text="期刊中心", activity_kind="generic"),
    ))
    assert colocated.blocks == ()


def test_unknown_current_does_not_invent_the_first_leg_but_routes_later_locations():
    _, _, context, outcome = _outcome((
        IntakeTask("写报告", total_minutes=20, is_splittable=False, location_text="图书馆", activity_kind="generic"),
        IntakeTask("吃饭", activity_kind="meal"),
    ))
    assert outcome.applied is True
    assert len(outcome.blocks) == 1
    assert outcome.blocks[0].origin_name == "图书馆"
    assert outcome.blocks[0].destination_name == context.bindings[1].execution_location.display_name


def test_unknown_current_assumes_the_first_routeable_execution_location():
    proposal, applied, context, _ = _outcome((
        IntakeTask("写报告", total_minutes=20, is_splittable=False, location_text="图书馆", activity_kind="generic"),
        IntakeTask("吃饭", activity_kind="meal"),
    ))
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu")
    overrides = effective_duration_overrides(context, applied.state)
    provisional = allocate_tasks_across_windows(
        applied.state, effective_duration_by_task_ref=overrides
    )
    events = execution_movement.execution_timeline(applied.state, provisional, context, map_data)
    assumed = execution_movement.assume_current_location_for_timeline(context, events)

    assert assumed.current_location.source is CurrentLocationSource.ASSUMED
    assert assumed.current_location.location.display_name == "图书馆"
    assert any(
        item.kind is ExecutionConfirmationKind.CURRENT_LOCATION_ASSUMED
        for item in assumed.confirmations
    )
    outcome = execution_movement.apply_execution_sequence_movements(
        applied.state, provisional, assumed, map_data,
        effective_duration_by_task_ref=overrides,
    )
    assert [(item.origin_name, item.destination_name) for item in outcome.blocks] == [
        ("图书馆", assumed.binding_for("day_task_002").execution_location.display_name),
    ]


def test_assumption_skips_unlocated_tasks_and_meal_first_keeps_provenance_separate():
    proposal = _proposal((
        IntakeTask("背单词", total_minutes=15, is_splittable=False, activity_kind="generic"),
        IntakeTask("写报告", total_minutes=20, is_splittable=False, location_text="图书馆", activity_kind="generic"),
    ))
    applied = apply_day_intake(REFERENCE, proposal)
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu")
    context = enrich_intake_execution_context(proposal, applied, map_data)
    provisional = allocate_tasks_across_windows(applied.state)
    events = execution_movement.execution_timeline(applied.state, provisional, context, map_data)
    assumed = execution_movement.assume_current_location_for_timeline(context, events)
    assert assumed.current_location.location.display_name == "图书馆"

    meal_first = _proposal(
        (IntakeTask("吃饭", activity_kind="meal"),),
        commitments=(IntakeCommitment("上课", starts_at="15:00", ends_at="16:00", location_text="第九教学楼", commitment_kind="class"),),
    )
    meal_applied = apply_day_intake(REFERENCE, meal_first)
    meal_context = enrich_intake_execution_context(meal_first, meal_applied, map_data)
    meal = meal_context.binding_for("day_task_001")
    assert meal.execution_location.source is ExecutionLocationSource.AUTO_SELECTED_MEAL
    meal_provisional = allocate_tasks_across_windows(
        meal_applied.state,
        effective_duration_by_task_ref=effective_duration_overrides(meal_context, meal_applied.state),
    )
    meal_events = execution_movement.execution_timeline(
        meal_applied.state, meal_provisional, meal_context, map_data
    )
    assumed_meal = execution_movement.assume_current_location_for_timeline(meal_context, meal_events)
    assert assumed_meal.current_location.source is CurrentLocationSource.ASSUMED
    assert assumed_meal.current_location.location.node_id == meal.execution_location.node_id
    assert assumed_meal.binding_for("day_task_001").execution_location.source is ExecutionLocationSource.AUTO_SELECTED_MEAL


def test_unknown_context_remains_unknown_when_no_location_fact_exists():
    _, applied, context, outcome = _outcome((
        IntakeTask("吃饭", activity_kind="meal"),
        IntakeTask("背单词", total_minutes=15, is_splittable=False, activity_kind="generic"),
    ))
    assert context.current_location.source is CurrentLocationSource.UNKNOWN
    assert context.binding_for("day_task_001").execution_location is None
    assert outcome.blocks == ()
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu")
    events = execution_movement.execution_timeline(
        applied.state, allocate_tasks_across_windows(applied.state), context, map_data
    )
    assert execution_movement.assume_current_location_for_timeline(context, events) == context


def test_meal_duration_and_route_occupancy_are_separate_facts():
    _, _, _, outcome = _outcome((
        IntakeTask("写报告", total_minutes=20, is_splittable=False, location_text="图书馆", activity_kind="generic"),
        IntakeTask("吃饭", activity_kind="meal"),
    ), current="31斋")
    assert outcome.allocation_plan.planned_minutes_by_task["day_task_002"] == 40
    assert sum(block.estimated_minutes + block.transition_minutes for block in outcome.blocks) > 0
    assert final_plan_overlap_errors(outcome.state, outcome.allocation_plan, outcome.blocks) == ()


def test_bike_sequence_uses_bike_routes_and_map_scope_is_enforced():
    _, _, _, outcome = _outcome((
        IntakeTask("写报告", total_minutes=20, is_splittable=False, location_text="图书馆", activity_kind="generic"),
        IntakeTask("吃饭", activity_kind="meal"),
    ), current="31斋", transport_mode="bike")
    assert outcome.applied is True
    assert all(block.mode is TransportMode.BIKE for block in outcome.blocks)

    proposal, applied, context, _ = _outcome((
        IntakeTask("写报告", total_minutes=20, is_splittable=False, location_text="图书馆", activity_kind="generic"),
    ))
    other_map = DEFAULT_CAMPUS_REGISTRY.get_campus_map("beiyangyuan")
    overrides = effective_duration_overrides(context, applied.state)
    provisional = allocate_tasks_across_windows(applied.state, effective_duration_by_task_ref=overrides)
    with pytest.raises(ValueError, match="selected campus"):
        execution_movement.apply_execution_sequence_movements(
            applied.state, provisional, context, other_map, effective_duration_by_task_ref=overrides
        )


def test_consistency_guard_degrades_instead_of_returning_stale_route(monkeypatch):
    original = execution_movement.execution_timeline
    calls = {"count": 0}

    def reordered(*args, **kwargs):
        calls["count"] += 1
        events = original(*args, **kwargs)
        return events if calls["count"] == 1 else tuple(reversed(events))

    monkeypatch.setattr(execution_movement, "execution_timeline", reordered)
    _, _, _, outcome = _outcome((
        IntakeTask("写报告", total_minutes=20, is_splittable=False, location_text="图书馆", activity_kind="generic"),
        IntakeTask("吃饭", activity_kind="meal"),
    ), current="31斋")
    assert outcome.applied is False
    assert outcome.blocks == ()
    assert any("执行顺序" in warning for warning in outcome.warnings)
