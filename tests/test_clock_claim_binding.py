import json
from types import SimpleNamespace

import pytest

from src.p2_agentic_parser import AgenticParseError
from src.p5_agent_runtime import AgentCallTrace, AgentCallRecord, MAX_AGENT_CALLS_PER_TRACE
from src.p5_copy_guard import GroundedNarrative
from src.p5_clock_claims import invalid_clock_fields, bind_clock_claims

CATALOG = [dict(ref='depart',value='2026-09-19T18:05:00'),
           dict(ref='arrive',value='2026-09-19T18:08:00')]
REFERENCE = '2026-09-19T14:00:00'


@pytest.mark.parametrize('copy,ref,time,relation,invalid', [
    ('Leave at18:08.', 'depart','18:08','at',True),
    ('Arrive at18:08.', 'arrive','18:08','at',False),
    ('18:08に出発。', 'depart','18:08','at',True),
    ('18:08に到着。', 'arrive','18:08','at',False),
    ('Arrive by18:10.', 'arrive','18:10','not_after',False),
    ('Arrive before18:00.', 'arrive','18:00','not_after',True),
    ('Leave after18:00.', 'depart','18:00','not_before',False),
    ('Something at18:08.', None,'18:08','at',True),
])
def test_role_is_supplied_by_model_not_reinterpreted_from_words(copy, ref, time, relation, invalid):
    payload = dict(claims=[dict(field='closing',quote=copy,time=time,ref=ref,relation=relation)])
    result = invalid_clock_fields(json.dumps(payload),dict(closing=copy),CATALOG,REFERENCE)
    assert result == ({'closing'} if invalid else set())


def test_omitted_clock_claim_cannot_make_field_pass():
    assert invalid_clock_fields('{"claims":[]}',dict(closing='Leave18:08.'),CATALOG,REFERENCE) == {'closing'}


def test_unknown_ref_is_not_accepted_as_a_new_fact():
    payload = dict(claims=[dict(field='closing',quote='Leave18:08.',time='18:08',ref='invented',relation='at')])
    with pytest.raises(AgenticParseError):
        invalid_clock_fields(json.dumps(payload),dict(closing='Leave18:08.'),CATALOG,REFERENCE)


def test_binding_hides_actual_clock_values_and_repairs_only_bad_field():
    context = SimpleNamespace(current_time=REFERENCE,active_tasks=(),selected_plan=(),available_windows=(),
        fixed_commitments=(),movements=(SimpleNamespace(origin_name='A',destination_name='B',
            preparation_starts_at=None,starts_at=CATALOG[0]['value'],ends_at=CATALOG[1]['value']),))
    narrative = GroundedNarrative('Ready.', 'The venue changed.', 'Leave at18:08.')
    fallback = GroundedNarrative('Ready.',None,'Follow the timeline.',generated=False)
    def caller(system,user):
        payload = json.loads(user)
        assert all('value' not in row for row in payload['catalog'])
        assert '18:05' not in user
        return json.dumps(dict(claims=[dict(field='closing',quote='Leave at18:08.',time='18:08',
            ref='movement:0:departure',relation='at')]))
    result,trace = bind_clock_claims(context,narrative,fallback,caller,None)
    assert result.opening == narrative.opening and result.why_this_plan == narrative.why_this_plan
    assert result.closing == fallback.closing and not result.generated
    assert trace.call_count == 1


def test_exhausted_existing_budget_keeps_plan_and_qualitative_copy_without_extra_call():
    context = SimpleNamespace(current_time=REFERENCE,active_tasks=(),selected_plan=(),available_windows=(),
        fixed_commitments=(),movements=())
    narrative = GroundedNarrative('Ready.', 'Venue changed.', 'Leave18:08.')
    fallback = GroundedNarrative('Ready.',None,'Follow timeline.',generated=False)
    trace = AgentCallTrace(tuple(AgentCallRecord('used','used',True) for _ in range(MAX_AGENT_CALLS_PER_TRACE)))
    result,updated = bind_clock_claims(context,narrative,fallback,lambda *_: pytest.fail('over budget'),trace)
    assert updated.call_count == MAX_AGENT_CALLS_PER_TRACE
    assert result.closing == fallback.closing and result.why_this_plan == narrative.why_this_plan
