"""Confirmed material batch -> existing canonical state and one atomic rebuild."""
from dataclasses import replace
from datetime import timedelta

from src.material_inbox import MaterialError, MaterialInbox, fingerprint, checked_item
from src.p1_models import SourceKind
from src.p1_window_models import FixedCommitment, AvailabilityLevel
from src.p2_agentic_models import DayPlanIntent, DAY_PLAN_INTENT_SCHEMA_VERSION
from src.p2_state_reconciler import append_structured_task
from src.p2_models import TaskState
from src.p2_task_progress import apply_total_update
from src.p2_window_derivation import derive_day_state
from src.p3_location_resolver import resolve_location
from src.p4_execution_context import (load_execution_context, save_execution_context,
    ExecutionPlanContext, ExecutableTaskBinding, ExecutionLocation, ExecutionLocationSource)
from src.p4_execution_enrichment import reconcile_execution_context
from src.personal_settings import load_personal_settings


def plan_fingerprint(store, campus_id):
    from src.p2_session import load_live_final_turn
    bundle = load_live_final_turn(store)
    return fingerprint(repr(bundle.state) if bundle else None,
        repr(bundle.execution_context) if bundle else None,
        repr(load_personal_settings(store)), campus_id)


def matching_context(store):
    from src.p2_session import load_live_final_turn
    bundle = load_live_final_turn(store)
    if not bundle:
        return ()
    return tuple(dict(task_ref=t.task_ref, title=t.title,
        scope=(bundle.execution_context.binding_for(t.task_ref).scope_summary
            if bundle.execution_context.binding_for(t.task_ref) else None))
        for t in bundle.state.tasks if t.state is TaskState.ACTIVE)


