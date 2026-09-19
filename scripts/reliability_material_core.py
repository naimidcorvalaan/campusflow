"""Observe production material UI actions with an isolated real SQLite profile.

No product monkeypatches, parser replacement, payload edits or token changes.
Only Session.request is wrapped to observe safe metadata and stop on errors.
"""
import argparse
from collections import Counter
from dataclasses import replace
from datetime import datetime
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

OUT=Path(__file__).resolve().parent
ROOT=OUT.parent
sys.path.insert(0,str(ROOT))
ENDPOINT=__import__('os').environ.get('TJU_LLM_BASE_URL','https://ai.tju.edu.cn/api/v3').rstrip('/')+'/chat/completions'


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def leaves(value):
    if isinstance(value,str):
        try:
            decoded=json.loads(value)
        except (ValueError,TypeError):
            yield value
        else:
            if isinstance(decoded,(dict,list)):
                yield from leaves(decoded)
            else:yield value
    elif isinstance(value,dict):
        for v in value.values():yield from leaves(v)
    elif isinstance(value,list):
        for v in value:yield from leaves(v)


class Meter:
    def __init__(self,real,meta,source,path):
        self.real,self.meta,self.source,self.path=real,meta,source,path
        self.records=[];self.phase='initial';self.failure=None;self.last=0

    def request(self,session,method,url,**kwargs):
        if self.failure:raise RuntimeError('Evaluation stopped after transport failure')
        if method.upper()!='POST' or url!=ENDPOINT:raise RuntimeError('Unexpected provider endpoint')
        if len(self.records)>=18:raise RuntimeError('Unexpected material call count')
        payload=kwargs.get('json',{});messages=payload.get('messages',[])
        content=[m.get('content','') for m in messages]
        system=messages[0].get('content','') if messages else ''
        stage='unknown';repair=False
        if 'campusflow.material-serialization.v1' in system:stage='material_serialization_repair';repair=True
        elif 'campusflow.workload-estimate.v1' in system:stage='workload_estimate'
        elif 'campusflow.task-estimate.v1' in system:stage='task_estimate'
        elif 'campusflow.material-text.v2' in system:stage='material_recognition'
        if stage=='workload_estimate':
            for message in messages[1:]:
                try:request_context=json.loads(message.get('content',''))
                except (ValueError,TypeError):continue
                if isinstance(request_context,dict) and request_context.get('request_stage') in ('estimate_arithmetic_repair','estimate_protocol_repair'):
                    stage='workload_estimate_repair';repair=True
        frame=inspect.currentframe()
        try:
            while frame:
                if frame.f_code.co_name=='run_material_intake' and stage in ('material_recognition','unknown'):
                    stage='material_recognition'
                    prior=frame.f_locals.get('responses',[])
                    repair=bool(prior)
                    if repair:stage='material_'+frame.f_locals.get('repair_kind','repair')
                    break
                frame=frame.f_back
        finally:del frame
        strings=list(leaves(content));text=self.source.text
        # A structure repair appends prose after the original JSON. Count its
        # JSON-escaped source too, without changing the product prompt.
        escaped=json.dumps(text,ensure_ascii=False)[1:-1]
        full=sum(s.count(text)+(s.count(escaped) if escaped!=text else 0) for s in strings) if text else 0
        excerpts=sum(len(s.strip()) for s in strings if len(s.strip())>=40 and s.strip() in text and text not in s)
        record=dict(self.meta,request_index=len(self.records)+1,phase=self.phase,stage=stage,repair=repair,
                    is_reestimate=self.phase=='reestimate',input_tokens=None,output_tokens=None,total_tokens=None,
                    latency_ms=None,http_status=None,failure_type=None,finish_reason=None,
                    original_material_present=bool(full or excerpts),full_material_occurrences=full,
                    original_material_full_chars=full*len(text),material_excerpt_chars_approx=excerpts,
                    prompt_text_chars=sum(len(s) for s in strings),max_output_tokens=payload.get('max_tokens'),
                    timeout_seconds=kwargs.get('timeout'),tls_verification=kwargs.get('verify',True))
        delay=2.1-(time.monotonic()-self.last)
        if delay>0:time.sleep(delay)
        self.last=time.monotonic();self.records.append(record)
        try:
            response=self.real(session,method,url,**kwargs)
            record['http_status']=response.status_code
            if response.status_code!=200:
                self.failure='http_'+str(response.status_code);record['failure_type']=self.failure
            else:
                data=response.json();usage=data.get('usage') or {}
                for dst,src in [('input_tokens','prompt_tokens'),('output_tokens','completion_tokens'),('total_tokens','total_tokens')]:
                    if type(usage.get(src)) is int:record[dst]=usage[src]
                reason=data.get('choices',[{}])[0].get('finish_reason')
                record['finish_reason']=reason if reason in ('stop','length','content_filter','tool_calls') else 'other'
            return response
        except Exception as exc:
            from src.llm_diagnostics import _failure_kind
            record['failure_type']=_failure_kind(exc)[0];self.failure=record['failure_type']
            raise
        finally:
            record['latency_ms']=round((time.monotonic()-self.last)*1000)
            save(self.path,self.records)


