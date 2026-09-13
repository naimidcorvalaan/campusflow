from datetime import datetime

import pytest

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_agentic_parser import AgenticParseError
from src.p2_commitment_reconciler import (
    COMMITMENT_SCHEMA_VERSION,
    CommitmentAction,
    CommitmentUpdate,
    apply_commitment_reconciliation,
    build_commitment_reconciliation_prompt,
    parse_commitment_reconciliation,
)
from src.p2_models import DayPlanningState, TaskProgress, TaskState
from src.p2_window_derivation import derive_day_state


def dt(h, m=0):
    return datetime(2026, 9, 1, h, m)


def make_commitment(ref, title, start, end, availability=AvailabilityLevel.UNAVAILABLE):
    return FixedCommitment(ref, title, title, start, end, None, availability, {}, ())


def make_task(
    ref="day_task_001",
    title="计组实验3",
    total=120,
    completed=50,
    state=TaskState.ACTIVE,
    splittable=True,
    min_slice=30,
):
    return TaskProgress(ref, title, total, completed, SourceKind.AI_ESTIMATED, state, splittable, min_slice)


def build_state(now=None, commitments=None, tasks=None):
    now = now or dt(9)
    commitments = commitments if commitments is not None else (
        make_commitment("day_commitment_001", "上课", dt(10), dt(11, 30)),
        make_commitment("day_commitment_002", "实验", dt(14), dt(15, 30)),
    )
    tasks = tasks if tasks is not None else (make_task(),)
    return derive_day_state(now, dt(22), commitments, tasks, 10, {}, (), now)


def make_result(*updates, questions=()):
    return parse_commitment_reconciliation(
        '{"schema_version": "p2.commitment-reconciliation.v1", "updates": ['
        + ", ".join(updates)
        + "], \"questions\": " + _json_list(questions) + "}"
    )


def _json_list(items):
    return "[" + ", ".join('"{}"'.format(item) for item in items) + "]"


def update_json(**fields):
    base = {
        "target_commitment_ref": None,
        "action": "add",
        "title": None,
        "starts_at": None,
        "ends_at": None,
        "delay_minutes": None,
    }
    base.update(fields)
    return "{" + ", ".join(
        '"{}": {}'.format(key, _json_value(value)) for key, value in base.items()
    ) + "}"


def _json_value(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return '"{}"'.format(value)


def test_parse_delay():
    text = '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
        target_commitment_ref="day_commitment_001", action="delay", delay_minutes=20
    ) + '], "questions": []}'
    result = parse_commitment_reconciliation(text)
    update = result.updates[0]
    assert update.action == CommitmentAction.DELAY
    assert update.target_commitment_ref == "day_commitment_001"
    assert update.delay_minutes == 20


def test_parse_add():
    text = '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
        action="add", title="组会", starts_at="14:00", ends_at="15:00"
    ) + '], "questions": []}'
    result = parse_commitment_reconciliation(text)
    update = result.updates[0]
    assert update.action == CommitmentAction.ADD
    assert update.title == "组会"
    assert update.starts_at == "14:00"
    assert update.ends_at == "15:00"


def test_parse_cancel_and_update_time():
    cancel = '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
        target_commitment_ref="day_commitment_003", action="cancel"
    ) + '], "questions": []}'
    update_time = '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
        target_commitment_ref="day_commitment_003", action="update_time", starts_at="15:00"
    ) + '], "questions": []}'
    assert parse_commitment_reconciliation(cancel).updates[0].action == CommitmentAction.CANCEL
    assert parse_commitment_reconciliation(update_time).updates[0].starts_at == "15:00"


def test_parse_rejects_duplicate_targets():
    text = '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
        target_commitment_ref="day_commitment_001", action="delay", delay_minutes=20
    ) + ", " + update_json(
        target_commitment_ref="day_commitment_001", action="cancel"
    ) + '], "questions": []}'
    with pytest.raises(AgenticParseError):
        parse_commitment_reconciliation(text)


def test_parse_rejects_bad_action():
    text = '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
        target_commitment_ref="day_commitment_001", action="pause"
    ) + '], "questions": []}'
    with pytest.raises(AgenticParseError):
        parse_commitment_reconciliation(text)


