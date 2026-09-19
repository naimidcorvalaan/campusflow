"""Isolated schema-annotation A/B experiment; no production activation.

Four calls per condition, same prompt, no repair. Only safe shapes, schema
definitions and usage are saved. Previous raw responses cannot be recreated.
"""
import argparse
import json
import os
import sys
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.fc_nullable_adapter_probe import CASES,NAME,formal_minimal_schema
from scripts.fc_schema_probe import ENDPOINT,wire_schema,json_type
from src.material_tool_schema import tool_schema
from src.material_recognition_schema import recognition_json_schema
from src.material_function_transport import ToolArguments
from src.material_serialization_repair import strict_object
from src.material_inbox import MaterialInbox,update_source,parse_extraction
from src.material_actionability_sources import source_spans,validate_repaired_action
from src.material_formal_validation import feedback
DEST=ROOT/'artifacts/evaluation/tju-20260916/function-metadata'
from src.material_tool_schema import ANNOTATIONS, schema_children as children, strip_annotations


def metadata_inventory(schema,path='$'):
    if not isinstance(schema,dict):return []
    rows=[dict(path=path+'.'+key,key=key,type=json_type(value),
        text_mentions_description=isinstance(value,str) and 'description' in value)
        for key,value in schema.items() if key in ANNOTATIONS or key in ('$defs','definitions','$ref','$id','$schema')]
    for route,child in children(schema):
        child_path=path+''.join('['+str(k)+']' if isinstance(k,int) else '.'+k for k in route)
        rows.extend(metadata_inventory(child,child_path))
    return rows


def matrix():
    a=tool_schema(formal_minimal_schema())
    prior=json.loads((ROOT/'artifacts/evaluation/tju-20260916/function-adapter/null-sent-schema.json').read_text(encoding='utf-8'))
    assert a==prior['parameters'],'A must match previously sent schema'
    return {'A':a,'B':strip_annotations(a)}


def history_audit():
    base=ROOT/'artifacts/evaluation/tju-20260916/function-adapter'
    rows=[r for r in json.loads((base/'null-probes.json').read_text(encoding='utf-8')) if r['case']=='object']
    rows+=json.loads((base/'diagnostic-object.json').read_text(encoding='utf-8'))
    definition=recognition_json_schema()['properties']['actionability'];allowed=set(definition['properties'])
    result=[]
    for row in rows:
        shapes=row.get('actionability_field_shapes');names=set(shapes) if shapes else None
        result.append(dict(call_id='historical-object-'+str(row['repetition']),actual_fields=sorted(names) if names else None,
            allowed_fields=sorted(allowed),unknown_fields=sorted(names-allowed) if names else None,
            missing_required=sorted(set(definition['required'])-names) if names else None,
            field_shapes=shapes,schema_errors=row.get('schema_errors'),recorded_failure=row.get('failure'),
            formal_parser_executed=False,limitation=None if shapes else 'field shapes and raw response were not retained; not retrospectively diagnosable'))
    return result


