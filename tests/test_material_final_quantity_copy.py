"""Validated aggregation survives rendering, draft retention and final gate."""
import json
from dataclasses import replace

import pytest

from src.material_quantity_claims import evaluate_quantities
from src.material_quantity_facts import result_quantity_issues
from src.material_estimate_diagnostics import capture_estimate_diagnostics
from tests.test_material_quantity_facts import FACTS,SOURCE
from tests.test_material_quantity_claims import claim
from tests.test_workload_engine import estimate_response


CLAIMS=[claim('region',2),claim('record',16,'per_region','region',2),claim('record',32)]


@pytest.mark.parametrize('text',[
    '2个区域各16条记录',
    '共2个区域，每区16条，共32条',
    '2个区域，每个区域16条',
    '每区16条，共32条',
    '共2个区域，每区16条',
    '2个区域，共32条记录',
    '共2个区域各16条记录',
])
def test_render_and_final_check_share_the_validated_quantity_scope(text):
    errors,_,rendered,assumptions=evaluate_quantities(text,[],FACTS,CLAIMS)
    assert not errors and rendered==text
    assert not evaluate_quantities(rendered,assumptions,FACTS,CLAIMS)[0]
    # Compatibility with an older saved draft cannot change local into total.
    assert not evaluate_quantities(rendered,assumptions,FACTS)[0]


@pytest.mark.parametrize('text',['共16条记录','总共16条','2个区域，共16条记录'])
def test_true_total_conflict_is_rejected_even_with_correct_structured_claims(text):
    errors=evaluate_quantities(text,[],FACTS,CLAIMS)[0]
    assert any(e['code']=='material_quantity_mismatch' and e['claimed']==16 and e['expected']==32 for e in errors)


def test_subset_and_ambiguous_copy_can_use_structured_attribution_without_guessing():
    subset=[claim('record',16,'scoped_subset'),claim('record',32)]
    assert not evaluate_quantities('其中16条需要复核，共32条',[],FACTS,subset)[0]
    errors,_,rendered,_=evaluate_quantities('处理16条记录',[],FACTS,CLAIMS)
    assert not errors and rendered=='处理每区域16条记录'
    assert not evaluate_quantities(rendered,[],FACTS,CLAIMS)[0]
    assert evaluate_quantities('共8条记录',[],FACTS,[claim('record',8)])[0]


@pytest.mark.parametrize('supplement',['','前两部分已完成，只剩第三至第五部分及提交检查'])
def test_formal_intake_final_gate_retains_claims_and_manual_adoption(supplement):
    from src.material_inbox import MaterialInbox,update_file_source,extract_material,item_values
    from src.file_material import read_file_material,DOCX_MIME
    from tests.document_fixtures import docx_bytes
    from src.material_estimate_recovery import confirm_minimal_task
    from src.material_scope import numbered_scope
    from tests.test_material_estimate_recovery import NOW
    text=SOURCE+'\n第一部分整理数据。\n第二部分计算均值。\n第三部分计算区间。\n第四部分比较均值。\n第五部分撰写讨论。\n提交检查。'
    source=read_file_material('work.docx',DOCX_MIME,docx_bytes(text))
    draft=update_file_source(MaterialInbox(),source,'',NOW,'plan','beiyangyuan',supplemental_context=supplement).draft
    calls=[]
    def main(system,user):calls.append('recognition');return '{}'
    def estimate(system,user):
        calls.append('estimate');obj=json.loads(estimate_response(json.loads(user),150))
        obj['estimates'][0].update(min_focus_minutes=120,max_focus_minutes=180,
            basis='计算(130分钟)：2个区域各16条记录，共32条记录',quantity_claims=CLAIMS)
        return json.dumps(obj)
    with capture_estimate_diagnostics() as events:
        result=extract_material(draft,main,file_source=source,workload_caller=estimate)
    entry=result.estimate_fallbacks[0]
    assert calls==['recognition','estimate'] and entry['origin']=='model_workload'
    assert entry['quantity_claims']==CLAIMS
    assert entry['estimate']['short_scope']==numbered_scope(source.text,supplement).scope
    assert '：2个区域各16条记录' in entry['estimate']['rationale']
    assert not result_quantity_issues(result,FACTS)
    final=[e for e in events if e['stage']=='material_final_copy']
    assert len(final)==1 and final[0]['schema_valid'] is True
    assert any(c['aggregation']=='per_region' for c in final[0]['quantities']['claims'])
    prepared=confirm_minimal_task(result,entry['item_id'],'完成作业',77,True,entry['estimate']['short_scope'])
    assert item_values(prepared.items[0],prepared)['minutes']=='77'
    # A later copy mutation cannot use retained good metadata as a bypass.
    tampered=dict(entry,estimate=dict(entry['estimate'],rationale='计算(130分钟)：共16条记录'))
    assert result_quantity_issues(replace(result,estimate_fallbacks=(tampered,)),FACTS)
