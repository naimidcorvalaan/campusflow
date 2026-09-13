from datetime import datetime

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_allocator import allocate_tasks_across_windows
from src.p2_day_plan import compact_plan_lines
from src.p2_models import TaskProgress, TaskState
from src.p2_window_derivation import derive_day_state
from src.p2_commitment_reconciler import (
    COMMITMENT_SCHEMA_VERSION, apply_commitment_reconciliation, parse_commitment_reconciliation,
)
from src.p3_class_prep import class_arrival_deadline, class_prep_interval


def _dt(hour, minute=0):
    return datetime(2026, 9, 1, hour, minute)


def _class(lead=None):
    return FixedCommitment(
        "class_1", "上课", "上课", _dt(15), _dt(16), "31教",
        AvailabilityLevel.UNAVAILABLE, {}, (), "class", lead,
    )


def _state(commitment):
    task = TaskProgress("task_1", "计组实验", 90, 0, SourceKind.AI_EXTRACTED_FROM_USER_TEXT,
                        TaskState.ACTIVE, True, 15)
    return derive_day_state(_dt(13, 35), _dt(22), (commitment,), (task,), 0)


def test_class_prep_is_a_structural_capacity_segment_and_renders_human_copy():
    state = _state(_class())
    assert state.windows[0].ends_at == _dt(14, 50)
    plan = allocate_tasks_across_windows(state)
    assert plan.allocations[0].planned_minutes == 75
    lines = compact_plan_lines(plan, state)
    assert "14:50–14:55：进楼 / 找教室" in lines
    assert "14:55–15:00：到教室后签到 / 课前准备" in lines
    assert all("提前10分钟到教室" not in line for line in lines)
    assert all("class_arrival_lead" not in line and "safety buffer" not in line for line in lines)


def test_class_lead_default_and_explicit_override_are_not_general_buffers():
    assert class_arrival_deadline(_class()) == _dt(14, 50)
    assert class_arrival_deadline(_class(20)) == _dt(14, 40)
    assert class_prep_interval(_class(5)) == (_dt(14, 55), _dt(15))
    meeting = FixedCommitment("m", "组会", "组会", _dt(15), _dt(16), "31教",
                              AvailabilityLevel.UNAVAILABLE, {}, (), "meeting", None)
    assert class_prep_interval(meeting) is None


def test_feedback_lead_update_persists_and_rederives_capacity():
    state = _state(_class())
    proposal = parse_commitment_reconciliation(
        '{"schema_version":"%s","updates":[{"target_commitment_ref":"class_1",'
        '"action":"update_time","title":null,"starts_at":null,"ends_at":null,'
        '"delay_minutes":null,"class_arrival_lead_minutes":20}],"questions":[]}' % COMMITMENT_SCHEMA_VERSION
    )
    applied = apply_commitment_reconciliation(state, proposal, 0)
    assert applied.state.commitments[0].class_arrival_lead_minutes == 20
    assert applied.state.windows[0].ends_at == _dt(14, 40)
    # A later time-only feedback preserves the explicitly chosen 20 minutes.
    later = parse_commitment_reconciliation(
        '{"schema_version":"%s","updates":[{"target_commitment_ref":"class_1",'
        '"action":"update_time","title":null,"starts_at":"15:30","ends_at":null,'
        '"delay_minutes":null,"class_arrival_lead_minutes":null}],"questions":[]}' % COMMITMENT_SCHEMA_VERSION
    )
    applied2 = apply_commitment_reconciliation(applied.state, later, 0)
    assert applied2.state.commitments[0].class_arrival_lead_minutes == 20
    assert class_prep_interval(applied2.state.commitments[0]) == (_dt(15, 10), _dt(15, 30))


def test_feedback_lead_zero_removes_prep_and_invalid_value_is_rejected():
    state = _state(_class(20))
    zero = parse_commitment_reconciliation(
        '{"schema_version":"%s","updates":[{"target_commitment_ref":"class_1",'
        '"action":"update_time","title":null,"starts_at":null,"ends_at":null,'
        '"delay_minutes":null,"class_arrival_lead_minutes":0}],"questions":[]}' % COMMITMENT_SCHEMA_VERSION
    )
    assert class_prep_interval(apply_commitment_reconciliation(state, zero, 0).state.commitments[0]) is None