def inspect_response(data,draft):
    from jsonschema import Draft7Validator
    choices=data.get('choices') or [];choice=choices[0] if len(choices)==1 else {}
    calls=(choice.get('message') or {}).get('tool_calls') or []
    result=dict(tool_call_count=len(calls),target_tool_calls=sum(c.get('function',{}).get('name')==NAME for c in calls),
        finish_reason=choice.get('finish_reason'),json_valid=False,formal_schema_valid=False,formal_parser_valid=False,source_valid=False)
    if len(calls)!=1 or result['target_tool_calls']!=1:return result
    try:obj=strict_object(calls[0]['function']['arguments'])
    except (ValueError,TypeError):return result
    result['json_valid']=True;action=obj.get('actionability');result['actionability_type']=json_type(action)
    definition=recognition_json_schema()['properties']['actionability'];allowed=set(definition['properties'])
    names=set(action) if isinstance(action,dict) else set()
    result.update(actual_fields=sorted(names),allowed_fields=sorted(allowed),unknown_fields=sorted(names-allowed),
        missing_required=sorted(set(definition['required'])-names),description_present='description' in names,
        field_shapes={k:dict(type=json_type(v),length=len(v) if isinstance(v,(str,list,dict)) else None)
            for k,v in action.items()} if isinstance(action,dict) else {})
    errors=list(Draft7Validator(formal_minimal_schema()).iter_errors(obj))
    result['schema_errors']=[dict(path='$'+''.join('['+str(k)+']' if isinstance(k,int) else '.'+k for k in e.absolute_path),
        rule=e.validator,observed_type=json_type(e.instance)) for e in errors]
    result['wrong_types']=[e for e in result['schema_errors'] if e['rule']=='type']
    result['enum_failures']=[e for e in result['schema_errors'] if e['rule']=='enum']
    result['minimal_schema_valid']=not errors
    envelope=dict(schema_version='campusflow.material-text.v2',reference_date=None,reference_evidence=None,items=[],**obj)
    full_errors=list(Draft7Validator(recognition_json_schema()).iter_errors(envelope))
    result['formal_schema_valid']=not full_errors
    raw=ToolArguments(json.dumps(envelope,ensure_ascii=False))
    # Run the unchanged formal parser even when the experimental schema fails,
    # so every candidate has an exact formal error, not just JSONSchema errors.
    try:
        parse_extraction(raw,draft,set());result['formal_parser_valid']=True
    except Exception as exc:result['formal_parser_failure']=feedback(exc)
    if result['formal_parser_valid']:
        try:validate_repaired_action(raw,draft);result['source_valid']=True
        except Exception as exc:result['source_failure']=feedback(exc)
    if isinstance(action,dict):result['is_estimatable']=action.get('is_estimatable') if type(action.get('is_estimatable')) is bool else None
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
    DEST.mkdir(parents=True,exist_ok=True);schemas=matrix()
    audit=dict(history=history_audit(),metadata={k:metadata_inventory(v) for k,v in schemas.items()},
        function_description='返回识别结果。',function_mentions_description=False,
        full_current_metadata=metadata_inventory(tool_schema(recognition_json_schema())))
    (DEST/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(history_identifiable=sum(r['actual_fields'] is not None for r in audit['history']),
        metadata=audit['metadata']),ensure_ascii=False),flush=True)
    if not args.live:return
    dest=DEST/'calls.json'
    if dest.exists():raise RuntimeError('No repeated experiment')
    env = []
    pass  # Use the caller environment unchanged.
    os.environ['TJU_LLM_BASE_URL']=ENDPOINT[:-len('/chat/completions')]
    from src.tju_llm_client import _require_all_envs,_build_chat_url
    base,key,model=_require_all_envs();assert _build_chat_url(base)==ENDPOINT and model=='tju-llm'
    text=CASES['object'];draft=update_source(MaterialInbox(),text,'',datetime(2026,9,16,14),'plan','beiyangyuan').draft
    messages=[dict(role='system',content='请调用给定函数返回识别结果。'),dict(role='user',content=json.dumps(dict(material=text,
        source_refs=[dict(ref=s['ref'],text=s['text']) for s in source_spans(draft)]),ensure_ascii=False))]
    import requests
    rows=[]
    with requests.Session() as session:
        for repeat in range(4):
            for variant in ('A','B'):
                payload=dict(model=model,temperature=0,max_tokens=500,tool_choice='auto',messages=messages,
                    tools=[dict(type='function',function=dict(name=NAME,description='返回识别结果。',parameters=schemas[variant]))])
                prepared=session.prepare_request(requests.Request('POST',ENDPOINT,json=payload,headers={'Authorization':'Bearer '+key}))
                wire=wire_schema(prepared.body);assert wire['parameters']==schemas[variant]
                (DEST/('sent-schema-'+variant+'.json')).write_text(json.dumps(wire,ensure_ascii=False,indent=2),encoding='utf-8')
                settings=session.merge_environment_settings(prepared.url,{},None,None,None)
                row=dict(variant=variant,repetition=repeat+1);start=time.monotonic()
                try:
                    response=session.send(prepared,timeout=120,**settings);row['http_status']=response.status_code
                    if response.status_code==200:
                        data=response.json();row.update(inspect_response(data,draft));usage=data.get('usage') or {}
                        row['usage']={k:usage.get(k) for k in ('prompt_tokens','completion_tokens','total_tokens')}
                except Exception as exc:row['transport_error_type']=type(exc).__name__
                row['latency_ms']=round((time.monotonic()-start)*1000);rows.append(row)
                dest.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(row,ensure_ascii=False),flush=True)
                if row.get('http_status')!=200:return
                time.sleep(2.1)

if __name__=='__main__':main()