def draft_result(draft):
    if draft is None:return None
    from src.material_inbox import item_values
    items=[]
    for item in draft.items:
        value=item_values(item,draft)
        items.append(dict(title=item.title,scope=item.scope,completion=item.completion,
                          minutes=value['minutes'],min_minutes=item.estimate_min_minutes,
                          max_minutes=item.estimate_max_minutes,basis=item.estimate_basis,
                          assumptions=list(item.estimate_assumptions)))
    return dict(status=draft.status,message=draft.message,model_calls=draft.model_calls,
                items=items,estimates=list(draft.estimate_fallbacks),coverage=draft.estimate_coverage,
                coverage_note=draft.coverage_note,diagnostics=draft.diagnostics,
                workload_summary=list(draft.workload_summary))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('material_id');parser.add_argument('--run',action='store_true')
    parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args()
    material=next(m for m in json.loads((OUT/'materials.json').read_text(encoding='utf-8')) if m['material_id']==args.material_id)
    destination=args.output_dir.resolve() if args.output_dir else OUT
    assert destination==OUT or OUT in destination.parents
    result_dir=destination/material['material_id'];result_dir.mkdir(exist_ok=True,parents=True)
    if (result_dir/'requests.json').exists():raise RuntimeError('Existing live records must not be overwritten')
    # Adopt exactly the previously observed normal-app network environment.
    evidence = []
    network=next(x['network'] for x in evidence if x['label']=='normal_app' and 'network' in x)
    for name,value in network.items():
        if value is None:os.environ.pop(name,None)
        else:os.environ[name]=value
    os.environ['TJU_LLM_BASE_URL']=ENDPOINT[:-len('/chat/completions')]
    from src.model_service_config import effective_configuration
    from src.llm_provider import provider_name
    from src.tju_llm_client import _build_chat_url
    values=effective_configuration()
    assert provider_name()=='tju' and _build_chat_url(values['TJU_LLM_BASE_URL'])==ENDPOINT
    assert values['TJU_LLM_MODEL'].strip()=='tju-llm'
    for name in ('TJU_LLM_BASE_URL','TJU_LLM_API_KEY','TJU_LLM_MODEL'):
        if not os.environ.get(name):os.environ[name]=values[name]
    logging.disable(logging.CRITICAL)
    from src.file_material import read_file_material,DOCX_MIME,PDF_MIME
    file=OUT/'materials'/material['file'];mime=DOCX_MIME if material['type']=='docx' else PDF_MIME
    started=time.monotonic();source=read_file_material(file.name,mime,file.read_bytes(),render=True)
    parse_ms=round((time.monotonic()-started)*1000)
    import pypdfium2
    page_file=file if material['type']=='pdf' else file.with_suffix('.pdf')
    doc=pypdfium2.PdfDocument(page_file);page_count=len(doc);doc.close()
    assert page_count==material['expected_pages']
    meta=dict(material_id=material['material_id'],file_type=material['type'],file_bytes=file.stat().st_size,
              pages=page_count,extracted_text_chars=len(source.text),source_type=source.source_type)
    baseline=OUT/material['material_id']/'material-metadata.json'
    if args.output_dir and baseline.exists():
        assert source.digest==json.loads(baseline.read_text(encoding='utf-8'))['sha256']
    save(result_dir/'material-metadata.json',dict(meta,sha256=source.digest,parse_ms=parse_ms,warnings=source.warnings,
                                               source_text_sha256=hashlib.sha256(source.text.encode()).hexdigest()))
    if not args.run:
        print(json.dumps(meta,ensure_ascii=False));return 0
    import requests
    from src.p2_tju_live_adapter import TJUP2CallAdapter
    from src.p2_live_main import make_live_session,LOCAL_PROFILE_STORE_KEY
    from src.local_persistence import LocalProfileStore
    from src.p2_session import load_live_final_turn
    from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
    from src.material_inbox import MaterialInbox,MATERIAL_INBOX_KEY,update_file_source,edited,checked_item,item_values
    from src.material_ui import save_material_edit,handle_material_action
    from src.material_planning import plan_fingerprint
    from src.material_estimate_recovery import minimal_confirmation_mode
    from src.material_estimate_diagnostics import capture_estimate_diagnostics
    adapter=TJUP2CallAdapter();now=datetime(2026,9,15,14)
    original=requests.sessions.Session.request;meter=Meter(original,meta,source,result_dir/'requests.json')
    result=dict(meta,phases=[],errors=[],evaluation_expanded=False)
    beginning=time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix='campusflow-material-token-') as profile:
            os.environ['CAMPUSFLOW_DATA_DIR']=profile
            store={LOCAL_PROFILE_STORE_KEY:LocalProfileStore(Path(profile)/'profile.sqlite3',user_id='material_token_eval')}
            warnings=[];st=SimpleNamespace(session_state=store,warning=warnings.append,error=warnings.append,write=warnings.append)
            campus='beiyangyuan'
            session=make_live_session(store,adapter,map_data=DEFAULT_CAMPUS_REGISTRY.get_campus_map(campus),campus_id=campus,
                                      companion_enabled=True,agent_intelligence_enabled=True)
            requests.sessions.Session.request=lambda s,method,url,**kw:meter.request(s,method,url,**kw)
            for phase,supplement in [('initial',''),('reestimate',material['supplement'])]:
                meter.phase=phase;start=time.monotonic();first=len(meter.records)
                inbox=store.get(MATERIAL_INBOX_KEY,MaterialInbox())
                source=read_file_material(file.name,mime,file.read_bytes(),render=True)
                submitted=update_file_source(inbox,source,'',now,plan_fingerprint(store,campus),campus,supplemental_context=supplement)
                if phase=='initial':assert save_material_edit(st,submitted,now)
                with capture_estimate_diagnostics() as estimate_diagnostics:
                    ok=handle_material_action(st,session,adapter,'extract',now,file_source=source,source_draft=submitted.draft)
                draft=store[MATERIAL_INBOX_KEY].draft
                result['phases'].append(dict(phase=phase,handler_success=ok,elapsed_ms=round((time.monotonic()-start)*1000),
                    request_count=len(meter.records)-first,supplement=supplement,result=draft_result(draft),warnings=list(warnings),
                    estimate_diagnostics=estimate_diagnostics))
                save(result_dir/'results.json',result)
                if meter.failure or not ok or not draft.diagnostics.get('estimate_available') or len(meter.records)==first:
                    result['errors'].append(meter.failure or ('no_observed_request' if len(meter.records)==first else 'estimate_unavailable'));break
            if not result['errors']:
                meter.phase='pre_adoption';start=time.monotonic();first=len(meter.records)
                inbox=store[MATERIAL_INBOX_KEY];draft=inbox.draft;outcome=[]
                if draft.estimate_fallbacks:
                    for entry in tuple(draft.estimate_fallbacks):
                        mode=minimal_confirmation_mode(entry);value=entry['estimate']
                        adopted=min(1440,value['recommended_minutes']+7)
                        if mode=='blocked':
                            # The real estimate card always saves the number
                            # input, even when publishing needs further review.
                            # Record those two capabilities separately.
                            current=store[MATERIAL_INBOX_KEY]
                            updated=replace(current,draft=replace(current.draft,estimate_fallbacks=tuple(
                                dict(v,adopted_minutes=adopted) if v['item_id']==entry['item_id'] else v
                                for v in current.draft.estimate_fallbacks)))
                            saved=save_material_edit(st,updated,now)
                            persisted=next(v for v in store[MATERIAL_INBOX_KEY].draft.estimate_fallbacks if v['item_id']==entry['item_id'])
                            outcome.append(dict(mode=mode,prepared=False,manual_saved=bool(saved and persisted.get('adopted_minutes')==adopted),
                                adopted_minutes=adopted,stored_minutes=persisted.get('adopted_minutes'),
                                confirmation_fields=entry.get('confirmation_fields')));continue
                        ok=handle_material_action(st,session,adapter,'prepare_estimate',now,item_id=entry['item_id'],
                            confirmed_title=value['task_name'],confirmed_minutes=adopted,simple_confirmed=True,
                            confirmed_scope=value['short_scope'])
                        prepared=store[MATERIAL_INBOX_KEY].draft
                        row=next((i for i in prepared.items if i.item_id==entry['item_id']),None)
                        value_now=item_values(row,prepared)['minutes'] if row else None
                        outcome.append(dict(mode=mode,prepared=ok,manual_saved=bool(ok and str(value_now)==str(adopted)),adopted_minutes=adopted,
                                            stored_minutes=value_now))
                else:
                    rows=[]
                    for item in draft.items:
                        if item.kind!='task':rows.append(item);continue
                        current=item_values(item,draft)['minutes']
                        if not current or not str(current).isdigit():
                            outcome.append(dict(prepared=False,reason='no_adoptable_minutes'));rows.append(item);continue
                        recommended=int(current)
                        adopted=min(1440,recommended+7);row=edited(item,minutes=str(adopted),reviewed=True)
                        try:checked=checked_item(row,draft);valid=True
                        except Exception:valid=False
                        outcome.append(dict(prepared=valid,manual_saved=valid,adopted_minutes=adopted,
                                            stored_minutes=item_values(row,draft)['minutes']));rows.append(row)
                    assert save_material_edit(st,replace(inbox,draft=replace(draft,items=tuple(rows))),now)
                result['phases'].append(dict(phase='pre_adoption',request_count=len(meter.records)-first,
                    elapsed_ms=round((time.monotonic()-start)*1000),outcomes=outcome,plan_published=load_live_final_turn(store) is not None))
            assert load_live_final_turn(store) is None
            result['temporary_profile_only']=True
    except Exception as exc:
        result['errors'].append(type(exc).__name__)
    finally:
        requests.sessions.Session.request=original
        result['total_elapsed_ms']=round((time.monotonic()-beginning)*1000)
        result['model_requests']=len(meter.records)
        for key in ('input_tokens','output_tokens','total_tokens'):
            result[key]=sum(r[key] or 0 for r in meter.records)
        result['usage_coverage']=sum(r['total_tokens'] is not None for r in meter.records)
        save(result_dir/'results.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='phases'},ensure_ascii=False,indent=2))
    return 1 if result['errors'] else 0


if __name__=='__main__':raise SystemExit(main())
