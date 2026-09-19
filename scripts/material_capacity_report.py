"""Aggregate actual capacity attempts, preserving failures and safe facts."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re


def summarize(root):
    requests=[];materials=[]
    for path in sorted(root.glob('*/*/results.json')):
        row=json.loads(path.read_text(encoding='utf-8'))
        if not path.with_name('requests.json').exists():continue
        calls=json.loads(path.with_name('requests.json').read_text(encoding='utf-8'));requests.extend(calls)
        manifest_path=path.parent.parent/'materials.json'
        manifest=json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else []
        gold=next((m for m in manifest if m['material_id']==row['material_id']),{})
        phases=[];events=[]
        for p in row['phases']:
            events.extend(p.get('estimate_diagnostics',[]))
            if p['phase']=='pre_adoption':continue
            draft=p.get('result') or {};entries=draft.get('estimates',[]);details=[]
            wanted={1,2,3,4,5} if p['phase']=='initial' else {3,4,5}
            for e in entries:
                v=e['estimate'];ledger=e.get('effort_ledger') or {};claims=e.get('quantity_claims',[])
                scope=set('一二三四五'.index(n)+1 for n in re.findall(r'第([一二三四五])部分',v['short_scope']))
                body=any(c.get('owner')=='deliverable_body' and c.get('metric')=='page_count' and c.get('qualifier')=='range' and c.get('minimum')==gold.get('minimum_pages',4) and c.get('maximum')==gold.get('maximum_pages',6) for c in claims)
                discussion=any(c.get('owner')=='subtask_discussion' and c.get('metric')=='word_count' and c.get('qualifier')=='approximately' and c.get('minimum')==gold.get('discussion_words',600) for c in claims)
                contract=e.get('final_contract') or {};facts=contract.get('material_facts',{})
                page=facts.get('page_count',{}).get('value')
                source_ok=row['file_type']!='pdf' or page==row['pages']
                core=sum(x['minutes'] for x in ledger.get('core',[])) if ledger else None
                adjust=sum(x['minutes'] for x in ledger.get('adjustments',[]) if not x.get('included_in')) if ledger else None
                valid=bool(contract and e.get('final_contract_seal') and e.get('final_validation_errors')==[])
                details.append(dict(origin=e.get('origin'),path=e.get('adoption_path'),suggested=v['recommended_minutes'],minimum=v['focused_minutes_min'],maximum=v['focused_minutes_max'],
                    scope_complete=scope==wanted,requirements_preserved=body and discussion,source_page_correct=source_ok,
                    confirmation_fields=e.get('confirmation_fields'),final_validation_pass=valid,
                    core=core,adjustment=adjust,gap=v['recommended_minutes']-core-adjust if ledger else None,
                    claims=claims,all_guards_pass=valid and scope==wanted and body and discussion and source_ok and e.get('confirmation_fields')==[]))
            phases.append(dict(phase=p['phase'],handler_success=p.get('handler_success'),entries=details))
        adoption=next((p for p in row['phases'] if p['phase']=='pre_adoption'),{})
        outcomes=adoption.get('outcomes',[])
        manual=bool(outcomes and all(v.get('prepared') and v.get('manual_saved') for v in outcomes) and adoption.get('plan_published') is False)
        pass_all=bool(not row['errors'] and len(phases)==2 and all(p['handler_success'] and p['entries'] and all(e['all_guards_pass'] for e in p['entries']) for p in phases) and manual)
        formal_errors=Counter(e['code'] for e in events if e.get('validator')=='formal_validation' and e.get('schema_valid') is False)
        materials.append(dict(batch=path.parent.parent.name,id=row['material_id'],file_type=row['file_type'],chars=row['extracted_text_chars'],pages=row['pages'],bytes=row['file_bytes'],
            requests=len(calls),tokens=sum(c.get('total_tokens') or 0 for c in calls),max_input=max([c.get('input_tokens') or 0 for c in calls] or [0]),
            http_statuses=dict(Counter(str(c.get('http_status')) for c in calls)),phases=phases,errors=row['errors'],
            manual_adoption=manual,pass_all=pass_all,repairs=sum(bool(c.get('repair')) for c in calls),
            # A failed re-estimate intentionally retains the last published
            # draft. Its origin is not a second fallback/recovery publication.
            fallback=sum(e['origin']=='local_workload' for p in phases if p['handler_success'] for e in p['entries']),
            full_source_occurrences=sum(c.get('full_material_occurrences',0) for c in calls),
            recovery=sum(e['path']=='recovery' or e['origin']=='model_recovery' for p in phases if p['handler_success'] for e in p['entries']),
            serialization_repairs=sum('serialization' in str(c.get('stage','')) for c in calls),
            formal_errors=dict(formal_errors),elapsed_ms=row.get('total_elapsed_ms')))
    return dict(materials=materials,requests=len(requests),http_statuses=dict(Counter(str(c.get('http_status')) for c in requests)),
        input_tokens=sum(c.get('input_tokens') or 0 for c in requests),output_tokens=sum(c.get('output_tokens') or 0 for c in requests),
        total_tokens=sum(c.get('total_tokens') or 0 for c in requests),usage_known=sum(c.get('total_tokens') is not None for c in requests),
        maximum_input=max([c.get('input_tokens') or 0 for c in requests] or [0]),
        average_latency_ms=round(sum(c.get('latency_ms') or 0 for c in requests)/max(len(requests),1),1))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);a=p.parse_args();report=summarize(a.root)
    (a.root/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='materials'}))
    for m in report['materials']:print(json.dumps({k:v for k,v in m.items() if k not in ('phases',)},ensure_ascii=False))