def test_parse_rejects_bad_time_format():
    text = '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
        action="add", title="组会", starts_at="14:99", ends_at="15:00"
    ) + '], "questions": []}'
    with pytest.raises(AgenticParseError):
        parse_commitment_reconciliation(text)


def test_parse_rejects_add_without_title():
    text = '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
        action="add", starts_at="14:00", ends_at="15:00"
    ) + '], "questions": []}'
    with pytest.raises(AgenticParseError):
        parse_commitment_reconciliation(text)


def test_parse_rejects_update_time_without_time():
    text = '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
        target_commitment_ref="day_commitment_003", action="update_time"
    ) + '], "questions": []}'
    with pytest.raises(AgenticParseError):
        parse_commitment_reconciliation(text)


def test_update_model_validations():
    with pytest.raises(ValueError):
        CommitmentUpdate("day_commitment_001", CommitmentAction.ADD, "组会", "14:00", "15:00", None)
    with pytest.raises(ValueError):
        CommitmentUpdate("day_commitment_001", CommitmentAction.DELAY, None, None, None, None)
    with pytest.raises(ValueError):
        CommitmentUpdate(None, CommitmentAction.CANCEL, None, None, None, None)
    with pytest.raises(ValueError):
        CommitmentUpdate("day_commitment_001", CommitmentAction.DELAY, None, None, None, True)


def test_delay_applies_and_rederives_windows():
    state = build_state()
    result = parse_commitment_reconciliation(
        '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
            target_commitment_ref="day_commitment_001", action="delay", delay_minutes=20
        ) + '], "questions": []}'
    )
    applied = apply_commitment_reconciliation(state, result)
    commitments = {c.commitment_ref: c for c in applied.state.commitments}
    assert commitments["day_commitment_001"].ends_at == dt(11, 50)
    assert any(w.starts_at == dt(11, 50) for w in applied.state.windows)
    # task ledger / progress 保留
    assert applied.state.tasks == state.tasks
    assert applied.state.tasks[0].completed_minutes == 50


def test_delay_unknown_ends_rejected():
    commitments = (
        make_commitment("day_commitment_001", "上课", dt(10), None),
        make_commitment("day_commitment_002", "实验", dt(14), dt(15, 30)),
    )
    state = build_state(commitments=commitments)
    result = parse_commitment_reconciliation(
        '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
            target_commitment_ref="day_commitment_001", action="delay", delay_minutes=20
        ) + '], "questions": []}'
    )
    applied = apply_commitment_reconciliation(state, result)
    assert applied.warnings
    assert applied.state.commitments == state.commitments


def test_delay_past_commitment_rejected():
    commitments = (
        make_commitment("day_commitment_001", "上课", dt(8), dt(9, 30)),
        make_commitment("day_commitment_002", "实验", dt(14), dt(15, 30)),
    )
    state = build_state(now=dt(10), commitments=commitments)
    result = parse_commitment_reconciliation(
        '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
            target_commitment_ref="day_commitment_001", action="delay", delay_minutes=20
        ) + '], "questions": []}'
    )
    applied = apply_commitment_reconciliation(state, result)
    assert applied.warnings
    assert applied.state.commitments == state.commitments


def test_add_creates_stable_ref_and_does_not_cross():
    state = build_state()
    result = parse_commitment_reconciliation(
        '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
            action="add", title="组会", starts_at="14:00", ends_at="15:00"
        ) + '], "questions": []}'
    )
    applied = apply_commitment_reconciliation(state, result)
    refs = [c.commitment_ref for c in applied.state.commitments]
    assert refs == ["day_commitment_001", "day_commitment_002", "day_commitment_003"]
    assert applied.new_commitment_refs == ("day_commitment_003",)
    for w in applied.state.windows:
        assert not (w.starts_at < dt(14, 30) < w.ends_at)


def test_cancel_frees_time_into_windows():
    commitments = (
        make_commitment("day_commitment_001", "上课", dt(10), dt(11, 30)),
        make_commitment("day_commitment_002", "组会", dt(14), dt(15)),
        make_commitment("day_commitment_003", "实验", dt(15, 30), dt(17)),
    )
    state = build_state(commitments=commitments)
    result = parse_commitment_reconciliation(
        '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
            target_commitment_ref="day_commitment_002", action="cancel"
        ) + '], "questions": []}'
    )
    applied = apply_commitment_reconciliation(state, result)
    refs = [c.commitment_ref for c in applied.state.commitments]
    assert "day_commitment_002" not in refs
    assert any(w.starts_at < dt(14, 30) < w.ends_at for w in applied.state.windows)


