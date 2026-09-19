"""Serial live TJU smoke evaluation through the production planning handler.

No pytest fixtures, fake responses, private profiles, or alternative providers.
Only synthetic campus scenarios and allowlisted response metadata are saved.
"""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent


def meal_starts_in_window(meals, start, end):
    """Arrival/start wording is not a deadline for finishing the whole meal.

    Matches the formal separation in preferred_start_overrides and
    latest_end_overrides; genuine finish deadlines are audited separately.
    """
    lower=datetime.strptime(start,'%H:%M').time()
    upper=datetime.strptime(end,'%H:%M').time()
    return bool(meals) and all(lower<=e.starts_at.time()<=upper for e in meals)


def scenarios():
    cases = [
        ('single_task', 'beiyangyuan', '现在14:00，我在北洋园图书馆。今天只需完成30分钟的高数习题，尚未开始，17:00之前做完；不需要移动。', 1, 0, [30], {}, False),
        ('multiple_tasks', 'weijinlu', '现在14:00，我在卫津路图书馆。尚未开始的任务有：写英语短文40分钟、复习数据结构50分钟、背单词20分钟，今天17:30前完成，都在图书馆做。', 3, 0, [20,40,50], {}, False),
        ('fixed_course', 'beiyangyuan', '现在14:00，我已经在北洋园第31教学楼自习。14:50到16:20在这里上高数课，不能占用。先做30分钟的高数习题，尚未开始，课前完成。', 1, 1, [30], {}, False),
        ('multiple_commitments', 'weijinlu', '现在14:00，人在卫津路第9教学楼。15:00到16:00在本楼上课，17:00到17:30在本楼开组会，这两项固定不变。还有60分钟的概率论作业没开始，可拆分，18:00前完成。', 1, 2, [60], {}, False),
        ('beiyangyuan_route', 'beiyangyuan', '现在14:00，人在北洋园图书馆。先在图书馆做45分钟的物理习题，尚未开始；16:00到17:30在第31教学楼上物理课，步行过去并留出课前准备时间。', 1, 1, [45], {'travel_mode':'walk'}, True),
        ('weijinlu_route', 'weijinlu', '现在14:00，人在卫津路图书馆。先在图书馆写45分钟的实验报告，尚未开始；16:00到17:30在第9教学楼上课，骑车过去并留出进楼准备时间。', 1, 1, [45], {'travel_mode':'bike'}, True),
        ('dinner_window', 'beiyangyuan', '现在16:00，人在北洋园图书馆。还有60分钟的计组复习没开始，在图书馆完成。晚饭要吃30分钟，17:30到18:30之间到北洋园学一食堂吃，步行过去，19:00前结束今天的安排。', 2, 0, [30,60], {'dinner_start':'17:30','dinner_end':'18:30'}, True),
        ('insufficient_time', 'weijinlu', '现在14:00，人在卫津路第9教学楼。14:40到17:00在本楼上课，时间不能改。课前想做还没开始的90分钟高数练习，可以只做一部分，不能占用上课时间。', 1, 1, [90], {}, False),
        ('explicit_order', 'beiyangyuan', '现在14:00，人在北洋园图书馆。今天17:00前先读30分钟课程论文，再用45分钟写摘要，两项都没开始。摘要必须一次连续写完，不要拆分。', 2, 0, [30,45], {'learning_rhythm':'fewer_switches'}, False),
        ('personal_preferences', 'weijinlu', '现在14:00，我在卫津路图书馆。还没开始的任务：60分钟的算法复习、30分钟的英语听力，都在图书馆做，17:30前完成。请参考已保存的学习习惯安排。', 2, 0, [30,60], {'learning_rhythm':'rest_breaks','default_note':'连续专注较久后希望短休息，下午先做需要完整注意力的学习。'}, False),
    ]
    return [dict(scenario_id='smoke-{:02d}'.format(i+1), category=c[0], campus=c[1],
                 input_summary=c[2], expected_tasks=c[3], expected_commitments=c[4],
                 expected_minutes=c[5], settings=c[6], route_required=c[7])
            for i,c in enumerate(cases)]


def save(name, value):
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


