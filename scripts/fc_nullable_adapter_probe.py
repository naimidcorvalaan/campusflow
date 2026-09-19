"""Six tiny positive/null probes, no product state or response conversions."""
import json,os,sys,time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.fc_schema_probe import ENDPOINT,wire_schema,json_type
from src.material_tool_schema import tool_schema
from src.material_recognition_schema import recognition_json_schema,validate_parameters
from src.material_serialization_repair import strict_object
from src.material_function_transport import ToolArguments
from src.material_inbox import MaterialInbox,update_source,parse_extraction
from src.material_actionability_sources import source_spans,validate_repaired_action
from src.material_formal_validation import feedback
DEST=ROOT/'artifacts/evaluation/tju-20260916/function-adapter'
NAME='nullable_action_result'
CASES={'object':'请检查实验表格中的缺失项，核对后提交结果。',
       'null':'这是一张纯装饰的蓝色背景图片，没有文字、任务或待处理事项。'}


def formal_minimal_schema():
    original=recognition_json_schema()['properties']['actionability']
    action=dict(type=deepcopy(original['type']),properties={k:deepcopy(original['properties'][k])
        for k in ('is_estimatable','reason','evidence_refs')},required=list(original['required']),additionalProperties=False,
        description='材料中可辨认的待处理行为；没有可辨认行为时为空。')
    return dict(type='object',properties=dict(actionability=action),required=['actionability'],additionalProperties=False)


def inspect_result(data,case,draft):
    from jsonschema import Draft7Validator
    choices=data.get('choices') or [];choice=choices[0] if len(choices)==1 else {}
    calls=(choice.get('message') or {}).get('tool_calls') or []
    row=dict(tool_call_count=len(calls),target_tool_calls=sum(c.get('function',{}).get('name')==NAME for c in calls),
        json_valid=False,formal_schema_valid=False,formal_parser_valid=False,source_valid=False,
        actionability_type='not_observed',case_pass=False,finish_reason=choice.get('finish_reason'))
    if len(calls)!=1 or row['target_tool_calls']!=1:return row
    try:
        obj=strict_object(calls[0]['function']['arguments']);row['json_valid']=True
        value=obj.get('actionability');row['actionability_type']=json_type(value) if 'actionability'in obj else 'missing'
        errors=list(Draft7Validator(formal_minimal_schema()).iter_errors(obj))
        row['schema_errors']=[dict(path='$'+''.join('['+str(p)+']' if isinstance(p,int) else '.'+str(p) for p in e.absolute_path),
            rule=e.validator,schema_path=list(e.absolute_schema_path),observed_type=json_type(e.instance)) for e in errors]
        if isinstance(value,dict):
            row['actionability_field_shapes']={k:dict(type=json_type(v),length=len(v) if isinstance(v,(str,list,dict)) else None)
                for k,v in value.items() if isinstance(k,str) and len(k)<80}
        if errors:return row
        envelope=dict(schema_version='campusflow.material-text.v2',reference_date=None,reference_evidence=None,items=[],**obj)
        # Only the fixed envelope is supplied by the test; actionability is
        # exactly the received JSON value, never decoded/filled/reinterpreted.
        validate_parameters(envelope);row['formal_schema_valid']=True
        raw=ToolArguments(json.dumps(envelope,ensure_ascii=False));parse_extraction(raw,draft,set());row['formal_parser_valid']=True
        validate_repaired_action(raw,draft);row['source_valid']=True
        row['case_pass']=row['actionability_type']==case and (case=='null' or value.get('is_estimatable') is True)
        if isinstance(value,dict):row['is_estimatable']=value.get('is_estimatable')
    except Exception as exc:row['failure']=feedback(exc)
    return row


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--diagnostic-object',action='store_true');args=parser.parse_args()
    DEST.mkdir(parents=True,exist_ok=True);dest=DEST/('diagnostic-object.json' if args.diagnostic_object else 'null-probes.json')
    if dest.exists():raise RuntimeError('No automatic repeated experiment')
    env = []
    pass  # Use the caller environment unchanged.
    os.environ['TJU_LLM_BASE_URL']=ENDPOINT[:-len('/chat/completions')]
    from src.tju_llm_client import _require_all_envs,_build_chat_url
    base,key,model=_require_all_envs();assert _build_chat_url(base)==ENDPOINT and model=='tju-llm'
    import requests
    schema=tool_schema(formal_minimal_schema());rows=[]
    with requests.Session() as session:
        for repeat in ([3] if args.diagnostic_object else range(3)):
            for case,text in (list(CASES.items())[:1] if args.diagnostic_object else CASES.items()):
                draft=update_source(MaterialInbox(),text,'',datetime(2026,9,16,14),'plan','beiyangyuan').draft
                spans=source_spans(draft)
                payload=dict(model=model,temperature=0,max_tokens=500,tool_choice='auto',tools=[dict(type='function',function=dict(
                    name=NAME,description='返回识别结果。',parameters=schema))],messages=[dict(role='system',content='请调用给定函数返回识别结果。'),
                    dict(role='user',content=json.dumps(dict(material=text,source_refs=[dict(ref=s['ref'],text=s['text']) for s in spans]),ensure_ascii=False))])
                prepared=session.prepare_request(requests.Request('POST',ENDPOINT,json=payload,headers={'Authorization':'Bearer '+key}))
                audit=wire_schema(prepared.body);assert audit['parameters']==schema
                (DEST/'null-sent-schema.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
                settings=session.merge_environment_settings(prepared.url,{},None,None,None)
                row=dict(case=case,repetition=repeat+1);start=time.monotonic()
                try:
                    response=session.send(prepared,timeout=120,**settings);row['http_status']=response.status_code
                    if response.status_code==200:
                        data=response.json();row.update(inspect_result(data,case,draft));usage=data.get('usage') or {}
                        row['usage']={k:usage.get(k) for k in ('prompt_tokens','completion_tokens','total_tokens')}
                except Exception as exc:row['transport_error_type']=type(exc).__name__
                row['latency_ms']=round((time.monotonic()-start)*1000)
                rows.append(row);dest.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(row,ensure_ascii=False),flush=True)
                if row.get('http_status')!=200:return
                time.sleep(2.1)

if __name__=='__main__':main()
