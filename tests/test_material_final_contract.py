"""Recovery and adoption share the final estimate contract; no live requests."""
import json
from copy import deepcopy
from dataclasses import replace
import pytest
from src.material_inbox import extract_material,parse_extraction
from src.material_estimate_contract import complete_final_estimate,validate_final_estimate
from src.material_estimate_recovery import recover_result,minimal_confirmation_mode,confirm_minimal_task
from src.material_formal_validation import FormalValidationError,feedback
from src.material_estimate_diagnostics import capture_estimate_diagnostics
from tests.test_material_estimate_recovery import draft_for,action_payload,payload
from tests.test_material_owner_qualifier import FACTS,REQ,CLAIMS,GOOD


def recovered(extra=None):
    draft,_=draft_for('text')
    obj=action_payload()
    obj['estimate']['basis']=GOOD
    obj['estimate']['quantity_claims']=deepcopy(CLAIMS)
    obj['quantity_claims']=deepcopy(CLAIMS)  # misplaced known context echo
    if extra:obj.update(extra)
    raw=json.dumps(obj,ensure_ascii=False)
    result=recover_result([raw,raw],draft,set(),True)
    return draft,obj,raw,result.estimate_fallbacks[0]


def test_original_and_repair_fail_precisely_but_recovery_is_complete():
    draft,obj,raw,entry=recovered()
    with capture_estimate_diagnostics() as events:
        for _ in range(2):
            with pytest.raises(FormalValidationError) as exc:parse_extraction(raw,draft,set())
            assert feedback(exc.value)['field_path']=='$.quantity_claims'
            assert feedback(exc.value)['code']=='unknown_field'
        assert entry['confirmation_fields']==('details',)
        final=complete_final_estimate(entry,draft,set(),[raw,raw],FACTS,REQ)
        assert validate_final_estimate(final)==()
    assert final['confirmation_fields']==()
    assert all(c in final['quantity_claims'] for c in CLAIMS)
    assert final['estimate']==entry['estimate']
    assert final['work_id'] and final['item_id']==entry['item_id']
    assert not final['final_validation_errors']
    assert minimal_confirmation_mode(final)!='blocked'
    result=replace(draft,status='ready',estimate_fallbacks=(final,))
    adopted=confirm_minimal_task(result,final['item_id'],'实验报告',27,True,final['estimate']['short_scope'])
    assert adopted.items[0].user_edits['minutes']=='27'
    assert events[-1]['code']=='accepted'


def test_final_gate_ignores_old_details_but_not_changed_final_data():
    draft,obj,raw,entry=recovered()
    final=complete_final_estimate(entry,draft,set(),[raw],FACTS,REQ)
    stale=dict(final,confirmation_fields=['details'])
    assert minimal_confirmation_mode(stale)!='blocked'
    changed=deepcopy(stale);changed['estimate']['recommended_minutes']=900
    assert minimal_confirmation_mode(changed)=='blocked'
    changed=deepcopy(stale);changed['quantity_claims'][0]['minimum']=7
    assert minimal_confirmation_mode(changed)=='blocked'


@pytest.mark.parametrize('field',['deadline','location','existing_task','fixed_arrangement'])
def test_real_required_confirmation_survives_and_blocks(field):
    draft,obj,raw,entry=recovered()
    obj['actionability']['confirmation_required']=[field]
    final=complete_final_estimate(entry,draft,set(),[json.dumps(obj)],FACTS,REQ)
    assert validate_final_estimate(final)==(field,)
    assert final['confirmation_fields']==(field,)
    assert minimal_confirmation_mode(final)=='blocked'


@pytest.mark.parametrize('change',[{'items':None},{'items':'private raw text'}, {'private-secret-field':'private raw text'},
    {'reference_date':'2026-01-01','reference_evidence':'private raw text'}])
def test_unrecoverable_structure_does_not_become_user_details(change):
    draft,obj,raw,entry=recovered(change)
    with capture_estimate_diagnostics() as events:
        final=complete_final_estimate(entry,draft,set(),[raw],FACTS,REQ)
    assert final['final_validation_errors']
    assert 'details' not in final['confirmation_fields']
    assert minimal_confirmation_mode(final)=='blocked'
    encoded=json.dumps(events)
    assert 'private raw text' not in encoded
    if 'private-secret-field' in change:
        assert '$.private-secret-field' in encoded
    assert any(e.get('formal_validation',{}).get('field_path') for e in events)


def test_source_facts_rehydrated_without_model_guessing():
    draft,obj,raw,entry=recovered()
    entry.pop('quantity_claims')
    final=complete_final_estimate(entry,draft,set(),[raw],FACTS,REQ)
    assert all(c in final['quantity_claims'] for c in CLAIMS)
    assert validate_final_estimate(final)==()


def test_conflicting_claim_is_not_silently_replaced():
    draft,obj,raw,entry=recovered()
    entry['quantity_claims'][0].update(minimum=7,maximum=7)
    final=complete_final_estimate(entry,draft,set(),[raw],FACTS,REQ)
    assert final['final_validation_errors']
    assert minimal_confirmation_mode(final)=='blocked'


@pytest.mark.parametrize('mutation,code,path',[
    (lambda o:o.pop('reference_date'),'missing_field','$.reference_date'),
    (lambda o:o.update(items={}), 'wrong_type','$.items'),
    (lambda o:o['items'][0].update(kind='private-secret'), 'invalid_enum','$.items[0].kind'),
    (lambda o:o['items'][0].pop('scope'),'missing_field','$.items[0].scope'),
    (lambda o:o['items'][0].update(possible_task_ref='private-secret'),'unknown_task_ref','$.items[0].possible_task_ref'),
])
def test_parser_field_feedback_never_emits_values(mutation,code,path):
    draft,_=draft_for('text');obj=payload(False);mutation(obj)
    with pytest.raises(FormalValidationError) as exc:parse_extraction(json.dumps(obj),draft,set())
    safe=feedback(exc.value)
    assert safe['code']==code and safe['field_path']==path
    assert safe['expected'] and safe['observed']['type']
    assert 'private-secret' not in json.dumps(safe)


def test_pipeline_repair_feedback_and_adoptable_recovery():
    draft,obj,raw,entry=recovered()
    # No artificial PDF facts in this text fixture.
    obj['estimate'].pop('quantity_claims');obj['estimate']['basis']='填写字段并检查材料'
    obj.pop('quantity_claims');obj['source_title']='private source content'
    calls=[]
    def caller(system,user):
        calls.append(user)
        return json.dumps(obj)
    with capture_estimate_diagnostics() as events:
        result=extract_material(draft,caller)
    assert len(calls)==2
    assert 'formal_validation_feedback' in calls[1]
    assert '$.source_title' in calls[1]
    final=result.estimate_fallbacks[0]
    assert final['adoption_path']=='recovery'
    assert validate_final_estimate(final)==()
    assert minimal_confirmation_mode(final)!='blocked'
    assert final['confirmation_fields']==()
    failures=[e['formal_validation'] for e in events if 'formal_validation' in e]
    assert {e['attempt'] for e in failures}=={'initial','repair'}
    assert all(e['code']=='unknown_field' for e in failures)
    assert 'private source content' not in json.dumps(events)
