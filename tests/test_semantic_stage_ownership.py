"""Semantic candidates can be corrected; confirmed facts cannot be erased."""
import json
import pytest
from datetime import datetime

from src.p2_day_intake import DayIntakeProposal, IntakeTask, IntakeCommitment, INTAKE_SCHEMA_VERSION
from src.p4_intake_auditor import audit_initial_intake, _proposal_payload


@pytest.mark.parametrize('wording', [
    'Class from 16:00 until 18:00. I need ninety minutes of programming.',
    '16時から18時まで授業。プログラミングを90分やる。',
    '下午四点至六点有课，还有90分钟编程练习。',
])
def test_reviewed_structured_facts_do_not_need_chinese_keyword_reapproval(wording):
    from src.p2_day_intake import apply_day_intake
    proposal = DayIntakeProposal(INTAKE_SCHEMA_VERSION,
        (IntakeCommitment('Class', starts_at='16:00', ends_at='18:00', commitment_kind='class'),),
        (IntakeTask('Programming', 90, duration_source='user_explicit'),), None, ())
    result = apply_day_intake(datetime(2026, 9, 19, 14), proposal,
        user_text=wording, semantically_reviewed=True)
    assert result.state.commitments[0].ends_at.hour == 18
    assert result.state.tasks[0].total_minutes == 90
    assert not result.questions


def test_reviewed_semantics_still_rejects_invalid_clock_order():
    from src.p2_day_intake import apply_day_intake
    proposal = DayIntakeProposal(INTAKE_SCHEMA_VERSION,
        (IntakeCommitment('Class', starts_at='18:00', ends_at='16:00', commitment_kind='class'),),
        (), None, ())
    result = apply_day_intake(datetime(2026, 9, 19, 14), proposal,
        user_text='Class', semantically_reviewed=True)
    assert result.questions and not result.state.commitments


def test_audit_envelope_supplies_only_its_fixed_nested_protocol_version():
    candidate = DayIntakeProposal(INTAKE_SCHEMA_VERSION, (),
        (IntakeTask('Review', 20, latest_end_time='15:20'),), None, ())
    fixed = _proposal_payload(candidate)
    fixed.pop('schema_version')
    fixed['tasks'][0]['latest_end_time'] = None
    response = dict(schema_version='p4.initial-intake-audit.v1', decision='repair',
        issues=['The deadline belongs to another activity'], repaired_proposal=fixed)
    outcome = audit_initial_intake(datetime(2026,9,19,14), 'Review for 20 minutes.',
        candidate, lambda *_: json.dumps(response))
    assert outcome.repaired and not outcome.audit_failed
    assert outcome.proposal.tasks[0].latest_end_time is None
    assert fixed.get('schema_version') is None  # caller object is not mutated


def test_nested_audit_adapter_does_not_default_missing_business_fields_or_wrong_version():
    candidate = DayIntakeProposal(INTAKE_SCHEMA_VERSION, (), (), None, ())
    for invalid in ({}, dict(_proposal_payload(candidate), schema_version='wrong')):
        response = dict(schema_version='p4.initial-intake-audit.v1', decision='repair',
            issues=['repair'], repaired_proposal=invalid)
        outcome = audit_initial_intake(datetime(2026,9,19,14), 'Review.', candidate,
            lambda *_: json.dumps(response))
        assert outcome.audit_failed and not outcome.repaired


def test_unconfirmed_before_relation_can_be_removed_by_semantic_audit():
    candidate = DayIntakeProposal(
        INTAKE_SCHEMA_VERSION,
        day_end=None, questions=(),
        commitments=(IntakeCommitment('Class', starts_at='16:00', ends_at='18:00'),),
        tasks=(IntakeTask('Study', 90, before_commitment_index=1),),
    )
    corrected = _proposal_payload(candidate)
    corrected['tasks'][0]['before_commitment_index'] = None
    answer = json.dumps(dict(schema_version='p4.initial-intake-audit.v1',
        decision='repair', issues=['No user requirement to finish before class'],
        repaired_proposal=corrected))
    result = audit_initial_intake(datetime(2026, 9, 19, 14),
        'Class from 16 to 18. I also need 90 minutes of study today.', candidate,
        lambda *args: answer)
    assert result.repaired and not result.audit_failed
    assert result.proposal.tasks[0].before_commitment_index is None
    assert result.proposal.commitments == candidate.commitments


