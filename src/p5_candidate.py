"""Deterministic candidate envelope for P5 strategy selection.

This module does not plan minutes itself.  It asks the existing deterministic
planner through a callback, rejects invalid results before Qwen sees them, and
produces compact objective summaries.
"""

from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from src.p5_plan_judge import CandidateSummary
from src.p5_plan_strategy import PlanStrategy


@dataclass(frozen=True)
class DeterministicPlanCandidate:
    candidate_id: str
    strategy: PlanStrategy
    result: object
    movement_blocks: Tuple[object, ...]
    concurrent_allocations: Tuple[object, ...]
    summary: CandidateSummary

    def __post_init__(self):
        if self.summary.candidate_id != self.candidate_id:
            raise ValueError("candidate summary id mismatch")
        if self.summary.strategy_id != self.strategy.strategy_id:
            raise ValueError("candidate strategy id mismatch")


@dataclass(frozen=True)
class CandidateGenerationResult:
    candidates: Tuple[DeterministicPlanCandidate, ...]
    rejected_strategy_ids: Tuple[str, ...] = ()


def generate_deterministic_candidates(strategies, build_candidate, validate_candidate=None):
    """Build each candidate once; invalid candidates never reach Plan Judge."""
    strategies = tuple(strategies)
    if not strategies or any(not isinstance(item, PlanStrategy) for item in strategies):
        raise ValueError("strategies must contain PlanStrategy values")
    if not callable(build_candidate):
        raise TypeError("build_candidate must be callable")
    validator = validate_candidate if validate_candidate is not None else (lambda value: ())
    if not callable(validator):
        raise TypeError("validate_candidate must be callable")
    accepted = []
    rejected = []
    for index, strategy in enumerate(strategies[:4]):
        candidate_id = "candidate_{:02d}".format(index + 1)
        try:
            built = build_candidate(strategy, candidate_id)
            candidate = _coerce_candidate(candidate_id, strategy, built)
            errors = tuple(validator(candidate) or ())
        except Exception:
            candidate = None
            errors = ("candidate build failed",)
        if candidate is None or errors:
            rejected.append(strategy.strategy_id)
            continue
        accepted.append(candidate)
    return CandidateGenerationResult(tuple(accepted), tuple(rejected))


def summarize_candidate(
    candidate_id,
    strategy,
    state,
    result,
    movement_blocks=(),
    concurrent_allocations=(),
    timeline=(),
    unresolved_questions=(),
    preferred_next_task_ref=None,
    meal_task_refs=(),
):
    plan = result.allocation_plan
    completion = tuple(sorted(plan.planned_minutes_by_task.items()))
    task_by_ref = {item.task_ref: item for item in state.tasks}
    remaining = []
    for ref, task in sorted(task_by_ref.items()):
        value = task.remaining_minutes
        if value is not None:
            value = max(0, value - plan.planned_minutes_by_task.get(ref, 0))
            value -= sum(
                item.planned_minutes for item in concurrent_allocations
                if getattr(item, "task_ref", None) == ref
            )
            remaining.append((ref, max(0, value)))
    sequence = [item.task_ref for item in plan.allocations]
    switches = sum(1 for left, right in zip(sequence, sequence[1:]) if left != right)
    preference_ok = True
    if preferred_next_task_ref:
        preference_ok = bool(sequence and sequence[0] == preferred_next_task_ref)
    # Rendered Chinese lines intentionally contain no internal refs.  Keep
    # meal timing grounded in structured allocation facts instead of trying
    # to rediscover meals by string matching presentation text.
    meal_refs = set(meal_task_refs or ())
    meal_lines = tuple(
        "{}:{}min".format(ref, plan.planned_minutes_by_task[ref])
        for ref in sorted(meal_refs)
        if plan.planned_minutes_by_task.get(ref, 0) > 0
    )
    return CandidateSummary(
        candidate_id=candidate_id,
        strategy_id=strategy.strategy_id,
        timeline=tuple(str(item) for item in timeline),
        task_completion=completion,
        remaining_work=tuple(remaining),
        movement_count=len(tuple(movement_blocks or ())),
        task_switches=switches,
        slack_minutes=sum(value for _, value in plan.unused_capacity_by_window),
        meal_timing=meal_lines,
        user_preference_satisfied=preference_ok,
        concurrency_pairs=tuple(
            (item.task_ref, item.commitment_ref) for item in concurrent_allocations or ()
        ),
        unresolved_questions=tuple(unresolved_questions or ()),
        task_sequence=tuple(sequence),
    )


def _coerce_candidate(candidate_id, strategy, built):
    if isinstance(built, DeterministicPlanCandidate):
        if built.candidate_id != candidate_id or built.strategy != strategy:
            raise ValueError("candidate builder returned mismatched envelope")
        return built
    if not isinstance(built, dict):
        raise TypeError("candidate builder must return dict or DeterministicPlanCandidate")
    result = built.get("result")
    state = built.get("state") or getattr(result, "updated_state", None)
    if result is None or state is None:
        raise ValueError("candidate result/state missing")
    movements = tuple(built.get("movement_blocks") or ())
    concurrent = tuple(built.get("concurrent_allocations") or ())
    summary = built.get("summary") or summarize_candidate(
        candidate_id, strategy, state, result, movements, concurrent,
        timeline=built.get("timeline") or (),
        unresolved_questions=built.get("unresolved_questions") or (),
        preferred_next_task_ref=built.get("preferred_next_task_ref"),
    )
    return DeterministicPlanCandidate(
        candidate_id, strategy, result, movements, concurrent, summary
    )
