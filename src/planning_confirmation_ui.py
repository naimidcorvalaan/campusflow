"""Bind an inline answer to the question the user actually saw.

This UI envelope goes through the existing reconciliation transaction. It does
not parse times, infer progress, or publish a candidate. Questions upstream are
text-only; use their shared intent classifier plus formal commitment identities.
"""
import hashlib
import json
from dataclasses import dataclass, replace

from src.p2_main import _pending_questions, _provisional_reason, build_current_plan_display
from src.p2_question_filter import _intent, named_question_commitments


@dataclass(frozen=True)
class PlanningConfirmation:
    token: str
    question: str
    field: object
    commitment_ref: object
    original_request: str
    candidate_refs: tuple = ()
    task_ref: object = None


def bind_confirmation(bundle, original_request='', selected_ref=None):
    if bundle is None:
        return None
    turn = bundle.turn
    question = _provisional_reason(build_current_plan_display(turn),
                                   _pending_questions(turn, bundle.extra_questions))
    if not question:
        return None
    field = _intent(question)
    task_ref = None
    from src.p4_execution_context import ExecutionConfirmationKind
    for pending in bundle.execution_context.confirmations:
        task = bundle.execution_context.binding_for(pending.task_ref)
        if (pending.kind is ExecutionConfirmationKind.TASK_LOCATION_REQUIRED and task
                and task.execution_location is None
                and question == '任务地点“{}”具体是哪里？'.format(task.raw_location_text)):
            field, task_ref = 'task_location', pending.task_ref
            break
    if field == 'destination':
        field = 'location_text'
    candidates = ()
    if field in ('starts_at', 'ends_at', 'location_text'):
        candidates = tuple(c for c in turn.result.updated_state.commitments
                           if getattr(c, field) is None)
        named = named_question_commitments(question, turn.result.updated_state)
        if named:
            candidates = tuple(c for c in named if getattr(c, field) is None)
    refs = tuple(c.commitment_ref for c in candidates)
    target = refs[0] if len(refs) == 1 else (selected_ref if selected_ref in refs else None)
    token = hashlib.sha256((repr(bundle) + question + str(target)).encode()).hexdigest()[:20]
    return PlanningConfirmation(token, question, field, target, original_request, refs, task_ref)


def answer_message(binding, answer, bundle):
    current = bind_confirmation(bundle, binding.original_request, binding.commitment_ref)
    if current != binding:
        raise ValueError('待确认的问题已更新，请按当前问题重新确认。你的回答已保留。')
    if not str(answer).strip():
        raise ValueError('请先填写这道问题的回答。')
    if binding.field in ('starts_at', 'ends_at', 'location_text') and not binding.commitment_ref:
        raise ValueError('请先选择这次回答对应的固定安排。')
    target = next((c for c in bundle.turn.result.updated_state.commitments
                   if c.commitment_ref == binding.commitment_ref), None)
    payload = dict(question=binding.question, answer=str(answer).strip(),
                   target_task_ref=binding.task_ref,
                   target_commitment_ref=binding.commitment_ref, field=binding.field,
                   target_title=target.title if target else None,
                   target_starts_at=target.starts_at.isoformat() if target and target.starts_at else None)
    return ('用户正在回答当前待确认问题，不是在提出新的独立任务或报告系统当前时间。'
            '原始请求仍在当前会话中，本次仅补充缺少的信息，不得重新执行、增添任务或重复报告进度。'
            '只将 answer 用于 question 所指的事项；有 target_commitment_ref 时只更新该安排的 field，'
            '保留其他已知字段。无法理解时继续询问，不猜测。\n'
            '当前确认回答：' + json.dumps(payload, ensure_ascii=False))


def verify_answer_result(binding, before, after):
    """Reject a misbound answer before the UI persists/adopts the transaction."""
    if after is None or after is before:
        raise ValueError('这次回答还未能用于当前问题，请补充具体信息后再确认。')
    if binding.field == 'task_location':
        old = before.execution_context
        new = after.execution_context
        binding_before = old.binding_for(binding.task_ref)
        binding_after = new.binding_for(binding.task_ref)
        if not binding_after or binding_after.execution_location is None:
            raise ValueError('还未识别到这项任务的地点，请填写具体地点；原方案已保留。')
        if (before.state.tasks != after.state.tasks or before.state.commitments != after.state.commitments
                or old.current_location != new.current_location
                or replace(binding_after, execution_location=binding_before.execution_location) != binding_before
                or tuple(b for b in old.bindings if b.task_ref != binding.task_ref)
                != tuple(b for b in new.bindings if b.task_ref != binding.task_ref)):
            raise ValueError('任务地点回答意外改变了其他事实，原方案已保留，请重试。')
    elif binding.field == 'current_location':
        from src.p4_execution_context import CurrentLocationSource
        old = before.turn.result.updated_state
        new = after.turn.result.updated_state
        context = after.execution_context
        if context.current_location.source is not CurrentLocationSource.USER:
            raise ValueError('还未确认你现在的位置，请填写具体地点；原方案已保留。')
        if old.commitments != new.commitments or old.tasks != new.tasks or old.now != new.now:
            raise ValueError('位置回答意外改变了已有任务或课程时间，原方案已保留，请重试。')
        # Route times and auto-selected destinations may change, event meaning may not.
        def meaning(binding):
            return (binding.task_ref, binding.activity_kind, binding.not_before_commitment_ref,
                    binding.before_commitment_ref, binding.meal_before_commitment_ref,
                    binding.meal_explicit_time, binding.earliest_start_time, binding.latest_end_time)
        if tuple(map(meaning, before.execution_context.bindings)) != tuple(map(meaning, context.bindings)):
            raise ValueError('位置回答意外改变了已有任务顺序，原方案已保留，请重试。')
    elif binding.commitment_ref and binding.field in ('starts_at', 'ends_at', 'location_text'):
        old = before.turn.result.updated_state
        new = after.turn.result.updated_state
        old_by_ref = {c.commitment_ref: c for c in old.commitments}
        new_by_ref = {c.commitment_ref: c for c in new.commitments}
        if old_by_ref.keys() != new_by_ref.keys():
            raise ValueError('回答未正确关联到原来的固定安排，请重试；原方案已保留。')
        for ref, previous in old_by_ref.items():
            candidate = new_by_ref[ref]
            if ref == binding.commitment_ref:
                value = getattr(candidate, binding.field)
                if value is None:
                    raise ValueError('还未识别到这项安排的时间，请填写具体时间后再确认。')
                if replace(candidate, **{binding.field: getattr(previous, binding.field)}) != previous:
                    raise ValueError('回答意外改变了其他安排信息，原方案已保留，请重试。')
                if candidate.starts_at and candidate.ends_at and candidate.ends_at <= candidate.starts_at:
                    raise ValueError('结束时间需要晚于这项安排的开始时间。')
            elif candidate != previous:
                raise ValueError('回答意外改变了其他固定安排，原方案已保留，请重试。')
        if old.tasks != new.tasks:
            raise ValueError('回答意外改变了任务或进度，原方案已保留，请重试。')
    else:
        remaining = bind_confirmation(after, binding.original_request)
        if remaining and remaining.question == binding.question:
            raise ValueError('这道问题还未解决，请补充更具体的回答；原方案已保留。')
