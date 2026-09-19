"""Model-only serialization recovery, bounded and semantic-preserving."""
import json
from copy import deepcopy

import pytest

from src.material_serialization_repair import eligible,repair,semantic_changes,witness,SYSTEM
from src.material_formal_validation import extract_material_object,FormalValidationError
from src.material_inbox import extract_material,parse_extraction
from src.material_actionability_sources import validate_repaired_action
from src.material_estimate_diagnostics import capture_estimate_diagnostics
from tests.test_material_actionability_sources import draft,with_refs


def parse_error(raw):
    with pytest.raises(FormalValidationError) as exc:extract_material_object(raw)
    return exc.value


def format_once(original,candidate,current=None):
    current=current or draft()
    calls=[]
    def request(system,user):calls.append((system,json.loads(user)));return candidate
    with capture_estimate_diagnostics() as events:
        try:
            value=repair(original,parse_error(original),request,{},
                lambda raw:parse_extraction(raw,current,set()),lambda raw:validate_repaired_action(raw,current))
            error=None
        except (ValueError,TypeError) as exc:value=None;error=exc
    return value,error,calls,next(e['serialization_repair'] for e in events if 'serialization_repair' in e)


def test_original_missing_close_is_observed_but_never_locally_parsed_or_changed():
    obj=with_refs(draft());raw=json.dumps(obj);broken=raw[:-1]
    before=broken
    nodes,complete=witness(broken)
    assert complete and nodes[('actionability','is_estimatable')]==('bool',True)
    assert broken==before
    with pytest.raises(ValueError):json.loads(broken)
    assert semantic_changes(broken,raw)==dict(semantic_comparison_complete=True,
        semantic_fields_changed_count=0,refs_changed_count=0,enums_changed_count=0)


def test_format_repair_preserves_semantics_and_runs_formal_validation():
    raw=json.dumps(with_refs(draft()))
    value,error,calls,safe=format_once(raw[:-1],raw)
    assert value==raw and error is None and len(calls)==1
    assert safe['repaired_parse_success'] and safe['schema_validation_success']
    assert safe['actionability_validation_success'] and safe['semantic_fields_changed_count']==0
    assert set(calls[0][1])=={'request_stage','previous_response','parser_error','recognition_contract'}
    assert calls[0][0]==SYSTEM


@pytest.mark.parametrize('mutation,category',[
    ('enum','enums_changed_count'),('refs','refs_changed_count'),('minutes','semantic_fields_changed_count'),
    ('scope','semantic_fields_changed_count'),('remove','semantic_fields_changed_count'),
])
def test_model_cannot_change_business_values(mutation,category):
    obj=with_refs(draft());raw=json.dumps(obj);changed=deepcopy(obj)
    if mutation=='enum':changed['actionability']['is_estimatable']=False
    if mutation=='refs':changed['actionability']['evidence_refs']=['replacement']
    if mutation=='minutes':changed['estimate']['recommended_minutes']+=1
    if mutation=='scope':changed['actionability']['short_scope']='changed'
    if mutation=='remove':changed['actionability'].pop('waiting_note')
    _,error,calls,safe=format_once(raw[:-1],json.dumps(changed))
    assert len(calls)==1 and error.feedback['code']=='serialization_semantics_changed'
    assert safe[category]>0 and not safe['actionability_validation_success']


def test_format_repair_cannot_add_unknown_field():
    obj=with_refs(draft());raw=json.dumps(obj);obj['unknown_field']=1
    _,error,_,safe=format_once(raw[:-1],json.dumps(obj))
    assert error.feedback['code']=='unknown_field'
    assert not safe['schema_validation_success']


@pytest.mark.parametrize('suffix',['{}',' []',' 解释','}\n'])
def test_serialization_output_is_strict_one_object_without_boundary_canonicalization(suffix):
    raw=json.dumps(with_refs(draft()))
    _,error,calls,safe=format_once(raw[:-1],raw+suffix)
    assert error and len(calls)==1 and not safe['repaired_parse_success']


def test_still_invalid_is_one_request_without_loop():
    raw=json.dumps(with_refs(draft()))[:-1]
    _,error,calls,safe=format_once(raw,raw)
    assert error and len(calls)==1 and safe['decision']=='rejected'
    assert not safe['repaired_parse_success']