class CircuitOpen(RuntimeError):
    pass


class Meter:
    def __init__(self, endpoint, real_request):
        self.url = endpoint
        self.real_request = real_request
        self.records = []
        self.last_started = 0.0
        self.circuit = None
        self.case_start = 0
        self.scenario_id = None

    def request(self, session, method, url, **kwargs):
        if self.circuit:
            raise CircuitOpen('Evaluation circuit is open')
        if url != self.url or method.upper() != 'POST':
            raise RuntimeError('Unexpected evaluation endpoint or method')
        if len(self.records)-self.case_start >= 24:
            self.circuit = 'evaluation_call_cap'
            raise CircuitOpen('Evaluation request cap reached')
        delay = 2.1-(time.monotonic()-self.last_started)
        if delay > 0:
            time.sleep(delay)
        self.last_started = time.monotonic()
        record = dict(scenario_id=self.scenario_id, request_index=len(self.records)+1,
                      status_code=None, response_received=False, failure_type=None,
                      latency_ms=None, usage=None)
        record['stage'] = 'other'
        record['repair'] = False
        frame = inspect.currentframe()
        try:
            while frame is not None:
                if frame.f_code.co_name == '_one_call' and frame.f_code.co_filename.endswith('p5_agent_runtime.py'):
                    record['stage'] = frame.f_locals['stage']
                    record['repair'] = bool(frame.f_locals['repair'])
                    break
                frame = frame.f_back
        finally:
            del frame
        if record['stage'] == 'other':
            messages = kwargs.get('json', {}).get('messages', [])
            system = messages[0].get('content', '') if messages else ''
            for marker, stage in (
                ('Semantic Linker', 'semantic_linker'), ('Raw Event Extractor', 'raw_event_extractor'),
                ('Initial Intake Semantic Auditor', 'intake_auditor'),
                ('p2.day-intake', 'day_intake'), ('p3.location-resolution', 'location_resolver'),
                ('p4.feedback-decision-review', 'feedback_decision_review'),
                ('p4.feedback-decision', 'feedback_decision'),
                ('p3.unified-feedback', 'unified_feedback'),
                ('p3.travel-time', 'travel_time'), ('p2.day-plan-intent', 'day_plan_intent'),
                ('p2.day-review', 'legacy_review'),
            ):
                if marker in system:
                    record['stage'] = stage
                    break
        # Formal repair contexts identify a bounded retry even when normal
        # and repair deliberately share exactly the same system contract.
        messages = kwargs.get('json', {}).get('messages', [])
        if len(messages) > 1 and isinstance(messages[1].get('content'), str):
            try:
                request_context = json.loads(messages[1]['content'])
            except (ValueError, TypeError):
                request_context = None
            if isinstance(request_context, dict):
                request_stage = request_context.get('request_stage')
                if request_stage in ('day_intake_repair', 'unified_feedback_repair'):
                    record['request_stage'] = request_stage
                    record['repair'] = True
        self.records.append(record)
        # Observe the production request without changing timeout, proxies,
        # redirects, certificate verification, payload or provider routing.
        try:
            response = self.real_request(session, method, url, **kwargs)
            record['status_code'] = response.status_code
            record['response_received'] = True
            if response.status_code != 200:
                record['failure_type'] = 'http_{}'.format(response.status_code)
                self.circuit = record['failure_type']
            else:
                try:
                    envelope = response.json()
                    usage = envelope.get('usage')
                    reason = envelope.get('choices', [{}])[0].get('finish_reason')
                    record['finish_reason'] = reason if reason in ('stop','length','content_filter') else 'other'
                    if record['stage'] == 'situation_analyst':
                        record['structure'] = situation_structure_summary(
                            envelope.get('choices', [{}])[0].get('message', {}).get('content', '')
                        )
                    if record['stage'] == 'copy_fact_checker':
                        from src.p2_agentic_parser import extract_json_object
                        try:
                            parsed_copy = extract_json_object(envelope.get('choices', [{}])[0].get('message', {}).get('content', ''))
                            record['copy_verdict'] = {
                                'safe':parsed_copy.get('safe') if type(parsed_copy.get('safe')) is bool else None,
                                'has_correction':isinstance(parsed_copy.get('corrected_copy'),dict),
                            }
                        except Exception:
                            record['copy_verdict'] = {'json_object':False}
                    if record['stage'] == 'intake_auditor':
                        from src.p2_agentic_parser import extract_json_object
                        try:
                            parsed_audit = extract_json_object(envelope.get('choices', [{}])[0].get('message', {}).get('content', ''))
                            decision = parsed_audit.get('decision')
                            record['intake_audit_verdict'] = decision if decision in ('approve','repair') else 'invalid'
                        except Exception:
                            record['intake_audit_verdict'] = 'invalid_json'
                    if record['stage'] in ('feedback_decision','feedback_decision_review','unified_feedback'):
                        from src.p2_agentic_parser import extract_json_object
                        try:
                            value=extract_json_object(envelope.get('choices',[{}])[0].get('message',{}).get('content',''))
                            from src.p4_feedback_decision import _INTENT_TYPES
                            record['feedback_shape']=dict(intent_type=value.get('intent_type') if value.get('intent_type') in _INTENT_TYPES else None,
                                decision=value.get('decision') if value.get('decision') in ('approve','repair','reject') else None,
                                task_update_count=len(value.get('task_updates',[])) if isinstance(value.get('task_updates',[]),list) else None,
                                new_task_count=sum(isinstance(r,dict) and r.get('target_task_ref') is None and bool(r.get('new_task_title'))
                                    for r in value.get('task_updates',[])) if isinstance(value.get('task_updates',[]),list) else None)
                            record['feedback_shape']['question_count']=len(value.get('questions',[])) if isinstance(value.get('questions',[]),list) else None
                            record['feedback_shape']['lifecycle_ref_counts']={
                                k:len(value[k]) for k in ('completed_task_refs','cancelled_task_refs','postponed_task_refs','restored_task_refs')
                                if isinstance(value.get(k),list)}
                            record['feedback_shape']['reason_length']=len(value['reason']) if isinstance(value.get('reason'),str) else None
                            record['feedback_shape']['target_ref_count']=len(value.get('target_task_refs',[])) if isinstance(value.get('target_task_refs',[]),list) else None
                            record['feedback_shape']['reason_contract_fields']=[name for name in ('task_ref','target_task_refs','mixed','completed_minutes','remaining_minutes','progress_delta_minutes','set_total_minutes','repaired_decision') if name in (value.get('reason') or '')]
                            record['feedback_shape']['task_numeric_updates']=[
                                {k:r[k] for k in ('set_total_minutes','progress_delta_minutes','reported_total_minutes','reported_completed_minutes','reported_remaining_minutes','actual_completed_minutes','remaining_minutes','total_minutes') if k in r and (r[k] is None or type(r[k]) is int)}
                                for r in value.get('task_updates',[]) if isinstance(r,dict)] if isinstance(value.get('task_updates'),list) else []
                        except Exception:record['feedback_shape']={'json_object':False}
                    if isinstance(usage, dict):
                        record['usage'] = {k:usage[k] for k in ('prompt_tokens','completion_tokens','total_tokens')
                                           if type(usage.get(k)) is int and usage[k] >= 0}
                except (ValueError, AttributeError):
                    pass
            return response
        except Exception as exc:
            from src.llm_diagnostics import _failure_kind
            record['failure_type'] = _failure_kind(exc)[0]
            self.circuit = record['failure_type']
            raise
        finally:
            record['latency_ms'] = round((time.monotonic()-self.last_started)*1000)
            save('requests.json', self.records)


