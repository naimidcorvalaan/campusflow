"""Continuous production sessions, independent temporal/state gold, no real plan.

Each trace has an initial intake and five successive user events. Failed events
remain failures; later events run against the actual retained state, not a reset.
Only public synthetic prompts and safe structural observations are recorded.
"""
import argparse
from copy import deepcopy
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.reliability_live import isolated_environment,write
from scripts.reliability_planning import harness
from scripts.fc_fixtures import ENDPOINT


def cases(count=100):
    rows=[]
    for i in range(count):
        campus='beiyangyuan' if i%2==0 else 'weijinlu'
        place='北洋园图书馆' if i%2==0 else '卫津路图书馆'
        building='第31教学楼' if i%2==0 else '第9教学楼'
        a=['高数作业','概率论作业','数据结构复习','课程论文批注','线代练习'][i//20%5]
        b='英语单词';c='实验数据核对';d='中文短文校对'
        duration=40+(i//20)*4;extra=12+(i//20)*2;mode=(i//2)%10
        initial=('现在14:00，我在'+place+'。'+a+str(duration)+'分钟、'+b+'20分钟、'+c+'15分钟，'
            '三项都尚未开始，均在图书馆做，今天18:30前完成。16:00到17:00在'+building+'上固定课程，'
            +('骑行' if mode%2 else '步行')+'过去，留出课前到楼与准备时间。')
        if mode in (4,9):initial+='17:40到18:00在同一教学楼有固定组会。'
        def event(kind,text,**expect):return dict(kind=kind,text=text,**expect)
        partial=event('partial','现在14:10，'+a+'已经实际做了10分钟，尚未完成。其他任务没有开始。',target=a,done=10,clock='14:10')
        complete=event('complete','现在14:50，'+a+'已经全部完成，其他任务进度没有变化。',target=a,completed=True,clock='14:50')
        insert=event('insert','临时增加'+str(extra)+'分钟的'+d+'，尚未开始，在图书馆完成。其他事项不变。',target=d,total=extra)
        cancel=event('cancel','取消今天的'+c+'，不再做这项任务。其他进度不变。',target=c)
        unchanged=event('unchanged','刚才没有执行任何任务，请保持已经记录的实际进度，继续安排剩余事项。')
        order=event('order','接下来先完成'+b+'，然后再做'+a+'，其他任务不变。',first=b,second=a)
        priority=event('priority','提高'+a+'的优先级，其他进度和时长不变。',target=a)
        deadline=event('deadline',a+'今天必须15:30前完成，其他时间和实际进度不变。',target=a,deadline='15:30')
        location=event('location','现在14:30，我已经到达'+building+'，请从这里安排后续移动。任务仍在图书馆做，实际进度不变。',location=building,clock='14:30')
        slow=event('slower',a+'实际已经做了10分钟，但仍需要50分钟才能完成。其他任务没有开始。',target=a,done=10,remaining=50)
        fast=event('faster',a+'已实际做了10分钟，剩余只需15分钟，其他任务进度不变。',target=a,done=10,remaining=15)
        continuous=event('continuous',a+'剩余工作必须一次连续做完，不要拆分，实际进度不变。',target=a)
        preview=event('preview','如果先完成'+b+'再做'+a+'，会怎样？暂不采用。',first=b,second=a)
        apply=event('preview_apply','如果先完成'+b+'再做'+a+'，会怎样？',first=b,second=a)
        # All later prompts refer to actual persistent identities; no scripted
        # task state is injected into the production session.
        plans=[
            [partial,insert,complete,unchanged,cancel],
            [unchanged,priority,deadline,cancel,unchanged],
            [partial,slow,location,cancel,unchanged],
            [insert,order,continuous,partial,complete],
            [deadline,partial,insert,cancel,location],
            [preview,unchanged,apply,partial,complete],
            [partial,fast,insert,complete,unchanged],
            [cancel,insert,priority,partial,unchanged],
            [order,partial,location,insert,unchanged],
            [preview,apply,continuous,partial,cancel],
        ]
        rows.append(dict(id='trace-{:03d}'.format(i+1),campus=campus,initial=initial,
            initial_tasks={a:duration,b:20,c:15},commitments=2 if mode in (4,9) else 1,
            fixed_times=[['16:00','17:00']]+([['17:40','18:00']] if mode in (4,9) else []),
            events=deepcopy(plans[mode])))
    return rows


def snapshot(bundle):
    return {t.task_ref:dict(title=t.title,total=t.total_minutes,done=t.completed_minutes,
        remaining=t.remaining_minutes,state=t.state.value,source=getattr(t.total_source,'value',None)) for t in bundle.state.tasks}


def safe_agent_trace(bundle):
    trace=getattr(getattr(bundle,'agent_intelligence',None),'trace',None)
    return [dict(stage=r.stage,success=r.success,repair=r.repair,fallback=r.fallback)
            for r in getattr(trace,'records',())]


def fixed_snapshot(state, refs=None):
    # Generated travel reservations may change with the user's current place.
    # Compare the user's original fixed identities and their real time facts.
    from src.p3_class_prep import class_arrival_lead_minutes
    return {c.commitment_ref:dict(start=c.starts_at.isoformat() if c.starts_at else None,
        end=c.ends_at.isoformat() if c.ends_at else None,availability=c.availability_during.value,
        kind=c.commitment_kind,arrival_lead=class_arrival_lead_minutes(c))
        for c in state.commitments if refs is None or c.commitment_ref in refs}


def initial_fixed_times_match(fixed, expected):
    actual=Counter((v['start'][11:16] if v.get('start') else None,
                   v['end'][11:16] if v.get('end') else None) for v in fixed.values())
    return actual==Counter(tuple(v) for v in expected)


def continuity_errors(before,after,event):
    errors=[];target=event.get('target','')
    for ref,old in before.items():
        new=after.get(ref)
        if new is None:errors.append('old_identity_disappeared');continue
        if new['title']!=old['title']:errors.append('identity_title_changed')
        if old['state'] in ('completed','abandoned') and new['state']!=old['state']:errors.append('terminal_task_resurrected')
        if new['done']<old['done']:errors.append('actual_progress_lost')
        if not (target and target in old['title'] and event['kind'] in ('partial','slower','faster','complete')) and new['done']!=old['done']:
            errors.append('unreported_progress_changed')
        if event['kind'] not in ('slower','faster') and new['total']!=old['total']:errors.append('unrequested_duration_changed')
        if new['source']!=old['source']:errors.append('duration_provenance_changed')
    if event['kind']=='insert':
        fresh=set(after)-set(before)
        if len(fresh)!=1 or not any(target in after[r]['title'] and after[r]['total']==event['total'] and after[r]['done']==0 for r in fresh):
            errors.append('insert_identity_or_duration')
    elif set(after)-set(before):errors.append('unexpected_new_task')
    if target and event['kind'] not in ('priority','continuous','deadline'):
        matches=[t for t in after.values() if target in t['title']]
        if len(matches)!=1:errors.append('target_identity_missing')
        else:
            t=matches[0]
            if 'done' in event and t['done']!=event['done']:errors.append('explicit_progress_mismatch')
            if 'remaining' in event and t['remaining']!=event['remaining']:errors.append('explicit_remaining_mismatch')
            if event.get('completed') and t['state']!='completed':errors.append('completion_not_applied')
            if event['kind']=='cancel' and t['state']!='abandoned':errors.append('cancellation_not_applied')
    return sorted(set(errors))


def audit_bundle(bundle,map_data):
    from src.p2_session import build_live_final_turn
    from src.p3_route_planner import final_plan_overlap_errors
    from src.p4_execution_movement import execution_timeline
    errors=list(final_plan_overlap_errors(bundle.state,bundle.result.allocation_plan,bundle.movement_blocks))
    build_live_final_turn(bundle.turn,map_data,version=bundle.version,extra_questions=bundle.extra_questions,feedback_decision=bundle.feedback_decision)
    tasks=snapshot(bundle);events=execution_timeline(bundle.state,bundle.result.allocation_plan,bundle.execution_context,map_data)
    for allocation in bundle.result.allocation_plan.allocations:
        if allocation.task_ref not in tasks:errors.append('unknown_allocated_identity')
        elif tasks[allocation.task_ref]['state'] in ('completed','abandoned'):errors.append('terminal_task_scheduled')
    from src.p4_execution_enrichment import latest_end_overrides
    limits=latest_end_overrides(bundle.execution_context,bundle.state)
    if any(e.activity_type=='task' and e.activity_ref in limits and e.ends_at>limits[e.activity_ref] for e in events):errors.append('canonical_deadline_violation')
    return errors,events


def planning_failure(exc):
    """Keep known planning invariant names, never exception free text."""
    from src.material_formal_validation import feedback
    result = feedback(exc)
    allowed = {
        'known location transition has no reserved route:': 'reserved_route_required',
        'location-bound task continues after departure:': 'location_before_departure',
        'movement activity ref is stale:': 'movement_identity_current',
        'task starts before explicit boundary:': 'task_earliest_start',
        'task ends after explicit boundary:': 'task_latest_end',
        'candidate plan violates latest feedback decision': 'latest_feedback_required',
        'meal_default allocation must be': 'meal_allocation_bounds',
        'meal_default duration must be': 'meal_duration_contract',
    }
    codes = [code for prefix, code in allowed.items() if prefix in str(exc)]
    if codes:
        result.update(expected='formal planning publication invariants',
                      planning_invariants=codes, field_path='$.candidate_plan')
    return result


def run(selected,destination):
    import requests
    from src.p2_live_main import make_live_session,_handle_intake_submit
    from src.p2_session import load_live_final_turn,MOVEMENT_DATA_KEY
    from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
    from src.p2_tju_live_adapter import TJUP2CallAdapter
    from src.material_formal_validation import feedback
    destination.mkdir(parents=True,exist_ok=False);write(destination/'gold.json',selected)
    module=harness();results=[]
    with isolated_environment():
        module.OUT=destination;original=requests.sessions.Session.request;meter=module.Meter(ENDPOINT,original)
        requests.sessions.Session.request=lambda session,method,url,**kw:meter.request(session,method,url,**kw)
        try:
            for case in selected:
                start=len(meter.records);meter.case_start=start;meter.scenario_id=case['id']+'/initial'
                store={};map_data=DEFAULT_CAMPUS_REGISTRY.get_campus_map(case['campus'])
                session=make_live_session(store,TJUP2CallAdapter(),map_data=map_data,campus_id=case['campus'],companion_enabled=True,agent_intelligence_enabled=True)
                unified_observations=[];original_handler=session.unified_handler
                def observe_unified(*args):
                    value=original_handler(*args);result=getattr(value,'result',None)
                    unified_observations.append(dict(understood=value.understood,applied=value.applied,
                        task_count=len(result.updated_state.tasks) if result else None,
                        question_count=len(result.questions) if result else None,
                        warning_count=len(result.warnings) if result else None))
                    return value
                session.unified_handler=observe_unified
                st=SimpleNamespace(session_state=store,error=lambda *a:None,warning=lambda *a:None,write=lambda *a:None)
                row=dict(id=case['id'],steps=[],initial_pass=False,success=False)
                try:
                    ok=_handle_intake_submit(st,session,(),datetime(2026,9,17,14),case['initial'],map_data,case['campus'],current_location_text='',rerun_after_commit=False)
                    initial=load_live_final_turn(store)
                    if not ok or initial is None:raise ValueError('initial unavailable')
                    issues,_=audit_bundle(initial,map_data);tasks=snapshot(initial)
                    if len(tasks)!=3 or sorted(t['total'] for t in tasks.values())!=sorted(case['initial_tasks'].values()):issues.append('initial_tasks_or_durations')
                    if any(t['done']!=0 for t in tasks.values()):issues.append('planned_progress_is_not_completed')
                    if len(initial.state.commitments)!=case['commitments']:issues.append('initial_fixed_commitment_count')
                    if not initial_fixed_times_match(fixed_snapshot(initial.state),case['fixed_times']):
                        issues.append('initial_fixed_time_fidelity')
                    row.update(initial_pass=not issues,initial_errors=issues,initial_state=tasks,
                               initial_agent_trace=safe_agent_trace(initial))
                    fixed=fixed_snapshot(initial.state)
                    row['initial_fixed']=fixed
                    reference=datetime(2026,9,17,14)
                    for number,event in enumerate(case['events'],1):
                        meter.case_start=len(meter.records);meter.scenario_id=case['id']+'/'+str(number)
                        before=load_live_final_turn(store);step=dict(index=number,kind=event['kind'],errors=[]);unified_observations.clear()
                        try:
                            if event['kind'] in ('preview','preview_apply'):
                                movement=deepcopy(store.get(MOVEMENT_DATA_KEY))
                                preview=session.preview_what_if_atomic(event['text'])
                                if load_live_final_turn(store)!=before:step['errors'].append('preview_polluted_canonical')
                                if store.get(MOVEMENT_DATA_KEY)!=movement:step['errors'].append('preview_polluted_movement')
                                step['preview_status']=preview.status
                                if not preview.can_apply:step['errors'].append('preview_unavailable')
                                if event['kind']=='preview_apply' and preview.can_apply:
                                    count=len(meter.records);session.apply_what_if_preview_atomic()
                                    if count!=len(meter.records):step['errors'].append('adoption_extra_model_call')
                            else:
                                if event.get('clock'):
                                    hour,minute=map(int,event['clock'].split(':'))
                                    reference=reference.replace(hour=hour,minute=minute)
                                session.rebuild_feedback_atomic(event['text'],reference_datetime=reference)
                            after=load_live_final_turn(store)
                            step['errors']+=continuity_errors(snapshot(before),snapshot(after),event)
                            if fixed_snapshot(after.state,set(fixed))!=fixed:step['errors'].append('fixed_commitments_changed')
                            hard,events=audit_bundle(after,map_data);step['errors']+=hard
                            if after.state.now<reference:step['errors'].append('stale_reference_clock')
                            if any(e.activity_type=='task' and e.starts_at<reference for e in events):step['errors'].append('planned_work_in_past')
                            taskmap={t.title:t.task_ref for t in after.state.tasks}
                            if event['kind'] in ('order','preview_apply'):
                                refs=[next((r for title,r in taskmap.items() if event[key] in title),None) for key in ('first','second')]
                                runs=[[e for e in events if e.activity_type=='task' and e.activity_ref==ref] for ref in refs]
                                if not all(runs) or max(e.ends_at for e in runs[0])>min(e.starts_at for e in runs[1]):step['errors'].append('order_not_applied')
                            if event['kind']=='location':
                                location=after.execution_context.current_location.location
                                from src.p3_location_resolver import resolve_location
                                expected=resolve_location(map_data,event['location'],None,None)
                                if location is None or location.node_id!=expected.node_id:step['errors'].append('location_not_applied')
                            if event['kind'] in ('continuous','deadline'):
                                ref=next((r for title,r in taskmap.items() if event['target'] in title),None)
                                runs=[e for e in events if e.activity_type=='task' and e.activity_ref==ref]
                                if event['kind']=='continuous' and len(runs)!=1:step['errors'].append('single_session_not_applied')
                                if event['kind']=='deadline' and (not runs or any(e.ends_at>datetime(2026,9,17,15,30) for e in runs)):step['errors'].append('explicit_deadline_not_applied')
                            step.update(state=snapshot(after),version=after.version,confirmation_questions=len(after.extra_questions),
                                decision=getattr(after.feedback_decision,'intent_type',None),fixed=fixed_snapshot(after.state),
                                location_node=getattr(after.execution_context.current_location.location,'node_id',None),
                                reference=after.state.now.isoformat(),agent_trace=safe_agent_trace(after))
                        except Exception as exc:
                            step['errors'].append('exception');step['failure']=planning_failure(exc)
                            step['old_plan_preserved']=load_live_final_turn(store)==before
                        step.update(success=not step['errors'],requests=len(meter.records)-meter.case_start)
                        step['unified_observations']=list(unified_observations)
                        row['steps'].append(step);write(destination/'inflight.json',row)
                        if meter.circuit:break
                    row['success']=row['initial_pass'] and len(row['steps'])==len(case['events']) and all(s['success'] for s in row['steps'])
                except Exception as exc:row['initial_failure']=planning_failure(exc)
                row['requests']=len(meter.records)-start;results.append(row);write(destination/'results.json',results)
                print(json.dumps(dict(id=case['id'],success=row['success'],steps=len(row['steps']),errors=[(s['index'],s['errors']) for s in row['steps'] if s['errors']]),ensure_ascii=False),flush=True)
                if meter.circuit:break
        finally:requests.sessions.Session.request=original
    summary=dict(executed=len(results),passed=sum(r['success'] for r in results),feedback_steps=sum(len(r['steps']) for r in results),
        successful_feedback_steps=sum(s['success'] for r in results for s in r['steps']),requests=len(meter.records),
        usage={k:sum((r['usage'] or {}).get(k) or 0 for r in meter.records) for k in ('prompt_tokens','completion_tokens','total_tokens')},
        infrastructure=meter.circuit,official_data_unchanged=True,
        planned_feedback_steps=sum(len(c['events']) for c in selected),
        scope='continuous sessions; actual event counts recorded per case; temporary plans only')
    write(destination/'summary.json',summary);print(json.dumps(summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--start',type=int,default=0);p.add_argument('--limit',type=int,default=20)
    a=p.parse_args();run(cases()[a.start:a.start+a.limit],a.output)