def test_unobservable_damaged_string_cannot_be_guessed():
    obj=with_refs(draft());raw=json.dumps(obj)
    broken='{"schema_version":"private_unfinished'
    _,error,calls,safe=format_once(broken,raw)
    assert len(calls)==1 and error.feedback['code']=='serialization_semantics_unverifiable'
    assert safe['semantic_fields_changed_count'] is None
    assert 'private_unfinished' not in json.dumps(safe)


def test_semantic_witness_protects_owner_and_array_order_not_only_scalar_bag():
    original='{"a":{"ref":"one"},"b":{"ref":"two"}'
    changed='{"a":{"ref":"two"},"b":{"ref":"one"}}'
    assert semantic_changes(original,changed)['semantic_fields_changed_count']>0
    assert semantic_changes('{"evidence_refs":["one","two"]','{"evidence_refs":["two","one"]}')['refs_changed_count']==2


def test_unmodified_bad_source_refs_still_fail_business_validation_after_formatting():
    obj=with_refs(draft(),['unknown']);raw=json.dumps(obj)
    _,error,_,safe=format_once(raw[:-1],raw)
    assert safe['semantic_fields_changed_count']==0 and safe['schema_validation_success']
    assert not safe['actionability_validation_success']
    assert error.feedback['invariant_id']=='actionability_repair_verified_source'


@pytest.mark.parametrize('tail',['{}',' []',',"x":{}'])
def test_multiple_structures_are_not_serialization_eligible(tail):
    raw=json.dumps(with_refs(draft()))+tail
    error=parse_error(raw)
    assert not eligible(error,dict(error_code='ambiguous_multiple_structures'))


@pytest.mark.parametrize('mode',['normal','semantic','serialization'])
def test_production_chain_only_formats_invalid_semantic_result(mode):
    current=draft();obj=with_refs(current);raw=json.dumps(obj);requests=[]
    def caller(system,user):
        payload=json.loads(user);requests.append((system,payload))
        if len(requests)==1:return raw if mode=='normal' else raw+'{}'
        if payload.get('request_stage')=='recognition_structure_repair':
            return raw[:-1] if mode=='serialization' else raw
        assert payload['request_stage']=='recognition_serialization_repair'
        assert set(payload)=={'request_stage','previous_response','parser_error','recognition_contract'}
        assert payload['recognition_contract']['output']==requests[0][1]['output']
        return raw
    with capture_estimate_diagnostics() as events:result=extract_material(current,caller)
    assert result.model_calls==len(requests)=={'normal':1,'semantic':2,'serialization':3}[mode]
    assert result.estimate_fallbacks[0]['estimate']['recommended_minutes']==20
    serial=[e['serialization_repair'] for e in events if 'serialization_repair' in e]
    assert len(serial)==int(mode=='serialization')
    for _,p in requests:
        if p.get('request_stage')=='recognition_serialization_repair':
            assert not {'material','material_facts','material_requirements','actionability_source_spans','verified_action_source_refs'} & set(p)
    for e in events:
        if 'prompt_shape' in e:assert e['prompt_shape']['contract_injections']==1


def test_initial_syntax_failure_never_directly_calls_serialization():
    raw=json.dumps(with_refs(draft()))[:-1];requests=[]
    def caller(system,user):requests.append(json.loads(user));return raw
    extract_material(draft(),caller)
    assert all(p.get('request_stage')!='recognition_serialization_repair' for p in requests)


def test_failed_serialization_does_not_loop_and_keeps_final_contract_blocked():
    current=draft();raw=json.dumps(with_refs(current));requests=[]
    def caller(system,user):
        payload=json.loads(user);requests.append(payload)
        if len(requests)==1:return raw+'{}'
        if payload.get('request_stage') in ('recognition_structure_repair','recognition_serialization_repair'):return raw[:-1]
        return '{}'
    with capture_estimate_diagnostics() as events:result=extract_material(current,caller)
    assert sum(p.get('request_stage')=='recognition_serialization_repair' for p in requests)==1
    from src.material_estimate_contract import validate_final_estimate
    for entry in result.estimate_fallbacks:
        with pytest.raises(ValueError):validate_final_estimate(entry)
    assert next(e['serialization_repair'] for e in events if 'serialization_repair' in e)['decision']=='rejected'