def situation_structure_summary(raw):
    """Observe only known schema keys/types; never save output values."""
    from src.p2_agentic_parser import extract_json_object
    from src.p5_situation_analyst import situation_output_schema, SITUATION_SCHEMA_VERSION
    def kind(value):
        return ('null' if value is None else 'boolean' if isinstance(value, bool) else
                'string' if isinstance(value, str) else 'array' if isinstance(value, list) else
                'object' if isinstance(value, dict) else 'number')
    try:
        payload = extract_json_object(raw)
    except Exception:
        return {'json_object': False}
    if not isinstance(payload, dict):
        return {'json_object': False, 'root_type': kind(payload)}
    known = situation_output_schema(SimpleNamespace(active_tasks=()))['properties']
    fields = {}
    for name in known:
        if name not in payload:
            fields[name] = {'type': 'missing'}
            continue
        value = payload[name]
        fields[name] = {'type': kind(value)}
        if isinstance(value, list):
            fields[name]['item_types'] = dict(Counter(kind(item) for item in value))
    return {'json_object': True, 'schema_version_matches': payload.get('schema_version') == SITUATION_SCHEMA_VERSION,
            'fields': fields, 'extra_field_count': len(set(payload)-set(known))}


def check_bundle(bundle, case, map_data):
    from src.p2_session import build_live_final_turn
    from src.p3_route_planner import final_plan_overlap_errors
    from src.p4_execution_movement import execution_timeline
    state, plan = bundle.state, bundle.result.allocation_plan
    errors = list(final_plan_overlap_errors(state, plan, bundle.movement_blocks))
    build_live_final_turn(bundle.turn, map_data, version=bundle.version,
                          extra_questions=bundle.extra_questions,
                          feedback_decision=bundle.feedback_decision)
    refs = [t.task_ref for t in state.tasks]
    if len(set(refs)) != len(refs): errors.append('duplicate_task_ref')
    if any(a.task_ref not in refs for a in plan.allocations): errors.append('unknown_allocated_task_ref')
    if any(t.completed_minutes != 0 for t in state.tasks): errors.append('planned_work_marked_completed')
    if len(state.tasks) != case['expected_tasks']: errors.append('unexpected_task_count')
    if len(state.commitments) != case['expected_commitments']: errors.append('unexpected_commitment_count')
    if sorted(t.total_minutes or 0 for t in state.tasks) != case['expected_minutes']:
        errors.append('explicit_minutes_not_preserved')
    if case['route_required'] and not bundle.movement_blocks: errors.append('required_route_missing')
    events = execution_timeline(state, plan, bundle.execution_context, map_data)
    from scripts.evaluation_deadlines import DeadlineExpectation, deadline_evaluation_errors
    # Fixture annotations, independent of whatever deadline the model extracts.
    # This changes no scenario input, provider/parser, or production behavior.
    expectations = {
        'fixed_course': [DeadlineExpectation('day_task_001', 'day_commitment_001',require_complete=case.get('expected_complete',True))],
        'multiple_commitments': [DeadlineExpectation('day_task_001', latest_finish=state.now.replace(hour=18, minute=0))],
        'explicit_order': [DeadlineExpectation(ref, latest_finish=state.now.replace(hour=17, minute=0))
                           for ref in ('day_task_001', 'day_task_002')],
        'personal_preferences': [DeadlineExpectation(ref, latest_finish=state.now.replace(hour=17, minute=30))
                                 for ref in ('day_task_001', 'day_task_002')],
    }.get(case['category'], [])
    errors.extend(deadline_evaluation_errors(state, events, bundle.movement_blocks, expectations))
    if case['category'] == 'insufficient_time':
        # The input forbids occupying class time, not continuing after class.
        # Do not confuse all-day planned minutes with pre-class capacity.
        for event in events:
            if event.activity_type != 'task':
                continue
            for commitment in state.commitments:
                if (commitment.starts_at and commitment.ends_at
                        and event.starts_at < commitment.ends_at
                        and commitment.starts_at < event.ends_at):
                    errors.append('fixed_course_overlap')
    if case['category'] == 'dinner_window':
        meal_refs = {t.task_ref for t in state.tasks if '晚饭' in t.title or '晚餐' in t.title}
        meals = [e for e in events if e.activity_type=='task' and e.activity_ref in meal_refs]
        if not meal_starts_in_window(meals,'17:30','18:30'):
            errors.append('explicit_dinner_arrival_window_violated')
    if case['category'] in ('single_task','multiple_tasks','explicit_order','personal_preferences') and bundle.movement_blocks:
        errors.append('unexpected_movement_for_same_location_tasks')
    if case['category'] == 'explicit_order':
        task_events = [e for e in events if e.activity_type == 'task']
        by_title = {t.task_ref:t.title for t in state.tasks}
        first = [e for e in task_events if '论文' in by_title.get(e.activity_ref,'')]
        second = [e for e in task_events if '摘要' in by_title.get(e.activity_ref,'')]
        if not first or len(second) != 1 or max(e.ends_at for e in first) > second[0].starts_at:
            errors.append('order_or_single_block_not_preserved')
    trace = getattr(bundle.agent_intelligence, 'trace', None)
    if trace is not None and trace.call_count > 9: errors.append('p5_trace_budget_exceeded')
    if trace is not None and sum(r.repair for r in trace.records if not r.fallback) > 2:
        errors.append('p5_repair_budget_exceeded')
    return errors, events


