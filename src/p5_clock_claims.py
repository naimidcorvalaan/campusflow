"""Model-owned clock meaning, program-owned comparison with formal boundaries.

The extractor sees event roles but not their real clock values. It cannot
accidentally substitute the actual arrival time while interpreting a claimed
departure. No action word lists or device-specific semantics are used here.
"""
import json
import re
from collections import Counter
from dataclasses import replace
from datetime import datetime

from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p5_agent_runtime import call_structured_stage
from src.p5_copy_semantics import copy_semantic_facts

CLOCK = re.compile(r'(?<!\d)(?:[01]?\d|2[0-4]):[0-5]\d(?!\d)')
FIELDS = ('opening', 'why_this_plan', 'closing', 'proactive_suggestion', 'risk_note')


def clock_catalog(context, actual_tasks=()):
    """A typed catalogue derived from existing state, never rendered Chinese."""
    rows = []
    def add(ref, subject, boundary, value):
        if value is not None:
            rows.append(dict(ref=ref, subject=subject, boundary=boundary, value=value))
    add('reference', '参考时间', 'reference_time', context.current_time)
    for i, movement in enumerate(context.movements):
        ref = 'movement:' + str(i)
        subject = '{} → {}'.format(movement.origin_name, movement.destination_name)
        add(ref + ':prepare', subject, 'preparation_start', movement.preparation_starts_at)
        add(ref + ':departure', subject, 'departure', movement.starts_at)
        add(ref + ':arrival', subject, 'arrival', movement.ends_at)
    for commitment in context.fixed_commitments:
        ref, subject = commitment.commitment_ref, commitment.title
        add(ref + ':start', subject, 'start', commitment.starts_at)
        add(ref + ':end', subject, 'end', commitment.ends_at)
        add(ref + ':building', subject, 'building_arrival_deadline', commitment.class_arrival_deadline)
        add(ref + ':classroom', subject, 'classroom_arrival', commitment.classroom_arrival_time)
        if commitment.classroom_arrival_time:
            add(ref + ':prepare_start', subject, 'preparation_start', commitment.classroom_arrival_time)
            add(ref + ':prepare_end', subject, 'preparation_end', commitment.starts_at)
    facts = copy_semantic_facts(context, actual_tasks)
    for task in context.active_tasks:
        add(task.task_ref + ':deadline', task.title, 'deadline', task.latest_end)
        add(task.task_ref + ':earliest', task.title, 'earliest_start', task.earliest_start)
    titles = {task['task_ref']: task['title'] for task in facts['tasks']}
    for segment in facts['segments']:
        ref, subject = segment['allocation_ref'], titles.get(segment['task_ref'], segment['task_ref'])
        add(ref + ':start', subject, 'start', segment['start'])
        add(ref + ':end', subject, 'end', segment['end'])
    return rows


def bind_clock_claims(context, narrative, fallback, caller, trace, actual_tasks=()):
    copy = {field: getattr(narrative, field) for field in FIELDS
            if CLOCK.search(getattr(narrative, field) or '')
            and getattr(narrative, field) != getattr(fallback, field)}
    if not copy:
        return narrative, trace
    catalog = clock_catalog(context, actual_tasks)
    system = (
        '你是 CampusFlow Clock Claim Extractor。只解释文案中每个钟点实际表达的事件和边界，'
        '不判断正确与否，也不猜真实计划时间。根据目录选择ref，保留文案声称的time(HH:MM)。'
        '不能把动作开始改成结束、出发改成到达、准备开始改成准备完成。'
        'relation为at(在该时刻)、not_after(不晚于)、not_before(不早于)。'
        '逐字段覆盖每个钟点；一个钟点若明确约束多个动作，分别列出。无法绑定则ref=null。'
        'field是copy对象中承载quote的字段键，不是事实ref。同一钟点在不同文案字段出现时，分别记录。'
        '目录的deadline是要求的截止界限，end是实际计划结束，二者不是同一事实。'
        'quote必须是该字段中的原文片段。只返回JSON：'
        '{"claims":[{"field":string,"quote":string,"time":string,"ref":string|null,"relation":string}]}。'
    )
    user = json.dumps(dict(copy=copy, catalog=[{k:v for k,v in row.items() if k != 'value'}
                                             for row in catalog]), ensure_ascii=False)
    invalid, trace = call_structured_stage(caller, None, system, user,
        lambda text: invalid_clock_fields(text, copy, catalog, context.current_time),
        'clock_claim_binding', 'bind claimed times to formal event roles', trace, max_repairs=0)
    # A missing/invalid binding is uncertainty, not permission to publish a
    # guessed instruction. Retain non-clock explanations and the whole plan.
    invalid = set(copy) if invalid is None else invalid
    if invalid:
        narrative = replace(narrative, generated=False,
            **{field: getattr(fallback, field) for field in invalid})
    return narrative, trace


def invalid_clock_fields(text, copy, catalog, reference):
    payload = extract_json_object(text)
    if not isinstance(payload, dict) or set(payload) != {'claims'} or not isinstance(payload['claims'], list):
        raise AgenticParseError('clock claim payload invalid')
    values = {row['ref']: row['value'] for row in catalog}
    covered, invalid = Counter(), set()
    for claim in payload['claims']:
        if not isinstance(claim, dict) or set(claim) != {'field','quote','time','ref','relation'}:
            raise AgenticParseError('clock claim fields invalid')
        field, quote, clock, ref, relation = (claim[k] for k in ('field','quote','time','ref','relation'))
        if (not isinstance(field, str) or field not in copy or not isinstance(quote, str)
                or not quote or quote not in copy[field] or not isinstance(clock, str)
                or not CLOCK.fullmatch(clock) or clock not in quote
                or (ref is not None and (not isinstance(ref, str) or ref not in values))
                or relation not in ('at','not_after','not_before')):
            raise AgenticParseError('clock claim reference invalid')
        covered[(field, clock)] += 1
        if ref is None:
            invalid.add(field)
            continue
        actual = datetime.fromisoformat(values[ref])
        day = datetime.fromisoformat(reference).date()
        actual_minutes = (actual.date() - day).days * 1440 + actual.hour * 60 + actual.minute
        hour, minute = map(int, clock.split(':'))
        claimed = hour * 60 + minute
        if hour == 24 and minute != 0:
            invalid.add(field)
        elif not {'at': actual_minutes == claimed, 'not_after': actual_minutes <= claimed,
                  'not_before': actual_minutes >= claimed}[relation]:
            invalid.add(field)
    for field, value in copy.items():
        for clock, count in Counter(CLOCK.findall(value)).items():
            if covered[(field, clock)] < count:
                invalid.add(field)
    return invalid
