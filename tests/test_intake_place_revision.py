"""A semantic audit cannot confuse a task's venue with its departure point."""
import json
from dataclasses import replace
from datetime import datetime

import pytest

from src.p2_day_intake import parse_day_intake
from src.p4_intake_auditor import audit_initial_intake, _proposal_payload


def proposal(place):
    return parse_day_intake(json.dumps(dict(schema_version='p2.day-intake.v1',
        day_end='24:00', commitments=[], questions=[], current_location='Dorm A',
        transport_mode=None, tasks=[dict(title='Deliver documents', total_minutes=10,
            location_text=place, activity_kind='generic', duration_source='user_explicit',
            is_splittable=False, minimum_slice_minutes=10)])))


@pytest.mark.parametrize('old,new,source,accepted', [
    ('Building B', 'Dorm A', 'Building B', False),
    ('Dorm A', 'Building B', 'Building B', True),
    ('Building B', None, 'Building B', False),
])
def test_independent_source_answer_controls_only_audit_acceptance(old, new, source, accepted):
    original = proposal(old)
    revised = proposal(new)
    text = 'I am at Dorm A. Deliver documents at Building B.'
    calls = []
    def caller(system, user):
        calls.append((system, user))
        if len(calls) == 1:
            return json.dumps(dict(schema_version='p4.initial-intake-audit.v1', decision='repair',
                issues=['incorrect place'], repaired_proposal=_proposal_payload(revised)))
        question = json.loads(user)
        assert question == dict(user=text, questions=[dict(ref='tasks:0',
            subject='Deliver documents', role='execution_location')])
        return json.dumps(dict(answers=[dict(ref='tasks:0', location=source,
            evidence='Deliver documents at Building B.')]))
    result = audit_initial_intake(datetime(2026,9,19,14), text, original, caller)
    assert result.repaired is accepted
    assert result.proposal == (revised if accepted else original)
    assert result.call_count == len(calls) == 2


@pytest.mark.parametrize('answer', [
    dict(ref='tasks:0', location='Dorm A', evidence='invented quote'),
    dict(ref='wrong', location='Dorm A', evidence='Dorm A'),
    dict(ref='tasks:0', location={'name':'Dorm A'}, evidence='Dorm A'),
    dict(ref='tasks:0', location='Dorm A', evidence='Dorm A', extra=True),
])
def test_invalid_source_answer_never_replaces_existing_place(answer):
    original, revised = proposal('Building B'), proposal('Dorm A')
    responses = iter([json.dumps(dict(schema_version='p4.initial-intake-audit.v1',
        decision='repair', issues=['place'], repaired_proposal=_proposal_payload(revised))),
        json.dumps(dict(answers=[answer]))])
    result = audit_initial_intake(datetime(2026,9,19,14),
        'At Dorm A; deliver at Building B.', original, lambda *_: next(responses))
    assert result.proposal == original and result.audit_failed
    assert result.call_count == 2


def test_non_location_audit_does_not_add_another_call():
    original = proposal('Building B')
    revised = replace(original, day_end='23:00')
    result = audit_initial_intake(datetime(2026,9,19,14), 'Finish by23:00.', original,
        lambda *_: json.dumps(dict(schema_version='p4.initial-intake-audit.v1',
            decision='repair', issues=['day end'], repaired_proposal=_proposal_payload(revised))))
    assert result.proposal == revised and result.call_count == 1


@pytest.mark.parametrize('malformed', [False, True])
def test_unverified_place_change_does_not_restore_an_invented_deadline(malformed):
    from src.p2_day_intake import IntakeCommitment
    original = proposal('Building B')
    original = replace(original,
        commitments=(IntakeCommitment('Class', starts_at='16:00', ends_at='18:00'),),
        tasks=(replace(original.tasks[0], before_commitment_index=1),))
    revised = replace(original, tasks=(replace(original.tasks[0],
        before_commitment_index=None, location_text=None),))
    responses = iter([json.dumps(dict(schema_version='p4.initial-intake-audit.v1',
        decision='repair', issues=['invented deadline', 'place'], repaired_proposal=_proposal_payload(revised))),
        'invalid JSON' if malformed else json.dumps(dict(answers=[dict(ref='tasks:0',
            location='Building B', evidence='Do it at Building B.')]))])
    result = audit_initial_intake(datetime(2026,9,19,14),
        'Do it at Building B. Class16-18; no deadline for the task.', original, lambda *_: next(responses))
    assert result.proposal.tasks[0].before_commitment_index is None
    assert result.proposal.tasks[0].location_text == 'Building B'
    assert result.repaired and not result.audit_failed and result.call_count == 2
