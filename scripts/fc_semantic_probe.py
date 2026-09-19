"""Paired clean-schema semantic-guide A/B; four repetitions of the same text.

Gold expectations are fixed before requests. No full recognition fixtures,
estimate, repair, persistence or publishing. Response values are never repaired.
"""
import json,os,sys,time
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.fc_nullable_adapter_probe import CASES,NAME,formal_minimal_schema
from scripts.fc_schema_probe import ENDPOINT,wire_schema
from scripts.fc_metadata_probe import inspect_response,metadata_inventory
from src.material_tool_schema import recognition_tool_schema
from src.material_output_schema import recognition_semantic_guide
from src.material_serialization_repair import strict_object
from src.material_inbox import MaterialInbox,update_source
from src.material_actionability_sources import source_spans,resolve
DEST=ROOT/'artifacts/evaluation/tju-20260916/function-semantics'
GOLD=dict(expected_is_estimatable=True,expected_type='object',
    expected_evidence_role='唯一原文段落明确要求检查缺失项、核对并提交结果，已有可辨认工作范围。',
    non_estimatable_reason=None)


def draft_and_gold():
    draft=update_source(MaterialInbox(),CASES['object'],'',datetime(2026,9,16,14),'plan','beiyangyuan').draft
    spans=source_spans(draft)
    assert len(spans)==1 and spans[0]['text']==CASES['object']
    # The expected source is chosen by the fixture specification, not inferred
    # from the support classifier or from any model output.
    gold=dict(GOLD,expected_ref=spans[0]['ref'],refs_supplied_count=1)
    return draft,gold


def evaluate(data,draft,gold):
    result=inspect_response(data,draft)
    result.update(refs_supplied_count=gold['refs_supplied_count'],refs_returned_count=0,
        valid_refs_count=0,supporting_refs_count=0,unknown_refs_count=0,unknown_support_count=0,
        evidence_provided=False,verified_evidence=False,estimatable_correct=False,business_correct=False)
    if not result['json_valid']:return result
    obj=strict_object(data['choices'][0]['message']['tool_calls'][0]['function']['arguments'])
    action=obj.get('actionability')
    if not isinstance(action,dict):return result
    refs=action.get('evidence_refs');refs=refs if isinstance(refs,list) else []
    index={s['ref']:s for s in source_spans(draft)}
    valid=[r for r in refs if isinstance(r,str) and r in index and index[r]['in_current_scope']]
    audit=resolve(action,draft)[1]
    result.update(refs_returned_count=len(refs),valid_refs_count=len(valid),
        supporting_refs_count=sum(index[r]['support_state']=='action_supporting' for r in valid),
        unknown_refs_count=sum(not isinstance(r,str) or r not in index for r in refs),
        unknown_support_count=sum(index[r]['support_state']=='unknown' for r in valid),
        evidence_provided=bool(refs),verified_evidence=audit['verified_evidence_required'],
        estimatable_correct=type(action.get('is_estimatable')) is bool and action['is_estimatable']==gold['expected_is_estimatable'])
    result['business_correct']=bool(result['formal_parser_valid'] and result['source_valid'] and result['estimatable_correct']
        and result['verified_evidence'] and gold['expected_ref'] in valid)
    return result


def main():
    DEST.mkdir(parents=True,exist_ok=True);dest=DEST/'calls.json'
    if dest.exists():raise RuntimeError('No repeated experiment')
    schema=recognition_tool_schema(formal_minimal_schema());assert not metadata_inventory(schema)
    old=json.loads((ROOT/'artifacts/evaluation/tju-20260916/function-metadata/sent-schema-B.json').read_text(encoding='utf-8'))
    assert old['parameters']==schema,'A/B must retain the previously tested clean schema'
    draft,gold=draft_and_gold();guide=recognition_semantic_guide(formal_minimal_schema())
    (DEST/'design.json').write_text(json.dumps(dict(schema=schema,guide=guide,gold=[dict(call_pair=i+1,**gold) for i in range(4)],
        repeated_single_material=True,description='Four paired repetitions, not four distinct materials'),ensure_ascii=False,indent=2),encoding='utf-8')
    env = []
    pass  # Use the caller environment unchanged.
    os.environ['TJU_LLM_BASE_URL']=ENDPOINT[:-len('/chat/completions')]
    from src.tju_llm_client import _require_all_envs,_build_chat_url
    base,key,model=_require_all_envs();assert _build_chat_url(base)==ENDPOINT and model=='tju-llm'
    user=json.dumps(dict(material=CASES['object'],source_refs=[dict(ref=s['ref'],text=s['text']) for s in source_spans(draft)]),ensure_ascii=False)
    import requests
    rows=[]
    with requests.Session() as session:
        for repeat in range(4):
            for variant in ('A','B'):
                system='请调用给定函数返回识别结果。'+('\n'+guide if variant=='B' else '')
                payload=dict(model=model,temperature=0,max_tokens=500,tool_choice='auto',
                    tools=[dict(type='function',function=dict(name=NAME,description='返回识别结果。',parameters=schema))],
                    messages=[dict(role='system',content=system),dict(role='user',content=user)])
                prepared=session.prepare_request(requests.Request('POST',ENDPOINT,json=payload,headers={'Authorization':'Bearer '+key}))
                wire=wire_schema(prepared.body);assert wire['parameters']==schema
                (DEST/('sent-schema-'+variant+'.json')).write_text(json.dumps(wire,ensure_ascii=False,indent=2),encoding='utf-8')
                settings=session.merge_environment_settings(prepared.url,{},None,None,None)
                row=dict(variant=variant,repetition=repeat+1,schema_sha256=wire['schema_sha256']);start=time.monotonic()
                try:
                    response=session.send(prepared,timeout=120,**settings);row['http_status']=response.status_code
                    if response.status_code==200:
                        data=response.json();row.update(evaluate(data,draft,gold));usage=data.get('usage') or {}
                        row['usage']={k:usage.get(k) for k in ('prompt_tokens','completion_tokens','total_tokens')}
                except Exception as exc:row['transport_error_type']=type(exc).__name__
                row['latency_ms']=round((time.monotonic()-start)*1000);rows.append(row)
                dest.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(row,ensure_ascii=False),flush=True)
                if row.get('http_status')!=200:return
                time.sleep(2.1)

if __name__=='__main__':main()