def test_update_time():
    state = build_state()
    result = parse_commitment_reconciliation(
        '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
            target_commitment_ref="day_commitment_002", action="update_time",
            starts_at="13:30", ends_at="15:00",
        ) + '], "questions": []}'
    )
    applied = apply_commitment_reconciliation(state, result)
    commitments = {c.commitment_ref: c for c in applied.state.commitments}
    assert commitments["day_commitment_002"].starts_at == dt(13, 30)
    assert commitments["day_commitment_002"].ends_at == dt(15, 0)


def test_update_time_partial_commitment_no_crash_end_stays_none():
    # P3e-final 真实验收崩溃回归：partial commitment（ends_at=None）
    # 修改开始时间时不得对 None 做 timedelta 运算。
    now = dt(13, 35)
    commitments = (
        make_commitment("day_commitment_001", "上课", dt(15), None),
    )
    state = build_state(now=now, commitments=commitments)
    result = parse_commitment_reconciliation(
        '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
            target_commitment_ref="day_commitment_001", action="update_time",
            starts_at="15:30", ends_at=None,
        ) + '], "questions": []}'
    )
    applied = apply_commitment_reconciliation(state, result)
    commitment = {c.commitment_ref: c for c in applied.state.commitments}["day_commitment_001"]
    assert commitment.starts_at == dt(15, 30)
    assert commitment.ends_at is None
    assert any("改时间" in entry for entry in applied.applied_entries)


def test_update_time_single_start_shifts_preserving_duration():
    state = build_state()
    result = parse_commitment_reconciliation(
        '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
            target_commitment_ref="day_commitment_002", action="update_time",
            starts_at="15:00", ends_at=None,
        ) + '], "questions": []}'
    )
    applied = apply_commitment_reconciliation(state, result)
    commitments = {c.commitment_ref: c for c in applied.state.commitments}
    assert commitments["day_commitment_002"].starts_at == dt(15, 0)
    assert commitments["day_commitment_002"].ends_at == dt(16, 30)
    assert any("改时间" in entry for entry in applied.applied_entries)


def test_update_time_invalid_order_rejected():
    state = build_state()
    result = parse_commitment_reconciliation(
        '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
            target_commitment_ref="day_commitment_002", action="update_time",
            starts_at="16:00", ends_at="15:00",
        ) + '], "questions": []}'
    )
    applied = apply_commitment_reconciliation(state, result)
    assert applied.warnings
    assert applied.state.commitments == state.commitments


def test_unknown_target_warns():
    state = build_state()
    result = parse_commitment_reconciliation(
        '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
            target_commitment_ref="day_commitment_999", action="cancel"
        ) + '], "questions": []}'
    )
    applied = apply_commitment_reconciliation(state, result)
    assert applied.warnings
    assert applied.state.commitments == state.commitments


def test_questions_suppress_updates():
    state = build_state()
    result = parse_commitment_reconciliation(
        '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
            target_commitment_ref="day_commitment_001", action="cancel"
        ) + '], "questions": ["你说的是哪个组会？"]}'
    )
    applied = apply_commitment_reconciliation(state, result)
    assert applied.questions == ("你说的是哪个组会？",)
    assert applied.state.commitments == state.commitments
    assert applied.applied_entries == ()


def test_input_state_immutable():
    state = build_state()
    before = state
    result = parse_commitment_reconciliation(
        '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [' + update_json(
            target_commitment_ref="day_commitment_001", action="delay", delay_minutes=20
        ) + '], "questions": []}'
    )
    apply_commitment_reconciliation(state, result)
    assert state is before
    assert state.commitments[0].ends_at == dt(11, 30)


def test_prompt_contains_examples_and_ledger():
    state = build_state()
    system, user = build_commitment_reconciliation_prompt(state, "下课晚了20分钟。")
    for marker in ("下课晚了20分钟", "14点临时有个组会", "下午的组会取消了", "组会改到15点"):
        assert marker in system
    assert "day_commitment_001" in user
    assert "上课" in user
    assert "FixedCommitment(" not in user
    assert "object at" not in user