def build_material_candidate(session, inbox, reference):
    """No mutation of session.store; return the isolated store and receipt refs."""
    from src import p2_session as ps
    from src.p5_what_if import clear_what_if_preview
    draft = inbox.draft
    if draft is None or draft.status != 'ready':
        raise MaterialError('请先整理并检查材料草稿。')
    if draft.source_fingerprint in inbox.receipts:
        raise MaterialError('这份材料已经确认过，不会重复加入。')
    if draft.plan_fingerprint != plan_fingerprint(session.store, session.campus_id):
        raise MaterialError('当前任务或设置已变化，请重新整理后核对，旧方案没有改变。')
    selected = [(i, checked_item(i, draft)) for i in draft.items]
    selected = [(i,v) for i,v in selected if v is not None]
    if not selected:
        raise MaterialError('请至少勾选一项要安排的事情。')
    previous = ps.load_live_final_turn(session.store)
    if previous and previous.state.reference_datetime.date() != reference.date():
        raise MaterialError('上次方案不属于当前日期，请先使用现有更新入口处理。')
    end = previous.state.day_end if previous else reference.replace(hour=23, minute=59, second=0, microsecond=0)
    if end <= reference:
        end = reference + timedelta(hours=2)
    state = derive_day_state(reference, end, previous.state.commitments if previous else (),
        previous.state.tasks if previous else (), default_safety_buffer_minutes=session.config.default_safety_buffer_minutes)
    context = previous.execution_context if previous else load_execution_context(session.store)
    context = context or ExecutionPlanContext()
    refs, targets = [], set()
    for item, v in selected:
        if v['campus_id'] != session.campus_id:
            raise MaterialError('{}：地点/校区不属于当前校区，请切换或先取消勾选。'.format(v['title']))
        location = None
        if v['location_text']:
            # Existing exact/alias resolver, no extra model or cross-campus fallback.
            resolution = resolve_location(session.map_data, session._personal_location_text(v['location_text']))
            if not resolution.usable or resolution.campus_id != session.campus_id:
                raise MaterialError('{}：未匹配地点“{}”，请在草稿中填写本校区已知地点。'.format(v['title'],v['location_text']))
            location = ExecutionLocation.from_resolution(resolution, ExecutionLocationSource.EXPLICIT_TASK_LOCATION)
        if item.kind == 'fixed_commitment':
            if v['start'].date() != reference.date() or v['end'].date() != reference.date():
                raise MaterialError('{}：固定安排不在当前规划日；材料已保留，可先取消勾选，确认其他项。'.format(v['title']))
            exact = next((c for c in state.commitments if c.title == v['title'] and
                c.starts_at == v['start'] and c.ends_at == v['end'] and
                c.location_text == (location.display_name if location else None)), None)
            if exact:
                refs.append(exact.commitment_ref)
                continue
            if any(c.starts_at and c.ends_at and v['start'] < c.ends_at and c.starts_at < v['end']
                   for c in state.commitments):
                raise MaterialError('{}：与已有固定安排冲突，请修改或取消勾选。'.format(v['title']))
            ref = 'material_commitment_' + item.item_id
            commitment = FixedCommitment(ref, v['title'], item.evidence, v['start'], v['end'],
                location.display_name if location else None, AvailabilityLevel.UNAVAILABLE, {},
                () if location else ('地点待确认',), commitment_kind=item.commitment_kind)
            state = replace(state, commitments=state.commitments + (commitment,))
            refs.append(ref)
            continue
        source = {'ai_estimated':SourceKind.AI_ESTIMATED,
            'material_explicit':SourceKind.AI_EXTRACTED_FROM_USER_TEXT,
            'user_confirmed':SourceKind.USER_CONFIRMED}.get(v['duration_source'])
        if v['target'] == 'new':
            added = append_structured_task(state, v['title'], v['minutes'], source, allow_unknown=True)
            state, ref = added.state, added.new_task_refs[0]
            old_binding = None
        else:
            ref = v['target']
            task = next((t for t in state.tasks if t.task_ref == ref and t.state is TaskState.ACTIVE), None)
            if task is None or ref in targets:
                raise MaterialError('已有任务引用失效，或本批多项重复修改同一任务，请重新选择。')
            old_binding = context.binding_for(ref)
            # Never silently replace newer facts. Only fields that actually
            # conflict require a per-field keep-old/use-new decision.
            conflicts = []
            if v['minutes'] is not None and task.total_minutes is not None and v['minutes'] != task.total_minutes:
                conflicts.append(('minutes','总用时'))
            for old, new, field, name in (
                (old_binding.scope_summary if old_binding else None, v['scope'], 'scope','范围'),
                (old_binding.completion_criteria if old_binding else None, v['completion'], 'completion','完成标准'),
                ((old_binding.deadline_at or old_binding.latest_end_time) if old_binding else None,
                    v['deadline'], 'deadline','截止'),
                (old_binding.execution_location if old_binding else None, location, 'location','地点')):
                if old and new and old != new:
                    conflicts.append((field,name))
            undecided = [name for field,name in conflicts
                if not v['reviewed'] and v.get(field+'_conflict') != 'new']
            if undecided:
                raise MaterialError('{}：{}与原任务不同，请选择保留已有信息或采用新材料；实际进度不会重置。'.format(
                    v['title'], '、'.join(undecided)))
            if v['minutes'] is not None:
                if v['minutes'] <= task.completed_minutes and v['minutes'] != task.total_minutes:
                    raise MaterialError('材料用时不能把已有实际进度自动标成完成，请通过执行反馈确认。')
                task = apply_total_update(task, v['minutes'], source)
                state = replace(state, tasks=tuple(task if t.task_ref == ref else t for t in state.tasks))
            targets.add(ref)
        binding = old_binding or ExecutableTaskBinding(ref, activity_kind='generic')
        changes = {}
        if v['minutes'] is not None:
            changes.update(effective_duration_minutes=v['minutes'], duration_source=v['duration_source'])
        if location:
            changes['execution_location'] = location
        if v['scope']:
            changes['scope_summary'] = v['scope']
        if v['completion']:
            changes['completion_criteria'] = v['completion']
        if v['deadline']:
            changes.update(deadline_at=v['deadline'], latest_end_time=None)
        context = context.upsert(replace(binding, **changes))
        refs.append(ref)
    # Re-derive windows after the whole batch, then reuse start_day once. This
    # does not reparse confirmed material or estimate unknown work implicitly.
    state = derive_day_state(reference, end, state.commitments, state.tasks,
        default_safety_buffer_minutes=session.config.default_safety_buffer_minutes)
    working = dict(session.store)
    working[ps.MOVEMENT_DATA_KEY] = dict(working.get(ps.MOVEMENT_DATA_KEY, {}))
    working[ps.MOVEMENT_DATA_KEY]['p4_execution_base_state'] = state
    save_execution_context(working, reconcile_execution_context(context,state))
    isolated = session._fork_with_store(working)
    prior = previous.result.day_plan_intent if previous else None
    intent = (replace(prior, task_order=tuple(t.task_ref for t in state.tasks), task_estimates=()) if prior else
        DayPlanIntent(DAY_PLAN_INTENT_SCHEMA_VERSION,tuple(t.task_ref for t in state.tasks),False,(),None))
    turn = isolated.start_day(state, force=True, agent_flow='feedback', seed_day_plan_intent=intent)
    turn = replace(turn, user_text='确认材料并安排', execution_context=load_execution_context(working))
    version = max(previous.version if previous else 0, int(working.get(ps.LIVE_FINAL_TURN_SEQUENCE_KEY,0))) + 1
    bundle = ps.build_live_final_turn(turn,session.map_data,version=version,
        feedback_decision=ps.load_feedback_decision(working))
    working[ps.LIVE_FINAL_TURN_KEY] = bundle
    working[ps.LIVE_FINAL_TURN_SEQUENCE_KEY] = version
    clear_what_if_preview(working)
    return working, tuple(refs)
