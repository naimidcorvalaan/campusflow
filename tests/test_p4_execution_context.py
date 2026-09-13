import pytest
from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY, resolve_location_in_campus
from src.p4_execution_context import *

def loc(campus, text, source=ExecutionLocationSource.EXPLICIT_TASK_LOCATION):
    return ExecutionLocation.from_resolution(resolve_location_in_campus(DEFAULT_CAMPUS_REGISTRY, campus, text), source)

def test_location_binding_and_current_context_invariants():
    library = loc("weijinlu", "图书馆")
    binding = ExecutableTaskBinding("day_task_001", library, "generic")
    ctx = ExecutionPlanContext((binding,), CurrentLocationContext(library, CurrentLocationSource.USER))
    assert ctx.binding_for("day_task_001") == binding
    assert ctx.upsert(ExecutableTaskBinding("day_task_001", activity_kind="meal")).binding_for("day_task_001").activity_kind == "meal"
    with pytest.raises(ValueError): CurrentLocationContext(library, CurrentLocationSource.UNKNOWN)
    with pytest.raises(ValueError): ExecutionPlanContext((binding, ExecutableTaskBinding("day_task_001")))

def test_context_persists_and_replaces_assumption_without_residue():
    assumed = CurrentLocationContext(loc("weijinlu", "图书馆"), CurrentLocationSource.ASSUMED)
    ctx = ExecutionPlanContext(current_location=assumed)
    user = ctx.with_current_location(CurrentLocationContext(loc("weijinlu", "友园"), CurrentLocationSource.USER))
    store = {}; save_execution_context(store, user)
    assert load_execution_context(store).current_location.source is CurrentLocationSource.USER
    assert load_execution_context(store).current_location.location.display_name == "友园"

def test_context_rejects_cross_campus_locations():
    with pytest.raises(ValueError):
        ExecutionPlanContext((ExecutableTaskBinding("a", loc("weijinlu", "图书馆")),), CurrentLocationContext(loc("beiyangyuan", "9斋"), CurrentLocationSource.USER))


def test_meal_temporal_binding_rejects_one_commitment_as_both_before_and_after():
    with pytest.raises(ValueError):
        ExecutableTaskBinding(
            "day_task_001", activity_kind="meal",
            not_before_commitment_ref="day_commitment_001",
            meal_before_commitment_ref="day_commitment_001",
        )
