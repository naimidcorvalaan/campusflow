"""Isolated TJU schema compatibility experiment. No product transport migration.

Run with --live only after offline tests. Never save prompts, response bodies,
headers or credentials. Requests are fixed to 3 repetitions of 5 tiny schemas.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from copy import deepcopy
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
DEST=ROOT/'artifacts/evaluation/tju-20260916/function-schema-compatibility'
ENDPOINT=__import__('os').environ.get('TJU_LLM_BASE_URL','https://ai.tju.edu.cn/api/v3').rstrip('/')+'/chat/completions'
FUNCTION='schema_compatibility_result'
REPETITIONS=3
TEXT='请检查实验表格中的缺失项，核对后提交结果。'


def schemas():
    from src.material_recognition_schema import recognition_json_schema,feature_schema
    original=recognition_json_schema()['properties']['actionability']
    props={k:deepcopy(original['properties'][k]) for k in ('is_estimatable','reason','evidence_refs')}
    for value in props.values():value.pop('description',None)
    obj=dict(type='object',properties=props,required=list(original['required']),additionalProperties=False)
    union=deepcopy(obj);union['type']=deepcopy(original['type'])
    # anyOf is already used by the current exporter. Validate its standard
    # semantics with the installed jsonschema library before any real calls.
    alternative=dict(anyOf=[deepcopy(obj),dict(type='null')])
    always=deepcopy(obj)
    always['properties']['present']=dict(type='boolean',description='原对象存在为true，无对象为false；不是是否可估时。')
    always['required'].append('present')
    result={k:dict(type='object',properties=dict(actionability=v),required=['actionability'],additionalProperties=False)
        for k,v in [('A',obj),('B',union),('C',alternative),('D',always)]}
    kind=deepcopy(feature_schema()['properties']['kind']);kind['description']='工作量类型。'
    result['E']=dict(type='object',properties=dict(feature_type=kind),required=['feature_type'],additionalProperties=False)
    from jsonschema import Draft7Validator
    for schema in result.values():Draft7Validator.check_schema(schema)
    return result


def payload(variant):
    text='请核对实验结果。' if variant=='E' else 'source ref: fixture_source_01。材料：'+TEXT
    return dict(model='tju-llm',temperature=0,max_tokens=500,tool_choice='auto',
        messages=[dict(role='system',content='请调用给定函数返回识别结果。'),dict(role='user',content=text)],
        tools=[dict(type='function',function=dict(name=FUNCTION,description='返回识别结果。',parameters=schemas()[variant]))])


def wire_schema(body):
    """Inspect actual requests PreparedRequest.body bytes, never headers."""
    if isinstance(body,str):body=body.encode('utf-8')
    data=json.loads(body.decode('utf-8'))
    parameters=data['tools'][0]['function']['parameters']
    encoded=json.dumps(parameters,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    return dict(parameters=parameters,schema_sha256=hashlib.sha256(encoded).hexdigest(),
        serialized_body_length=len(body),tool_choice=data['tool_choice'])


def current_schema_preflight():
    """Actual client serialization path, intercepted at send; no live request."""
    import requests
    from unittest.mock import patch
    from src.material_function_transport import function_definition,NAME
    from src.tju_llm_client import call_tju_llm_messages
    captured=[]
    def prepared_post(url,**kwargs):
        request=requests.Session().prepare_request(requests.Request('POST',url,json=kwargs['json'],headers=kwargs['headers']))
        captured.append(wire_schema(request.body))
        reply=requests.Response();reply.status_code=200
        reply._content=json.dumps(dict(choices=[dict(message=dict(tool_calls=[dict(type='function',
            function=dict(name=NAME,arguments='{}'))]),finish_reason='tool_calls')])).encode()
        return reply
    with patch('src.tju_llm_client._require_all_envs',return_value=(ENDPOINT[:-len('/chat/completions')],'offline-placeholder','tju-llm')):
        with patch('src.tju_llm_client.requests.post',prepared_post):
            call_tju_llm_messages([dict(role='user',content=TEXT)],recognition_function=function_definition())
    result=captured[0]
    result['capture_mode']='current caller PreparedRequest before network; historical request bytes unavailable'
    result['actionability']=result['parameters']['properties']['actionability']
    result['actionability_root_required']='actionability' in result['parameters']['required']
    return result


def json_type(value):
    return ('null' if value is None else 'boolean' if type(value) is bool else
        'object' if isinstance(value,dict) else 'array' if isinstance(value,list) else
        'string' if isinstance(value,str) else 'number' if isinstance(value,(int,float)) else 'other')


def examine(data,variant):
    from jsonschema import Draft7Validator
    from src.material_serialization_repair import strict_object
    from src.material_workload import RATES
    choices=data.get('choices') or [];choice=choices[0] if len(choices)==1 else {}
    calls=(choice.get('message') or {}).get('tool_calls') or []
    result=dict(tool_call_count=len(calls),target_call_count=sum(c.get('function',{}).get('name')==FUNCTION for c in calls),
        finish_reason=choice.get('finish_reason'),json_valid=False,schema_valid=False,
        actionability_type='not_observed',schema_errors=[])
    if len(calls)!=1 or calls[0].get('function',{}).get('name')!=FUNCTION:return result
    raw=calls[0]['function'].get('arguments')
    result['arguments_length']=len(raw) if isinstance(raw,str) else None
    try:obj=strict_object(raw)
    except (ValueError,TypeError):return result
    result['json_valid']=True
    result['actionability_type']=json_type(obj['actionability']) if 'actionability' in obj else 'missing'
    # Deliberately no json.loads on a string-valued actionability.
    errors=list(Draft7Validator(schemas()[variant]).iter_errors(obj))
    result['schema_valid']=not errors
    for error in errors:
        result['schema_errors'].append(dict(path='$'+''.join('['+str(p)+']' if isinstance(p,int) else '.'+p for p in error.path),
            rule=error.validator,observed_type=json_type(error.instance)))
    action=obj.get('actionability')
    if isinstance(action,dict):
        result['is_estimatable']=action.get('is_estimatable') if type(action.get('is_estimatable')) is bool else None
        result['present']=action.get('present') if type(action.get('present')) is bool else None
    if variant=='E':
        value=obj.get('feature_type');result['enum_valid']=isinstance(value,str) and value in RATES
        import re
        result['safe_category']=value if isinstance(value,str) and re.fullmatch(r'[a-z_]{1,40}',value) else None
        result['feature_type_json_type']=json_type(value)
    return result


def snapshot_sources():
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'src').glob('*.py'))}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
    DEST.mkdir(parents=True,exist_ok=True)
    audit=current_schema_preflight();matrix=schemas()
    (DEST/'schema-audit.json').write_text(json.dumps(dict(current=audit,matrix=matrix),ensure_ascii=False,indent=2),encoding='utf-8')
    baseline=snapshot_sources()
    (DEST/'source-hashes-before.json').write_text(json.dumps(baseline,indent=2),encoding='utf-8')
    action=audit['actionability'];object_branch=action.get('anyOf',[action])[0]
    print(json.dumps(dict(mode='wire preflight',nullable=action.get('type','anyOf'),
        required=object_branch['required'],variants=list(matrix)),ensure_ascii=False),flush=True)
    if not args.live:return
    dest=DEST/'calls.json'
    if dest.exists():raise RuntimeError('Existing experiment results; no automatic rerun')
    env = []
    pass  # Use the caller environment unchanged.
    os.environ['TJU_LLM_BASE_URL']=ENDPOINT[:-len('/chat/completions')]
    from src.tju_llm_client import _require_all_envs,_build_chat_url
    base,key,model=_require_all_envs();assert _build_chat_url(base)==ENDPOINT and model=='tju-llm'
    import requests
    records=[]
    with requests.Session() as session:
        # Interleave conditions to avoid making variant order a time effect.
        for repeat in range(REPETITIONS):
            for variant in matrix:
                request=requests.Request('POST',ENDPOINT,json=payload(variant),headers={'Authorization':'Bearer '+key})
                prepared=session.prepare_request(request)
                wire=wire_schema(prepared.body)
                assert wire['parameters']==matrix[variant]
                (DEST/('sent-schema-'+variant+'.json')).write_text(json.dumps(wire,ensure_ascii=False,indent=2),encoding='utf-8')
                settings=session.merge_environment_settings(prepared.url,{},None,None,None)
                row=dict(variant=variant,repetition=repeat+1,schema_sha256=wire['schema_sha256'])
                started=time.monotonic()
                try:
                    response=session.send(prepared,timeout=120,**settings)
                    row['http_status']=response.status_code
                    if response.status_code==200:
                        data=response.json();row.update(examine(data,variant))
                        usage=data.get('usage') or {}
                        row['usage']={k:usage.get(k) for k in ('prompt_tokens','completion_tokens','total_tokens')}
                except Exception as exc:row['transport_error_type']=type(exc).__name__
                row['latency_ms']=round((time.monotonic()-started)*1000)
                records.append(row);dest.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(row,ensure_ascii=False),flush=True)
                if row.get('http_status')!=200:return
                time.sleep(2.1)
    assert snapshot_sources()==baseline,'Production source changed during isolated experiment'


if __name__=='__main__':main()
