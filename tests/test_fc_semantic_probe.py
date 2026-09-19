import json
from copy import deepcopy
from scripts.fc_semantic_probe import draft_and_gold,evaluate,NAME
from scripts.fc_metadata_probe import metadata_inventory
from src.material_output_schema import ACTIONABILITY_FIELDS,recognition_semantic_guide,actionability_template
from src.material_recognition_schema import recognition_json_schema
from src.material_function_transport import function_definition
from src.p2_tju_live_adapter import TJUP2CallAdapter


def response(action):
    return dict(choices=[dict(finish_reason='tool_calls',message=dict(tool_calls=[dict(type='function',
        function=dict(name=NAME,arguments=json.dumps(dict(actionability=action))))]))])


def test_schema_stays_clean_and_generation_is_read_only():
    formal=recognition_json_schema();before=deepcopy(formal)
    guide=recognition_semantic_guide(formal)
    assert formal==before
    assert not metadata_inventory(function_definition()['parameters'])
    assert function_definition()['parameters']['properties']['actionability']['anyOf'][1]=={'type':'null'}
    for field,(_,meaning) in ACTIONABILITY_FIELDS.items():
        assert field in actionability_template()
        if meaning:assert field in formal['properties']['actionability']['properties'] and meaning in guide
    assert '{' not in guide and '只能输出' not in guide


def test_shared_declaration_drives_template_and_guide(monkeypatch):
    template,meaning=ACTIONABILITY_FIELDS['reason']
    monkeypatch.setitem(ACTIONABILITY_FIELDS,'reason',('changed template','changed meaning'))
    assert actionability_template()['reason']=='changed template'
    assert 'actionability.reason：changed meaning' in recognition_semantic_guide()
    schema=recognition_json_schema();del schema['properties']['actionability']['properties']['reason']
    assert 'changed meaning' not in recognition_semantic_guide(schema)


def test_schema_success_does_not_equal_gold_semantic_success():
    draft,gold=draft_and_gold()
    result=evaluate(response(dict(is_estimatable=False,reason='没有任务',evidence_refs=[])),draft,gold)
    assert result['formal_parser_valid'] and result['source_valid']
    assert not result['estimatable_correct'] and not result['business_correct']


def test_supplied_ref_and_gold_pass_but_fabricated_ref_fails():
    draft,gold=draft_and_gold()
    action=dict(is_estimatable=True,reason='明确要求检查与提交',evidence_refs=[gold['expected_ref']])
    good=evaluate(response(action),draft,gold)
    assert good['business_correct'] and good['valid_refs_count']==good['supporting_refs_count']==1
    action['evidence_refs']=['foreign_ref']
    bad=evaluate(response(action),draft,gold)
    assert bad['unknown_refs_count']==1 and not bad['source_valid'] and not bad['verified_evidence']


def test_unknown_field_and_string_remain_rejected():
    draft,gold=draft_and_gold()
    extra=dict(is_estimatable=True,reason='检查',description='extra',evidence_refs=[gold['expected_ref']])
    assert not evaluate(response(extra),draft,gold)['formal_parser_valid']
    assert not evaluate(response(json.dumps(extra)),draft,gold)['formal_parser_valid']


def test_only_opt_in_tool_path_gets_shared_guide_normal_and_repair():
    calls=[]
    def message(messages,**kwargs):calls.append((messages,kwargs));return '{}'
    adapter=TJUP2CallAdapter(message_call_function=message,recognition_tools=True)
    for stage in ('normal','repair'):adapter.agent_caller('campusflow.material-text.v2 '+stage,'source')
    assert calls[0][1]['recognition_function']==calls[1][1]['recognition_function']
    assert all(recognition_semantic_guide() in c[0][0]['content'] for c in calls)
    assert TJUP2CallAdapter(recognition_tools=False).recognition_tools is False