def test_mixed_feedback_is_ref_checked_transfer_not_incomplete_operation():
    from tests.test_p4_feedback_decision import _state, _payload
    from src.p4_feedback_decision import decide_feedback
    calls = []
    def caller(*args):
        calls.append(args)
        assert len(calls) == 1, 'routing must not run a second operation critic'
        return json.dumps(_payload(intent_type='mixed',
            preferred_next_task_ref=None,
            cancelled_task_refs=['day_task_003'], target_task_refs=['day_task_003']))
    result = decide_feedback(_state(), None, 'Cancel laundry and add shopping for 10 minutes', '', caller)
    assert result.decision.intent_type == 'mixed'
    assert result.decision.cancelled_task_refs == ('day_task_003',)
    assert result.critic_calls == 0 and not result.used_fallback


def test_unresolved_task_place_is_preserved_and_requires_bound_answer():
    from tests.test_p4_execution_enrichment import enrich
    from src.p4_execution_context import ExecutionConfirmationKind
    from src.p4_execution_presentation import execution_confirmation_questions
    _, applied, context = enrich([IntakeTask('Work', 30, location_text='unlisted residence')])
    binding = context.binding_for(applied.new_task_refs[0])
    assert binding.raw_location_text == 'unlisted residence'
    assert binding.execution_location is None
    assert context.confirmations[0].kind is ExecutionConfirmationKind.TASK_LOCATION_REQUIRED
    assert 'unlisted residence' in execution_confirmation_questions(context)[0]


def test_location_model_receives_context_without_changing_exact_alias_resolution():
    from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
    from src.p3_location_resolver import resolve_location
    campus = DEFAULT_CAMPUS_REGISTRY.get_campus_map('beiyangyuan')
    seen = []
    def caller(system, user):
        seen.append(user)
        return json.dumps(dict(schema_version='p3.location-resolution.v1', status='resolved',
            matched_node_id='chengyuan_7zhai', display_name=None, question=None))
    result = resolve_location(campus, 'my residence', caller,
        semantic_context='User confirmed residence is chengyuan_7zhai; task is there after class.')
    assert result.node_id == 'chengyuan_7zhai' and 'User confirmed residence' in seen[0]
    exact = resolve_location(campus, '诚园7斋', caller, semantic_context='irrelevant')
    assert exact.node_id == result.node_id and len(seen) == 1


def test_bad_relationship_graph_does_not_erase_valid_task_deadline():
    from tests.test_p4_event_semantics import _raw, _event, _graph
    from src.p2_day_intake import run_day_intake
    raw = _raw([_event('delivery', 'task', 'Deliver', 1, location='31教学楼')])
    raw['events'][0]['ends_at'] = '15:20'
    invalid = _graph()
    invalid['commitment_relations'] = [dict(event_id='delivery', commitment_event_id='delivery', relation='before')]
    def no_reextract(*args):
        raise AssertionError('valid raw facts must not be discarded for whole-utterance re-extraction')
    outcome = run_day_intake(datetime(2026, 9, 19, 14), 'Deliver by 15:20', no_reextract,
        raw_event_caller=lambda *a: json.dumps(raw),
        semantic_linker_caller=lambda *a: json.dumps(invalid))
    assert outcome.applied is not None
    assert not outcome.proposal.commitments
    assert outcome.proposal.tasks[0].latest_end_time == '15:20'


def test_venue_revision_can_refresh_derived_title_without_clock_or_identity_change():
    from dataclasses import replace
    from tests.test_product_semantic_regressions import venue_update
    from tests.test_p2_commitment_reconciler import build_state
    from src.p2_commitment_reconciler import apply_commitment_reconciliation
    state = build_state()
    state = replace(state, commitments=(replace(state.commitments[0], title='31教学楼上课'),) + state.commitments[1:])
    result = apply_commitment_reconciliation(state, venue_update(title='上课')).state
    assert result.commitments[0].title == '上课'
    assert result.commitments[0].location_text == '46教学楼'
    assert replace(result.commitments[0], title=state.commitments[0].title,
                   location_text=state.commitments[0].location_text) == state.commitments[0]
    assert result.tasks == state.tasks


