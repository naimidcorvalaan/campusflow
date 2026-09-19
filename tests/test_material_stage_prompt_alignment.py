import hashlib
import json
from copy import deepcopy
import pytest

from src.material_recognition_prompts import recognition_system_prompt
from src.material_stage_projection import prepare_recognition_request,recognition_stage_schema,validate_stage_parameters
from src.material_output_schema import recognition_template,output_instructions,RECOGNITION_ROOT_OWNERS
from src.material_formal_validation import FormalValidationError
from src.material_function_transport import ToolArguments
from src.material_inbox import extract_material
from src.p2_tju_live_adapter import TJUP2CallAdapter
from tests.test_fc_original_ten import candidate


def test_legacy_prompt_exactly_preserved_and_stage_has_no_estimate_duty():
    assert hashlib.sha256(recognition_system_prompt().encode()).hexdigest()=='d60c7b1433d8641a51c375c9cc997ac69bbda4f44e29905f909f09015a3d1be3'
    assert '必须给合法区间' in recognition_system_prompt()
    assert '必须给合法区间' not in recognition_system_prompt('recognition')
    assert '每个任务同时给出' not in recognition_system_prompt('recognition')


def test_prompt_template_schema_and_root_instructions_share_ownership(monkeypatch):
    source='正文原样保留：estimate、建议30分钟，保存后提交。'
    context=dict(output=recognition_template(),material=source,previous_recognition='{"estimate":"bad old output"}',
        actionability_source_spans=[dict(ref='verified',text=source)])
    before=deepcopy(context)
    system=recognition_system_prompt()+output_instructions()+' repair suffix'
    projected,user,_=prepare_recognition_request(system,json.dumps(context,ensure_ascii=False))
    obj=json.loads(user)
    assert set(obj['output'])==set(recognition_stage_schema()['properties'])
    assert {k:v for k,v in obj.items() if k!='output'}=={k:v for k,v in before.items() if k!='output'}
    assert context==before and projected.endswith(' repair suffix')
    assert '必须给合法区间' not in projected
    roots=projected.split('输出根字段仅允许：')[1].split('。')[0].split(',')
    assert set(roots)==set(obj['output']) and 'estimate' not in roots
    assert prepare_recognition_request(projected,user)==(projected,user,None)
    monkeypatch.setitem(RECOGNITION_ROOT_OWNERS,'coverage','presentation')
    projected,user,_=prepare_recognition_request(recognition_system_prompt()+output_instructions(),json.dumps(context))
    assert 'coverage' not in json.loads(user)['output']
    assert 'coverage' not in projected.split('输出根字段仅允许：')[1].split('。')[0].split(',')


def test_multimodal_data_untouched_only_template_projected():
    user=json.dumps(dict(output=recognition_template(),material='all original text'))
    content=[dict(type='text',text=user),dict(type='image_url',image_url={'url':'synthetic-image-token'}),dict(type='text',text='other text')]
    original=deepcopy(content)
    _,changed,projected=prepare_recognition_request('system',user,content)
    assert projected[0]['text']==changed and projected[1:]==original[1:]
    assert content==original


@pytest.mark.parametrize('value',[None,'invalid estimate string',{}])
def test_wrong_stage_feedback_precedes_full_model_type_error(value):
    _,_,obj=candidate();obj['estimate']=value;before=deepcopy(obj)
    with pytest.raises(FormalValidationError) as exc:validate_stage_parameters(obj)
    data=exc.value.feedback
    assert data['code']=='unknown_field' and data['field_path']=='$.estimate'
    assert data['field_owner']=='estimate' and data['stage']=='recognition'
    assert 'omit' in data['expected'] and obj==before


def test_actual_formal_prompt_is_projected_at_opt_in_request_boundary():
    draft,_,obj=candidate(2);messages=[]
    def send(msgs,**kwargs):messages.append((msgs,kwargs));return ToolArguments(json.dumps(obj))
    adapter=TJUP2CallAdapter(message_call_function=send,recognition_tools=True)
    class Stop(BaseException):pass
    def caller(system,user):
        adapter.agent_caller(system,user)
        repair=json.loads(user);repair['previous_recognition']='{"estimate":"bad"}'
        repair['validation_feedback']=dict(code='unknown_field',field_path='$.estimate',field_owner='estimate')
        adapter.agent_caller(system+' semantic repair',json.dumps(repair,ensure_ascii=False))
        raise Stop()
    with pytest.raises(Stop):extract_material(draft,caller)
    assert len(messages)==2
    for msgs,kwargs in messages:
        context=json.loads(msgs[1]['content'])
        assert context['material']==draft.original_text
        assert set(context['output'])==set(kwargs['recognition_function']['parameters']['properties'])
        assert 'estimate' not in context['output']
        assert '必须给合法区间' not in msgs[0]['content']
        roots=msgs[0]['content'].split('输出根字段仅允许：')[1].split('。')[0].split(',')
        assert 'estimate' not in roots
    assert messages[0][1]['recognition_function']==messages[1][1]['recognition_function']
    assert json.loads(messages[1][0][1]['content'])['previous_recognition']=='{"estimate":"bad"}'
    assert TJUP2CallAdapter(recognition_tools=False).recognition_tools is False


def test_item_template_ownership_is_shared_with_schema_and_repair():
    from src.material_output_schema import formal_item_template
    from src.material_stage_projection import project_item_template
    original=formal_item_template()
    assert 'estimate' in original
    system,user,_=prepare_recognition_request('system',json.dumps(dict(output=recognition_template(),formal_item_format=original)))
    projected=json.loads(user)['formal_item_format']
    assert 'estimate' not in projected and 'estimate' in original
    assert set(projected)==set(recognition_stage_schema()['properties']['items']['items']['properties'])
    assert project_item_template(projected)==projected


def test_item_stage_violation_preserves_legacy_model_and_reports_owner():
    from src.material_recognition_schema import validate_parameters
    from src.material_output_schema import ITEM_REQUIRED
    _,_,obj=candidate()
    item=dict.fromkeys(ITEM_REQUIRED)
    item.update(kind='task',title='检查表格',scope='检查',completion='核对后提交',evidence='核对',uncertainties=[],estimate=None)
    obj['items']=[item]
    validate_parameters(obj)
    with pytest.raises(FormalValidationError) as exc:validate_stage_parameters(obj)
    assert exc.value.feedback['field_path']=='$.items[0].estimate'
    assert exc.value.feedback['field_owner']=='estimate'
    del item['estimate']
    validate_stage_parameters(obj)
