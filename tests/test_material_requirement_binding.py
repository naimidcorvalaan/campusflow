import json
from copy import deepcopy
import pytest
from src.material_requirement_binding import bind_copy,bind_response,verified
from src.material_formal_validation import FormalValidationError,feedback,extract_material_object
from src.material_estimate_diagnostics import capture_estimate_diagnostics
from src.material_estimate_contract import complete_final_estimate,validate_final_estimate
from tests.test_material_owner_qualifier import FACTS,REQ,CLAIMS,SOURCE
from tests.test_material_final_contract import recovered


@pytest.mark.parametrize('wording',['约400字','至少400字','不少于400字','必须400字','不超过400字','400字以上'])
def test_known_requirement_copy_keeps_source_qualifier_and_minutes(wording):
    bad=deepcopy(CLAIMS);bad[2]['qualifier']='minimum'
    text,assumptions,claims=bind_copy('讨论'+wording+'；计算(95分钟)', ['正文7页'],bad,REQ)
    assert '讨论约400字' in text and '计算(95分钟)' in text
    assert assumptions==['正文6～8页']
    assert claims[2]['qualifier']=='approximately'
    assert bad[2]['qualifier']=='minimum'  # don't mutate input


@pytest.mark.parametrize('path',['normal','repair','recovery'])
def test_all_estimate_paths_bind_source_before_final_validation(path):
    draft,obj,raw,entry=recovered()
    obj['estimate']['basis']='讨论至少400字；正文6～8页'
    raw=bind_response(json.dumps(obj),REQ)
    assert json.loads(raw)['estimate']['basis']=='讨论约400字；正文6～8页'
    entry['estimate']['rationale']='讨论至少400字；正文6～8页'
    entry['quantity_claims'][2]['qualifier']='minimum'
    before=entry['estimate']['recommended_minutes']
    final=complete_final_estimate(entry,draft,set(),[raw]*(2 if path=='repair' else 1),FACTS,REQ)
    assert validate_final_estimate(final)==()
    assert final['estimate']['recommended_minutes']==before
    assert final['estimate']['rationale']=='讨论约400字；正文6～8页'
    assert any(c.get('owner')=='subtask_discussion' and c['qualifier']=='approximately' for c in final['quantity_claims'])
    assert not final['confirmation_fields']


def test_normal_workload_does_not_fallback_for_bad_requirement_copy():
    from src.material_workload import source_workload,estimate_workloads
    from tests.test_workload_engine import estimate_response
    calls=[]
    def caller(system,user):
        p=json.loads(user);calls.append(p)
        obj=json.loads(estimate_response(p,120))
        obj['estimates'][0].update(basis='工作(120分钟)，讨论至少400字',assumptions=[])
        return json.dumps(obj)
    result,_=estimate_workloads(source_workload(SOURCE),caller,material_facts=FACTS,material_requirements=REQ)
    assert len(calls)==1 and result[0][2]=='model_workload'
    assert result[0][1]['recommended_minutes']==120
    assert '讨论约400字' in result[0][1]['basis']


@pytest.mark.parametrize('mutation',[lambda r:r.update(qualifier='made_up'),lambda r:r.update(minimum=900,maximum=400),
    lambda r:r.pop('source'),lambda r:r.update(source_type='derived_estimate')])
def test_invalid_upstream_requirement_cannot_be_hidden(mutation):
    req=deepcopy(REQ);mutation(req[0])
    with pytest.raises(FormalValidationError) as exc:bind_copy('讨论至少400字',[],[],req)
    assert feedback(exc.value)['invariant_id']=='upstream_requirement_valid'
    assert feedback(exc.value)['involved_field_paths']==['$.material_requirements[0]']


def test_conflicting_upstream_requirements_remain_blocked():
    req=deepcopy(REQ);req.append(dict(req[0],minimum=10,maximum=12))
    with pytest.raises(FormalValidationError) as exc:verified(req)
    assert feedback(exc.value)['invariant_id']=='upstream_requirement_unique'


@pytest.mark.parametrize('raw,reason',[('', 'empty_response'),('private-body', 'object_boundary_missing'),
    ('{"secret":"private-body",broken}', 'invalid_json_syntax')])
def test_json_invariant_records_reason_without_content(raw,reason):
    with pytest.raises(FormalValidationError) as exc:extract_material_object(raw)
    safe=feedback(exc.value)
    assert safe['invariant_id']=='material_response_json_object'
    assert safe['involved_field_paths']==['$.response']
    assert safe['observed']['failure_reason']==reason
    assert 'private-body' not in json.dumps(safe)


def test_recovery_with_malformed_recognition_is_still_blocked_precisely():
    draft,obj,raw,entry=recovered()
    entry['origin']='model_workload'
    with capture_estimate_diagnostics() as events:
        final=complete_final_estimate(entry,draft,set(),['{"broken":'],FACTS,REQ)
    error=final['final_validation_errors'][0]
    assert error['invariant_id']=='material_response_json_object'
    assert not final.get('final_contract')
    event=next(e['formal_validation'] for e in events if 'formal_validation' in e)
    assert event['validation_stage']=='material_final_validation'
    assert event['involved_field_paths']==['$.response']


def test_final_seal_invariant_remains_enforced_and_diagnosable():
    draft,obj,raw,entry=recovered()
    final=complete_final_estimate(entry,draft,set(),[raw],FACTS,REQ)
    final['estimate']['rationale']='private-body'
    with pytest.raises(FormalValidationError) as exc:validate_final_estimate(final)
    safe=feedback(exc.value)
    assert safe['invariant_id']=='final_estimate_identity_and_seal'
    assert '$.final_contract_seal' in safe['involved_field_paths']
    assert safe['observed']['seal_matches'] is False
    assert 'private-body' not in json.dumps(safe)


def test_unknown_requirement_owner_is_not_canonicalized_away():
    from src.material_requirements import evaluate_estimate_facts
    text,assumptions,claims=bind_copy('报告至少400字',[],[],REQ)
    assert text=='报告至少400字'
    assert evaluate_estimate_facts(text,assumptions,FACTS,claims,REQ)[0]