def test_stale_title_venue_copy_cannot_override_structured_update():
    from dataclasses import replace
    from tests.test_product_semantic_regressions import venue_update
    from tests.test_p2_commitment_reconciler import build_state
    from src.p2_commitment_reconciler import apply_commitment_reconciliation
    for old_place, new_place, title, expected in (
        ('31教学楼', '46教学楼', '31教学楼有课', '有课'),
        ('Old Hall', 'New Hall', 'Seminar @ Old Hall', 'Seminar'),
        ('旧講堂', '新講堂', '講義 · 旧講堂', '講義'),
    ):
        state = build_state()
        state = replace(state, commitments=(replace(state.commitments[0],
            title=title, location_text=old_place),) + state.commitments[1:])
        result = apply_commitment_reconciliation(state, venue_update(title=title, location_text=new_place)).state
        assert result.commitments[0].title == expected
        assert result.commitments[0].location_text == new_place
        assert result.commitments[0].commitment_ref == state.commitments[0].commitment_ref


def test_task_place_confirmation_verifies_only_that_field_changed():
    from dataclasses import replace
    from types import SimpleNamespace
    import pytest
    from tests.test_p4_execution_enrichment import enrich
    from src.p3_location_resolver import resolve_location
    from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
    from src.p4_execution_context import ExecutionLocation, ExecutionLocationSource
    from src.planning_confirmation_ui import PlanningConfirmation, verify_answer_result
    _, applied, context = enrich([IntakeTask('Work', 30, location_text='unresolved')])
    ref = applied.new_task_refs[0]
    resolution = resolve_location(DEFAULT_CAMPUS_REGISTRY.get_campus_map('weijinlu'), '9教')
    location = ExecutionLocation.from_resolution(resolution, ExecutionLocationSource.EXPLICIT_TASK_LOCATION)
    updated = context.upsert(replace(context.binding_for(ref), execution_location=location))
    before = SimpleNamespace(state=applied.state, execution_context=context)
    after = SimpleNamespace(state=applied.state, execution_context=updated)
    question = PlanningConfirmation('token', 'question', 'task_location', None, '', task_ref=ref)
    verify_answer_result(question, before, after)
    with pytest.raises(ValueError):
        verify_answer_result(question, before, SimpleNamespace(state=replace(applied.state, tasks=()),
                                                             execution_context=updated))


def test_same_round_effort_revision_can_correct_only_tentative_estimates():
    from tests.test_p2_agentic_pipeline import (build_state, make_task, window, dt,
        PLAN_INTENT_ESTIMATE, PLAN_INTENT_ESTIMATE_180, REVIEW_ACCEPT)
    from src.p2_agentic_pipeline import run_p2_agentic_day_planning
    state = build_state((make_task(total=None),), (window('w', dt(9), dt(18), 540),))
    review = json.loads(REVIEW_ACCEPT)
    review.update(decision='revise', reason='Effort scope includes unrelated work')
    calls = []
    def caller(system, user):
        calls.append((system, user))
        return (PLAN_INTENT_ESTIMATE_180, json.dumps(review), PLAN_INTENT_ESTIMATE)[len(calls)-1]
    result = run_p2_agentic_day_planning(state, 'Do the task', caller,
        skip_reconciliation=True, skip_legacy_review=True)
    assert result.updated_state.tasks[0].total_minutes == 100
    assert state.tasks[0].total_minutes is None
    assert result.revision_used and len(calls) == 3


def test_revision_does_not_reset_prior_round_or_explicit_effort():
    from dataclasses import replace
    from src.p1_models import SourceKind
    from src.p2_agentic_pipeline import _restore_round_estimate_slots
    from tests.test_p2_agentic_pipeline import build_state, make_task, window, dt
    for source in (SourceKind.USER_STATED, SourceKind.AI_ESTIMATED):
        state = build_state((make_task(total=60, completed=20, source=source),),
            (window('w', dt(9), dt(18), 540),))
        assert _restore_round_estimate_slots(state, state) == state
    unknown = replace(state, tasks=(replace(state.tasks[0], total_minutes=None),))
    tentative = replace(unknown, tasks=(replace(unknown.tasks[0], total_minutes=100),))
    assert _restore_round_estimate_slots(tentative, unknown, set()) == tentative
    restored = _restore_round_estimate_slots(tentative, unknown)
    assert restored.tasks[0].total_minutes is None
    assert restored.tasks[0].completed_minutes == 20


def test_binding_update_preserves_existing_order_and_other_bindings():
    from dataclasses import replace
    from tests.test_p4_execution_enrichment import enrich
    _, applied, context = enrich([IntakeTask('First', 20, location_text='9教'),
                                 IntakeTask('Second', 30, location_text='9教')])
    first = context.binding_for(applied.new_task_refs[0])
    updated = context.upsert(replace(first, raw_location_text='resolved later'))
    assert [b.task_ref for b in updated.bindings] == [b.task_ref for b in context.bindings]
    assert updated.bindings[1:] == context.bindings[1:]
