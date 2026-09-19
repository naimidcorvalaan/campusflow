"""One fact authority, strict estimate output, bounded copy fidelity."""
import json
from copy import deepcopy
import pytest
from src.material_output_schema import (MATERIAL_REQUIRED,MATERIAL_OPTIONAL,ESTIMATE_FIELDS,
    WORKLOAD_ESTIMATE_FIELDS,render_source_facts,authoritative_claims)
from src.material_inbox import parse_extraction,extract_material,update_source,MaterialInbox
from src.material_formal_validation import FormalValidationError,fields,feedback
from src.material_estimate_diagnostics import capture_estimate_diagnostics
from src.material_estimate_contract import validate_final_estimate
from src.material_estimate_recovery import minimal_confirmation_mode
from src.material_requirements import evaluate_estimate_facts
from src.material_workload import estimate_workloads
from tests.test_material_owner_qualifier import FACTS,REQ,CLAIMS,GOOD
from tests.test_material_estimate_recovery import draft_for,action_payload
from tests.test_material_quantity_facts import work
from tests.test_workload_engine import estimate_response


def test_recognition_prompt_template_and_parser_share_declared_fields():
    draft,_=draft_for('text');seen=[]
    def caller(system,user):
        p=json.loads(user);seen.append(p)
        assert set(p['output'])==MATERIAL_REQUIRED|MATERIAL_OPTIONAL
        assert set(p['output']['estimate'])==ESTIMATE_FIELDS
        assert 'material_facts' not in p['output']
        assert 'quantity_claims' not in p['output']['estimate']
        assert '仅为只读输入事实' in system
        return json.dumps(action_payload())
    result=extract_material(draft,caller)
    assert len(seen)==1 and validate_final_estimate(result.estimate_fallbacks[0])==()
    bad=dict(action_payload(),material_facts={'page_count':8})
    with pytest.raises(FormalValidationError) as exc:parse_extraction(json.dumps(bad),draft,set())
    assert feedback(exc.value)['field_path']=='$.material_facts'


@pytest.mark.parametrize('repair',[False,True])
def test_workload_normal_and_repair_receive_one_schema_and_attach_upstream_facts(repair):
    calls=[]
    def caller(system,user):
        p=json.loads(user);calls.append(p)
        assert set(p['output']['estimates'][0])==WORKLOAD_ESTIMATE_FIELDS
        assert 'material_facts' not in p['output'] and 'quantity_claims' not in p['output']['estimates'][0]
        obj=json.loads(estimate_response(p,125))
        obj['estimates'][0].update(basis='分析(115分钟)',assumptions=['按8页正文上界估算'])
        if repair and len(calls)==1:obj['material_facts']={'private_body':'PRIVATE_SOURCE'}
        return json.dumps(obj)
    with capture_estimate_diagnostics() as events:
        result,_=estimate_workloads((work(),),caller,material_facts=FACTS,material_requirements=REQ)
    assert len(calls)==1+int(repair) and result[0][2]=='model_workload'
    assert result[0][1]['quantity_claims']==CLAIMS
    assert result[0][1]['recommended_minutes']==125
    assert 'PRIVATE_SOURCE' not in json.dumps(events)
    if repair:
        failure=next(e['formal_validation'] for e in events if 'formal_validation' in e)
        assert failure['field_name']=='material_facts' and failure['parent_path']=='$'
        assert failure['observed']['type']=='object'
        assert calls[1]['validation_feedback'][0]['field_path']=='$.material_facts'


@pytest.mark.parametrize('mode',['normal','repair','recovery'])
def test_final_path_uses_source_facts_without_model_repetition(mode):
    from src.material_estimate_contract import complete_final_estimate
    from src.material_estimate_recovery import recover_result
    draft,_=draft_for('text');obj=action_payload()
    obj['estimate']['basis']=GOOD
    if mode!='normal':obj['source_title']='context echo'
    raw=json.dumps(obj)
    if mode=='repair':
        final_obj=dict(obj);final_obj.pop('source_title')
        responses=[raw,json.dumps(final_obj)]
    else:responses=[raw]
    old=recover_result(responses,draft,set(),mode!='normal').estimate_fallbacks[0]
    assert old['quantity_claims']==[]
    final=complete_final_estimate(old,draft,set(),responses,FACTS,REQ)
    assert final['quantity_claims']==CLAIMS
    assert validate_final_estimate(final)==()
    assert final['confirmation_fields']==() and minimal_confirmation_mode(final)!='blocked'


@pytest.mark.parametrize('text',['按6～8页上界估算','按8页正文上界估算','8页','处理16条记录','默认已有基础数据，预留一次复核'])
def test_prose_assumptions_are_not_new_material_records(text):
    errors,observations,_,_=evaluate_estimate_facts('分析(115分钟)',[text],FACTS,CLAIMS,REQ)
    assert not errors
    assert not any(c.get('aggregation')=='unknown' for c in observations)


@pytest.mark.parametrize('text',['源文件8页','材料共8页','正文至少400字','讨论至少400字'])
def test_explicit_conflicting_user_copy_still_rejected(text):
    assert evaluate_estimate_facts(text,[],FACTS,CLAIMS,REQ)[0]


def test_authoritative_render_preserves_owners_ranges_and_qualifiers():
    assert authoritative_claims(FACTS,REQ)==CLAIMS
    assert render_source_facts(FACTS,REQ)=='源文件共12页；正文6～8页；讨论约400字'
    assert not evaluate_estimate_facts(GOOD,[],FACTS,CLAIMS,REQ)[0]


def test_unknown_field_path_and_type_are_exact_without_values():
    with pytest.raises(FormalValidationError) as exc:
        fields({'unexpected_requirements':{'body':'PRIVATE_VALUE'}},set(),'$.estimate')
    safe=feedback(exc.value)
    assert safe['code']=='unknown_field'
    assert safe['field_name']=='unexpected_requirements' and safe['parent_path']=='$.estimate'
    assert safe['field_path']=='$.estimate.unexpected_requirements'
    assert safe['observed']['type']=='object'
    assert 'PRIVATE_VALUE' not in json.dumps(safe)


def test_nested_estimate_unknown_fields_are_not_silently_ignored_in_recovery():
    from src.material_estimate_recovery import _action_estimate
    draft,_=draft_for('text');obj=action_payload();obj['estimate']['material_facts']={}
    with pytest.raises(FormalValidationError) as exc:_action_estimate(obj,draft)
    assert feedback(exc.value)['field_path']=='$.estimate.material_facts'


def test_source_fact_render_is_used_in_the_real_card():
    from src.material_ui import _render_estimate_card
    from types import SimpleNamespace
    draft,_=draft_for('text');rendered=[]
    value=dict(task_name='实验报告',short_scope='完成指定部分',focused_minutes_min=105,
        focused_minutes_max=145,recommended_minutes=125,rationale='分析和检查',assumptions=[])
    _render_estimate_card(SimpleNamespace(markdown=lambda text,**kw:rendered.append(text)),draft,value,
        facts_note=render_source_facts(FACTS,REQ))
    assert '源文件共12页；正文6～8页；讨论约400字' in rendered[0]
