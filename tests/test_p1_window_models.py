from datetime import datetime

from src.p1_models import FieldEvidence, SourceKind
from src.p1_window_models import (AvailabilityLevel, CurrentContext, FixedCommitment, WindowConstraints, WindowContextDocument, WINDOW_SCHEMA_VERSION)


def test_nominal_boundary_and_arrival_deadline_are_separate():
    now = datetime(2026, 1, 1, 10, 5)
    current = CurrentContext(now, now, "31教学楼", {}, ())
    late = FixedCommitment("later", "课", "课", datetime(2026, 1, 1, 13, 30), None, "45楼", AvailabilityLevel.UNAVAILABLE, {}, ())
    early = FixedCommitment("early", "课", "课", datetime(2026, 1, 1, 11, 30), None, "46楼", AvailabilityLevel.UNAVAILABLE, {}, ())
    constraints = WindowConstraints(120, datetime(2026, 1, 1, 13), 10, {}, ())
    document = WindowContextDocument(WINDOW_SCHEMA_VERSION, "输入", current, (late, early), constraints, ())
    assert document.earliest_known_commitment.commitment_ref == "early"
    assert document.nominal_window_end == datetime(2026, 1, 1, 11, 30)
    assert document.arrival_deadline == datetime(2026, 1, 1, 11, 20)
    assert document.effective_time_constraint == datetime(2026, 1, 1, 11, 20)


def test_unknown_boundary_has_no_time_boundary():
    now = datetime(2026, 1, 1, 10)
    document = WindowContextDocument(WINDOW_SCHEMA_VERSION, "输入", CurrentContext(now, now, None, {}, ("current_location_text",)), (), WindowConstraints(None, None, 10, {"safety_buffer_minutes": FieldEvidence(SourceKind.SYSTEM_DEFAULT)}, ()), ())
    assert document.nominal_window_end is None
    assert not document.has_time_boundary


def test_unknown_start_keeps_earliest_known_but_does_not_confirm_next():
    now = datetime(2026, 1, 1, 10)
    current = CurrentContext(now, now, "31楼", {}, ())
    known = FixedCommitment("known", "课", "课", datetime(2026, 1, 1, 11, 30), None, "46楼", AvailabilityLevel.UNAVAILABLE, {}, ())
    unknown = FixedCommitment("unknown", "课", "课", None, None, "45楼", AvailabilityLevel.UNAVAILABLE, {}, ("starts_at",))
    document = WindowContextDocument(WINDOW_SCHEMA_VERSION, "输入", current, (known, unknown), WindowConstraints(None, None, 10, {}, ()), ())
    assert document.earliest_known_commitment is known
    assert document.has_unresolved_commitment_start
    assert not document.next_commitment_is_confirmed
    assert document.arrival_deadline is None
