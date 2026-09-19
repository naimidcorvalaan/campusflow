"""Safe aggregation of a reliability campaign; never infer missing usage."""
import argparse
from collections import Counter
import json
from pathlib import Path


def usage_summary(records):
    def usage(row):
        old=row.get('usage')
        return old if isinstance(old,dict) else dict(
            prompt_tokens=row.get('input_tokens'),completion_tokens=row.get('output_tokens'),
            total_tokens=row.get('total_tokens'))
    values=[usage(row) for row in records]
    return dict(requests=len(records),
        http_statuses=dict(Counter(str(r.get('http_status',r.get('status_code'))) for r in records)),
        usage_known=sum(type(v.get('total_tokens')) is int for v in values),
        input_tokens=sum(v.get('prompt_tokens') or 0 for v in values),
        output_tokens=sum(v.get('completion_tokens') or 0 for v in values),
        known_total_tokens=sum(v.get('total_tokens') or 0 for v in values),
        maximum_input=max([v.get('prompt_tokens') or 0 for v in values] or [0]),
        average_latency_ms=round(sum(r.get('latency_ms') or 0 for r in records)/max(1,len(records)),1),
        repair_requests=sum(bool(r.get('repair')) or any('repair' in str(r.get(k,''))
                            for k in ('request_stage','stage')) for r in records))


def summarize(root):
    batches=[];records=[]
    for path in sorted(root.rglob('requests.json')):
        rows=json.loads(path.read_text(encoding='utf-8'));records.extend(rows)
        batch=dict(path=str(path.parent.relative_to(root)).replace('\\','/'),**usage_summary(rows))
        results=path.with_name('results.json')
        if results.exists():
            value=json.loads(results.read_text(encoding='utf-8'))
            if isinstance(value,list) and value:
                first=value[0]
                if 'fixture' in first:
                    batch.update(kind='recognition_stage',cases=len(value),passed=sum(v['final_pass'] for v in value))
                elif 'steps' in first:
                    gold_path=path.with_name('gold.json')
                    gold=json.loads(gold_path.read_text(encoding='utf-8')) if gold_path.exists() else []
                    planned={item['id']:len(item.get('events',[])) for item in gold}
                    counts=[planned.get(item['id']) for item in value]
                    kind=trajectory_scope(counts)
                    batch.update(kind=kind,cases=len(value),passed=sum(v['success'] for v in value),
                        planned_feedback_steps=sum(count or 0 for count in counts),
                        complete_event_sequences=sum(count is not None and len(v['steps'])==count
                                                     for v,count in zip(value,counts)),
                        feedback_steps=sum(len(v['steps']) for v in value),
                        successful_steps=sum(s['success'] for v in value for s in v['steps']))
                else:
                    batch.update(kind=first.get('evaluation_kind','initial_planning'),cases=len(value),passed=sum(v['success'] for v in value))
            elif isinstance(value,dict) and 'phases' in value:
                batch.update(kind='material_flow',cases=1,phases=len(value['phases']),errors=value['errors'])
        batches.append(batch)
    return dict(batches=batches,**usage_summary(records))


def trajectory_scope(planned_counts):
    """Classify by frozen inputs, never by how early execution failed."""
    if not planned_counts or any(count is None for count in planned_counts):
        return 'trajectory_scope_unknown'
    if all(count==0 for count in planned_counts):
        return 'initial_planning'
    if all(count>=3 for count in planned_counts):
        return 'continuous_trajectory'
    return 'feedback_prefix'


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);a=p.parse_args();result=summarize(a.root)
    (a.root/'campaign-summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='batches'}))
    for b in result['batches']:print(json.dumps(b,ensure_ascii=False))