def run_case(case, meter, adapter):
    from src.p2_live_main import make_live_session, _handle_intake_submit, render_live_page_text
    from src.p2_session import load_live_final_turn
    from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
    from src.personal_settings import PersonalSettings, save_personal_settings
    from src.p5_agent_pipeline import AGENT_CALL_TRACE_KEY
    meter.case_start = len(meter.records)
    meter.scenario_id = case['scenario_id']
    start = time.monotonic()
    store, messages = {}, []
    save_personal_settings(store, PersonalSettings(**case['settings']))
    campus = case['campus']
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map(campus)
    session = make_live_session(store, adapter, map_data=map_data, campus_id=campus,
                                companion_enabled=True, agent_intelligence_enabled=True)
    st = SimpleNamespace(session_state=store, error=messages.append,
                         warning=messages.append, write=messages.append)
    result = dict(scenario_id=case['scenario_id'], category=case['category'],
                  input_summary=case['input_summary'], campus=campus, success=False,
                  hard_constraints_passed=None, exceptions=[], failures=[],
                  repair=None, replan=None, final_summary=None, checks_applicable={
                      'time_and_fixed_commitment_overlap':True, 'route_validation':True,
                      'task_ref_consistency':True, 'progress_unchanged':True,
                      'atomic_bundle_consistency':True, 'bounded_calls':True,
                      'cross_turn_ref_stability':False, 'manual_estimate_adoption':False,
                      'persistence_transaction_failure':False})
    stage = 'production_intake'
    try:
        hour = 16 if case['category']=='dinner_window' else 14
        applied = _handle_intake_submit(st, session, (), datetime(2026,9,15,hour),
                    case['input_summary'], map_data, campus,
                    current_location_text='', rerun_after_commit=False)
        bundle = load_live_final_turn(store)
        if applied and bundle is not None:
            stage = 'evaluation_checks'
            failures, events = check_bundle(bundle, case, map_data)
            result['failures'] = failures
            result['hard_constraints_passed'] = not failures
            result['final_summary'] = dict(tasks=[dict(task_ref=t.task_ref,title=t.title,
                total_minutes=t.total_minutes,completed_minutes=t.completed_minutes,
                total_source=t.total_source.value if t.total_source else None) for t in bundle.state.tasks],
                commitments=[dict(title=c.title, starts_at=c.starts_at.isoformat() if c.starts_at else None,
                    ends_at=c.ends_at.isoformat() if c.ends_at else None) for c in bundle.state.commitments],
                planned_minutes=bundle.result.allocation_plan.total_planned_minutes,
                movement_count=len(bundle.movement_blocks),
                timeline=[dict(activity_ref=e.activity_ref,type=e.activity_type,
                    start=e.starts_at.isoformat(),end=e.ends_at.isoformat()) for e in events],
                movements=[dict(origin=b.origin_name,destination=b.destination_name,
                    start=b.window_start.isoformat(),end=b.end_time.isoformat(),
                    mode=b.mode.value,minutes=b.estimated_minutes) for b in bundle.movement_blocks],
                narrative=render_live_page_text(bundle.turn,bundle.extra_questions)[:5000])
            result['replan'] = bool(bundle.result.revision_used)
            trace = store.get(AGENT_CALL_TRACE_KEY)
            result['agent_trace'] = [dict(stage=r.stage,success=r.success,repair=r.repair,
                                         fallback=r.fallback,failure_kind=r.failure_kind,
                                         failure_code=r.failure_code,
                                         validation_issues=r.validation_issues) for r in getattr(trace,'records',())]
            result['p5_repair_calls'] = sum(r.repair for r in getattr(trace,'records',()) if not r.fallback)
            result['p5_call_count'] = getattr(trace, 'call_count', 0)
            from src.p2_live_main import LIVE_INTAKE_CACHE_KEY
            intake_outcome = store.get(LIVE_INTAKE_CACHE_KEY, {}).get('outcome')
            intake_proposal = getattr(intake_outcome, 'proposal', None)
            result['location_structure']=dict(
                current_node=getattr(bundle.execution_context.current_location.location,'node_id',None),
                intake=[dict(index=i,location_present=bool(t.location_text),activity_kind=t.activity_kind)
                        for i,t in enumerate(getattr(intake_proposal,'tasks',()))],
                bindings=[dict(task_ref=b.task_ref,node_id=getattr(b.execution_location,'node_id',None),
                               activity_kind=b.activity_kind) for b in bundle.execution_context.bindings])
            result['intake_duration_sources'] = [dict(index=i, total_minutes=t.total_minutes,
                duration_source=t.duration_source) for i,t in enumerate(getattr(intake_proposal, 'tasks', ()))]
            from src.p4_execution_enrichment import latest_end_overrides
            deadlines = latest_end_overrides(bundle.execution_context, bundle.state)
            result['deadline_structure'] = dict(
                intake=[dict(index=i, before_commitment_index=t.before_commitment_index,
                             after_commitment_index=t.after_commitment_index,
                             latest_end_time=t.latest_end_time)
                        for i,t in enumerate(getattr(intake_proposal,'tasks',()))],
                execution=[dict(task_ref=b.task_ref, before_commitment_ref=b.before_commitment_ref,
                                latest_finish=deadlines[b.task_ref].isoformat() if b.task_ref in deadlines else None)
                           for b in bundle.execution_context.bindings])
            result['intake_information_gate'] = dict(
                proposed_question_count=len(getattr(intake_proposal,'questions',())),
                required_question_count=len(getattr(intake_outcome,'questions',())),
                fixed_count=len(getattr(intake_proposal,'commitments',())),
                fixed_missing_end_count=sum(
                    not any((c.ends_at,c.duration_minutes,c.relative_end_minutes))
                    for c in getattr(intake_proposal,'commitments',())),
                task_count=len(getattr(intake_proposal,'tasks',())),
                unknown_duration_count=sum(t.total_minutes is None for t in getattr(intake_proposal,'tasks',())),
            )
            result['narrative_generated'] = getattr(getattr(bundle.agent_intelligence,'narrative',None),'generated',False)
            result['explicit_duration_sources_passed'] = all(
                getattr(t.total_source, 'value', None) == 'ai_extracted_from_user_text' for t in bundle.state.tasks)
            result['intake_format_repair'] = bool(getattr(intake_outcome,'repair_used',False))
            result['intake_semantic_repair'] = bool(getattr(intake_outcome,'semantic_repair_used',False))
            result['extra_questions'] = list(bundle.extra_questions)
            result['repair'] = any(r.repair for r in getattr(trace,'records',()))
            result['success'] = not failures and meter.circuit is None
        else:
            result['failures'].append('no_published_plan')
            result['safe_failure_preserved_empty_plan'] = bundle is None
            result['final_summary'] = {'messages': messages[:8]}
    except Exception as exc:
        # Never serialize exception text, traceback, request payload or headers.
        result['exceptions'].append(type(exc).__name__)
        result['exception_stage'] = stage
        result['failures'].append('exception')
    calls = meter.records[meter.case_start:]
    result['actual_model_requests'] = len(calls)
    result['http_200_responses'] = sum(r['status_code']==200 for r in calls)
    result['elapsed_seconds'] = round(time.monotonic()-start,3)
    result['infrastructure_failure'] = meter.circuit
    result['usage'] = {k:sum((r['usage'] or {}).get(k,0) for r in calls)
                       for k in ('prompt_tokens','completion_tokens','total_tokens')} if any(r['usage'] for r in calls) else None
    return result
