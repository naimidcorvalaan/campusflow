"""Independent deadline oracle for evaluation fixtures, never product planning.

Expectations are annotated from the fixture's user requirement, not inferred
from the model's extracted deadline. Otherwise a lost relation would pass.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.p3_class_prep import class_arrival_deadline


@dataclass(frozen=True)
class DeadlineExpectation:
    task_ref: str
    before_commitment_ref: Optional[str] = None
    latest_finish: Optional[datetime] = None
    require_complete: bool = True


def deadline_evaluation_errors(state, events, movement_blocks, expectations):
    tasks = {t.task_ref: t for t in state.tasks}
    commitments = {c.commitment_ref: c for c in state.commitments}
    errors = []
    for expected in expectations:
        task = tasks.get(expected.task_ref)
        commitment = commitments.get(expected.before_commitment_ref)
        if task is None or (expected.before_commitment_ref and commitment is None):
            errors.append("deadline_expectation_unknown_ref")
            continue
        boundaries = [v for v in (expected.latest_finish,
                                  commitment.starts_at if commitment else None) if v is not None]
        if not boundaries:
            errors.append("deadline_expectation_missing_boundary")
            continue
        deadline = min(boundaries)
        segments = [e for e in events if e.activity_type == "task" and e.activity_ref == task.task_ref]
        if any(e.ends_at > deadline for e in segments):
            errors.append("task_finishes_after_required_deadline:" + task.task_ref)
        planned = sum(int((e.ends_at - e.starts_at).total_seconds() // 60) for e in segments)
        if expected.require_complete and (task.remaining_minutes is None or planned < task.remaining_minutes):
            errors.append("required_work_not_completed_by_deadline:" + task.task_ref)
        if commitment is not None:
            arrival = class_arrival_deadline(commitment) or commitment.starts_at
            for block in movement_blocks:
                if block.destination_activity_ref != commitment.commitment_ref:
                    continue
                if block.end_time > arrival:
                    errors.append("required_commitment_arrival_missed:" + commitment.commitment_ref)
                if block.origin_activity_ref == task.task_ref:
                    departure = block.transition_start or block.window_start
                    if any(e.ends_at > departure for e in segments):
                        errors.append("deadline_task_overruns_departure:" + task.task_ref)
    return tuple(dict.fromkeys(errors))
