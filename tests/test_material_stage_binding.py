import json
from dataclasses import replace
import pytest
from src.material_inbox import MaterialTime,MaterialError,extract_material
from src.material_function_transport import ToolArguments
from src.material_estimate_recovery import matching_unestimated_item,confirm_minimal_task,minimal_confirmation_mode
from tests.test_fc_original_ten import candidate
from tests.test_workload_engine import estimate_response


def prepared():
    draft,gold,obj=candidate(0)
    work=obj['workload'][0]
    obj['items']=[dict(kind='task',title=work['task_name'],scope=work['scope'],
        completion='核对后提交',evidence=gold['text'],deadline=None,start=None,end=None,
        location_text=None,campus_id=None,commitment_kind=None,minutes=None,
        duration_evidence=None,uncertainties=[],possible_task_ref=None)]
    result=extract_material(draft,lambda *a:ToolArguments(json.dumps(obj)),
        workload_caller=lambda s,u:estimate_response(json.loads(u),25))
    return result


def test_same_stage_identity_adopts_once_and_retains_source_row():
    result=prepared();entry=result.estimate_fallbacks[0];original=result.items[0]
    assert entry['item_id']==original.item_id
    assert not entry['confirmation_fields']
    assert minimal_confirmation_mode(entry)=='scope'
    adopted=confirm_minimal_task(result,entry['item_id'],original.title,32,True,original.scope)
    assert len(adopted.items)==1 and not adopted.estimate_fallbacks
    row=adopted.items[0]
    assert (row.item_id,row.completion,row.evidence)==(original.item_id,original.completion,original.evidence)
    assert row.user_edits['minutes']=='32' and row.minutes==25


@pytest.mark.parametrize('change',[dict(scope='不同范围'),dict(minutes=10),dict(user_edits={'reviewed':True}),
    dict(deadline=MaterialTime(text='明天')),dict(possible_task_ref='day_task_001'),dict(location_text='图书馆'),
    dict(ambiguities=('scope',))])
def test_binding_does_not_guess_or_replace_existing_facts(change):
    result=prepared();entry=result.estimate_fallbacks[0]
    changed=replace(result.items[0],**change)
    assert matching_unestimated_item(entry,(changed,)) is None
    with pytest.raises(MaterialError):
        confirm_minimal_task(replace(result,items=(changed,)),entry['item_id'],'任务',32,True,entry['estimate']['short_scope'])


def test_duplicate_or_different_rows_do_not_collapse_into_one_task():
    result=prepared();entry=result.estimate_fallbacks[0]
    assert matching_unestimated_item(entry,result.items*2) is None


def test_sealed_item_identity_cannot_be_redirected():
    result=prepared();entry=dict(result.estimate_fallbacks[0],item_id='other-row')
    assert minimal_confirmation_mode(entry)=='blocked'


def test_ui_renders_one_adoption_card_for_the_bound_task():
    from tests.test_material_adoption_regression import app_for,adopt_button
    app=app_for(prepared())
    assert not app.exception
    assert len([v for v in app.number_input if v.label=='采用分钟'])==1
    assert adopt_button(app,25)
