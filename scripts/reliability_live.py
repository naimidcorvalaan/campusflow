"""Isolated, sequential production-recognition evaluation. Safe metadata only.

Uses the real intake/repair chain and stops at the independent estimator boundary.
Independent gold does not feed production repairs. No publication or persistence.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.fc_fixtures import ENDPOINT,NAME,Done
from scripts.reliability_fixtures import recognition_cases


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def official_hashes():
    paths=[ROOT/'.env',ROOT/'.env.dev',ROOT/'.streamlit/secrets.toml']
    for directory in (ROOT/'.campusflow',ROOT/'data'):
        if directory.exists():paths.extend(p for p in directory.rglob('*') if p.is_file())
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}
    # Windows production profiles live outside the checkout. Always observe
    # that directory, independently of the temporary evaluation override.
    local=os.environ.get('LOCALAPPDATA')
    if local:
        directory=Path(local)/'CampusFlow'
        if directory.exists():
            for p in directory.rglob('*'):
                if p.is_file() and (p.name=='model-service.json' or any(token in p.name for token in ('.sqlite','.db'))):
                    hashes['official-profile/'+str(p.relative_to(directory))]=hashlib.sha256(p.read_bytes()).hexdigest()
    return hashes


@contextmanager
def isolated_environment():
    original=dict(os.environ)
    hashes=official_hashes()
    try:
        env = []
        pass  # Use the caller environment unchanged.
        os.environ['TJU_LLM_BASE_URL']=ENDPOINT[:-len('/chat/completions')]
        from src.model_service_config import effective_configuration
        from src.tju_llm_client import _build_chat_url
        config=effective_configuration()
        for key in ('TJU_LLM_BASE_URL','TJU_LLM_API_KEY','TJU_LLM_MODEL'):
            if not os.environ.get(key):os.environ[key]=config[key]
        assert _build_chat_url(os.environ['TJU_LLM_BASE_URL'])==ENDPOINT
        assert os.environ['TJU_LLM_MODEL']=='tju-llm'
        logging.disable(logging.CRITICAL)
        with tempfile.TemporaryDirectory(prefix='campusflow-reliability-') as directory:
            os.environ['CAMPUSFLOW_DATA_DIR']=directory
            yield
    finally:
        assert official_hashes()==hashes,'Official data changed'
        os.environ.clear();os.environ.update(original)


class Meter:
    def __init__(self,destination):
        self.destination=destination;self.records=[];self.case=None;self.last=0
    def __enter__(self):
        import requests
        self.real=requests.sessions.Session.request
        requests.sessions.Session.request=lambda session,method,url,**kw:self.request(session,method,url,**kw)
        return self
    def __exit__(self,*args):
        import requests
        requests.sessions.Session.request=self.real
    def request(self,session,method,url,**kwargs):
        assert url==ENDPOINT and method.upper()=='POST'
        time.sleep(max(0,2.1-(time.monotonic()-self.last)))
        self.last=time.monotonic()
        body=kwargs.get('json',{});messages=body.get('messages',[])
        context={}
        try:context=json.loads(messages[1]['content'])
        except (ValueError,TypeError,IndexError):pass
        row=dict(case=self.case,index=len(self.records)+1,stage=context.get('request_stage','initial'),
                 http_status=None,usage=None,latency_ms=None)
        self.records.append(row)
        try:
            response=self.real(session,method,url,**kwargs)
            row['http_status']=response.status_code
            if response.status_code==200:
                data=response.json();choice=data.get('choices',[{}])[0]
                calls=(choice.get('message') or {}).get('tool_calls') or []
                row.update(finish_reason=choice.get('finish_reason'),tool_call_count=len(calls),
                    target_tool_count=sum(c.get('function',{}).get('name')==NAME for c in calls),
                    usage={k:(data.get('usage') or {}).get(k) for k in ('prompt_tokens','completion_tokens','total_tokens')})
                row['arguments_json_valid']=[]
                for call in calls:
                    try:json.loads(call.get('function',{}).get('arguments'));valid=True
                    except (ValueError,TypeError):valid=False
                    row['arguments_json_valid'].append(valid)
            return response
        except Exception as exc:
            row['exception_type']=type(exc).__name__;raise
        finally:
            row['latency_ms']=round((time.monotonic()-self.last)*1000)
            write(self.destination/'requests.json',self.records)


def audit(raw,draft,gold):
    from scripts.fc_original_ten import audit_candidate
    from src.material_serialization_repair import strict_object
    if gold['expected_is_estimatable']:return audit_candidate(raw,draft,gold)
    from src.material_function_transport import validate_parameters
    from src.material_inbox import parse_extraction
    from src.material_actionability_sources import validate_repaired_action
    from src.material_formal_validation import feedback
    row=dict(json_valid=False,schema_valid=False,parser_valid=False,business_pass=False)
    try:
        obj=strict_object(raw);row['json_valid']=True
        validate_parameters(obj);row['schema_valid']=True
        parsed=parse_extraction(raw,draft,set());row['parser_valid']=True
        validate_repaired_action(raw,draft)
        action=obj.get('actionability')
        row['actionability_type']='null' if action is None else type(action).__name__
        row['is_estimatable']=action.get('is_estimatable') if isinstance(action,dict) else None
        row['actionability_correct']=action is None or action.get('is_estimatable') is False
        row['business_pass']=row['actionability_correct'] and not parsed.items and not obj.get('workload')
        row['primary_failure_layer']=None if row['business_pass'] else 'invented workload/actionability'
    except Exception as exc:row['formal_failure']=feedback(exc)
    return row


def recognition_run(cases,destination):
    from src.material_inbox import MaterialInbox,update_source,extract_material
    from src.p2_tju_live_adapter import TJUP2CallAdapter
    from src.material_estimate_diagnostics import capture_estimate_diagnostics
    from src.material_formal_validation import feedback
    from scripts.fc_original_ten import freeze_hashes
    destination.mkdir(parents=True,exist_ok=False)
    hashes=freeze_hashes();write(destination/'gold.json',cases)
    results=[]
    with isolated_environment(),Meter(destination) as meter:
        adapter=TJUP2CallAdapter()
        assert adapter.recognition_tools
        for case in cases:
            meter.case=case['fixture'];start=len(meter.records)
            draft=update_source(MaterialInbox(),case['text'],'',datetime(2026,9,17,14),'plan','beiyangyuan').draft
            row=dict(fixture=case['fixture'],label=case['label'],attempts=[],production_validated=False)
            def recognize(system,user):
                raw=adapter.agent_caller(system,user)
                row['attempts'].append(audit(raw,draft,case))
                return raw
            def estimation_boundary(*args):
                row['production_validated']=True;row['estimation_requested']=True
                raise Done()
            with capture_estimate_diagnostics() as diagnostics:
                try:
                    result=extract_material(draft,recognize,workload_caller=estimation_boundary)
                    row['production_validated']=True
                    row['final_status']=result.status
                except Done:pass
                except Exception as exc:row['failure']=feedback(exc)
            row['diagnostics']=diagnostics
            row['final_pass']=bool(row['production_validated'] and row['attempts'] and row['attempts'][-1]['business_pass'])
            calls=meter.records[start:];row['requests']=len(calls);row['repair_count']=max(0,len(calls)-1)
            results.append(row);write(destination/'results.json',results)
            print(json.dumps(dict(fixture=row['fixture'],pass_=row['final_pass'],requests=len(calls),
                layer=row['attempts'][-1].get('primary_failure_layer') if row['attempts'] else 'transport'),ensure_ascii=False),flush=True)
            if any(c['http_status']!=200 for c in calls):break
        usage={key:sum((r.get('usage') or {}).get(key) or 0 for r in meter.records) for key in ('prompt_tokens','completion_tokens','total_tokens')}
        summary=dict(planned=len(cases),executed=len(results),passed=sum(r['final_pass'] for r in results),
            requests=len(meter.records),usage=usage,repairs=sum(r['repair_count'] for r in results),
            maximum_input=max(((r.get('usage') or {}).get('prompt_tokens') or 0 for r in meter.records),default=0),
            official_data_unchanged=True,production_recognition=True,gold_driven_repair=False)
    assert freeze_hashes()==hashes
    write(destination/'summary.json',summary);print(json.dumps(summary),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--start',type=int,default=0);parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--run',action='store_true')
    args=parser.parse_args();cases=recognition_cases()[args.start:args.start+args.limit]
    if args.run:recognition_run(cases,args.output)
    else:print(json.dumps(cases,ensure_ascii=False,indent=2))
