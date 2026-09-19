"""Estimate-only recovery from known JSON, independent of formal fact validation.

No model calls, raw response retention, or minute extraction from prose.
Each estimate is validated within one response. Earlier formal facts may be
retained, with estimate attachment restricted to an exact, unique task/scope.
"""
from dataclasses import replace
import json
import re
from src.material_estimate_diagnostics import record
from src.estimate_arithmetic import ensure_estimate_arithmetic
from src.material_inbox import (MaterialError, MaterialItem, SCHEMA, fingerprint,
    parse_extraction, extract_json_object, AgenticParseError, estimate_basis, item_values)

ESTIMATE_KEYS = frozenset(('task_name', 'short_scope', 'focused_minutes_min',
    'focused_minutes_max', 'recommended_minutes', 'rationale', 'assumptions'))
MAX_FALLBACK_MINUTES = 1440


def apply_coverage(result, responses, file_source=None):
    """Coverage can qualify a valid estimate, never remove it or open a gate.

    No filename/type/minute heuristics. Missing metadata is conservative. A
    format repair cannot erase an earlier explicit workload limitation.
    """
    source_incomplete = bool(file_source and (file_source.warnings or
        (file_source.page_count and len(file_source.selected_pages)<file_source.page_count)))
    assessments=[]
    for raw in responses:
        try:
            obj=extract_json_object(raw)
            value=obj.get('coverage') if isinstance(obj,dict) else None
            if (isinstance(value,dict) and set(value)=={'level','reason','uncovered_content'}
                    and value['level'] in ('whole','partial','unknown')
                    and isinstance(value['reason'],str) and 0<len(value['reason'].strip())<=400
                    and isinstance(value['uncovered_content'],str) and len(value['uncovered_content'])<=400):
                assessments.append(value['level']=='whole' and not value['uncovered_content'].strip())
        except (ValueError,TypeError,KeyError,AgenticParseError):
            pass
    unresolved = (result.diagnostics.get('unresolved_count',0)
        or any(entry.get('scope_unresolved') for entry in result.estimate_fallbacks)) or any(
        isinstance(issue,dict) and issue.get('field') in ('scope','completion','identity','general')
        for item in result.items for issue in item.ambiguities)
    whole = bool(assessments) and all(assessments) and not (source_incomplete or unresolved)
    note = '' if whole else ('还有部分内容未能读到，这里只估目前识别到的部分，完成整项任务可能需要更久。'
        if source_incomplete else '这里只估目前识别到的部分，完成整项任务可能需要更久。')
    return replace(result,estimate_coverage='whole' if whole else 'partial',coverage_note=note,
        source_incomplete=source_incomplete)


def validate_estimate(value):
    if not isinstance(value, dict) or set(value) != ESTIMATE_KEYS:
        record('material_estimate','validate_estimate','unknown_field' if isinstance(value,dict)
            and set(value)-ESTIMATE_KEYS else 'missing_field',field='estimate',parsed=True,valid=False)
        raise MaterialError('还缺少可以判断工作量的信息。')
    for key, limit in (('task_name',100),('short_scope',500),('rationale',400)):
        if not isinstance(value[key],str) or not value[key].strip() or len(value[key]) > limit:
            record('material_estimate','validate_estimate','missing_scope' if key=='short_scope'
                else 'invalid_field',field=key,parsed=True,valid=False)
            raise MaterialError('还缺少可以判断工作量的信息。')
    numbers = [value[k] for k in ('focused_minutes_min','recommended_minutes','focused_minutes_max')]
    if (any(type(n) is not int or not 1 <= n <= MAX_FALLBACK_MINUTES for n in numbers)
            or not numbers[0] <= numbers[1] <= numbers[2]):
        field=next((k for k in ('focused_minutes_min','recommended_minutes','focused_minutes_max')
            if type(value[k]) is not int or not 1<=value[k]<=MAX_FALLBACK_MINUTES),'minutes')
        record('material_estimate','validate_estimate','missing_field' if any(n is None for n in numbers)
            else 'invalid_duration_range',field=field,parsed=True,valid=False)
        raise MaterialError('用时还不能确定，请补充任务范围后再估算。')
    assumptions = value['assumptions']
    if (not isinstance(assumptions,(tuple,list)) or len(assumptions) > 6
            or any(not isinstance(s,str) or not s.strip() or len(s)>200 for s in assumptions)):
        record('material_estimate','validate_estimate','invalid_field',field='assumptions',parsed=True,valid=False)
        raise MaterialError('估算依据还不完整。')
    record('material_estimate','validate_estimate','accepted',parsed=True,valid=True)
    return dict(value, assumptions=tuple(assumptions))


def result_diagnostics(result, repair_used, category, error_category='', unresolved_count=0):
    if not result.items and not result.estimate_fallbacks:
        category = 'needs_input'
    return dict(source_type=result.source_type, parse_status=category,
        result_level=category, repair_used=repair_used,
        estimate_available=bool(result.estimate_fallbacks or any(i.minutes for i in result.items)),
        task_count=len(result.items), estimate_count=len(result.estimate_fallbacks),
        safe_error_category=error_category,unresolved_count=unresolved_count)


def _empty_time(value):
    return value is None or (isinstance(value,dict)
        and set(value)=={'text','date','clock','offset_days','week_offset','weekday'}
        and all(v is None or v == '' for v in value.values()))


def _workload_evidence(evidence, draft):
    """Layout whitespace may vary; words, punctuation and order may not.

    This is only for workload evidence, never formal dates, refs or facts.
    """
    if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 600:
        return False
    if draft.source_type not in ('text', 'docx', 'pdf_text'):
        return True
    from src.material_evidence import workload_quote_matches
    return workload_quote_matches(evidence,draft.original_text)


def _scope_question(estimate):
    question = estimate.get('clarification_question')
    if question is not None and (not isinstance(question, str) or len(question) > 300):
        raise MaterialError('估时范围说明无效。')
    return bool(question and question.strip())


def _blocking_confirmation_fields(obj):
    """Retain constraint categories for adoption; never copy unvalidated facts."""
    fields = set()
    known_fields = {'deadline','location','fixed_arrangement','existing_task','identity','multiple_actions'}
    action = obj.get('actionability')
    if isinstance(action, dict):
        required = action.get('confirmation_required', [])
        if not isinstance(required, list) or any(not isinstance(v, str) for v in required):
            fields.add('details')
        else:
            fields.update('scope' if v == 'identity' else v if v in known_fields else 'details'
                for v in required if v != 'multiple_actions')
    rows = obj.get('items', [])
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, dict):
            fields.add('details')
            continue
        known = {'kind','title','scope','completion','evidence','deadline','start','end','location_text',
            'campus_id','commitment_kind','minutes','duration_evidence','uncertainties','possible_task_ref','estimate'}
        if set(row)-known:
            fields.add('details')
        for key, field in (('deadline', 'deadline'), ('start', 'fixed_arrangement'),
                           ('end', 'fixed_arrangement')):
            if key in row and not _empty_time(row[key]):
                fields.add(field)
        for key, field in (('location_text', 'location'), ('campus_id', 'location'),
                           ('commitment_kind', 'fixed_arrangement'), ('possible_task_ref', 'existing_task')):
            if row.get(key):
                fields.add(field)
        if row.get('kind') == 'fixed_commitment':
            fields.add('fixed_arrangement')
        for issue in row.get('uncertainties', []) if isinstance(row.get('uncertainties', []), list) else [{}]:
            field = issue.get('field') if isinstance(issue, dict) else None
            if not isinstance(field, str):
                field = 'details'
            fields.add({'identity': 'scope', 'scope': 'scope', 'completion': 'scope',
                'start': 'fixed_arrangement', 'end': 'fixed_arrangement',
                'deadline': 'deadline', 'location': 'location'}.get(field, 'details'))
    return tuple(sorted(fields))


def workload_confirmation_fields(responses):
    fields = set()
    for raw in responses:
        try:
            obj = extract_json_object(raw)
            if isinstance(obj, dict):
                fields.update(_blocking_confirmation_fields(obj))
        except (ValueError, TypeError, AgenticParseError):
            pass
    return tuple(sorted(fields))


def scope_clarification(entry):
    """A coverage/identity flag is not a missing task definition.

    Use the retained scope, not the document's unread remainder. Explicit
    unresolved scope text keeps its original question; a fully stated action
    does not become ambiguous merely because a model asks about other work.
    """
    value = entry.get('estimate')
    if not isinstance(value, dict):
        return '请先取得包含任务步骤的估时结果。'
    scope = value.get('short_scope')
    scope = scope.strip() if isinstance(scope, str) else ''
    unspecified = (not scope or scope in ('处理材料', '处理这份材料', '完成任务', '按要求完成')
        or re.search(r'^(?:尚未|尚不|未|待|不)(?:明确|确定|确认|清楚|补充)', scope)
        or re.search(r'(?:范围|动作|步骤|方式|栏目)(?:尚未|未|待)(?:明确|确定|确认)', scope))
    if not unspecified:
        return ''
    question = entry.get('scope_clarification')
    if isinstance(question, str) and question.strip():
        return question.strip()
    return '请明确本次要完成的具体步骤：{}'.format(scope or value.get('task_name', ''))


def minimal_confirmation_mode(entry):
    try:
        validate_estimate(entry.get('estimate'))
    except MaterialError:
        return 'blocked'
    if scope_clarification(entry):
        return 'blocked'
    if entry.get('final_validation_errors'):
        return 'blocked'
    if entry.get('final_contract'):
        from src.material_estimate_contract import validate_final_estimate
        try:
            fields=set(validate_final_estimate(entry))
        except (ValueError,TypeError,KeyError):
            return 'blocked'
    else:
        fields = set(entry.get('confirmation_fields') or ())
    if fields - {'scope'}:
        return 'blocked'
    if entry.get('simple_confirmation_allowed'):
        return 'simple'
    # Confirm the retained candidate, including saved drafts disabled by a
    # coarse identity flag or display-only policy. Batch review and every
    # known constraint/envelope blocker still run through the existing gates.
    if (entry.get('scope_confirmation_allowed')
            or 'confirmation_fields' in entry):
        return 'scope'
    return 'blocked'


def adoption_next_step(entry):
    fields = set(entry.get('confirmation_fields') or ()) - {'scope'}
    labels = [('deadline', '截止时间'), ('location', '执行地点'),
              ('fixed_arrangement', '固定安排时间'), ('existing_task', '新任务或已有任务')]
    parts = [label for key, label in labels if key in fields]
    if parts:
        return '补充{}后加入计划'.format('、'.join(parts))
    return scope_clarification(entry) or '核对任务时间、地点及新增方式后加入计划'


def _estimate(row, draft, index, singleton):
    # Unknown event/identity and evidence-free model guesses cannot yield even
    # an estimate. Deadline/location data never enters the estimate schema.
    if not isinstance(row,dict) or row.get('kind') != 'task':
        raise MaterialError('请补充你具体准备做什么。')
    evidence = row.get('evidence')
    if not _workload_evidence(evidence, draft):
        raise MaterialError('没有足够材料依据。')
    estimate = row.get('estimate')
    if not isinstance(estimate,dict):
        raise MaterialError('还需要补充任务范围。')
    from src.material_output_schema import ESTIMATE_REQUIRED,LEGACY_ESTIMATE_OPTIONAL
    from src.material_formal_validation import fields
    fields(estimate,ESTIMATE_REQUIRED,'$.estimate',LEGACY_ESTIMATE_OPTIONAL)
    unresolved = _scope_question(estimate)
    safe = validate_estimate(dict(task_name=row.get('title'),short_scope=row.get('scope'),
        focused_minutes_min=estimate.get('min_focus_minutes'),
        focused_minutes_max=estimate.get('max_focus_minutes'),
        recommended_minutes=estimate.get('recommended_minutes'),rationale=estimate.get('basis'),
        assumptions=estimate.get('assumptions')))
    from src.material_effort import ensure_effort,sources_for_draft,digest
    from src.material_effort_provenance import receipt
    ensure_effort(safe['recommended_minutes'],safe['focused_minutes_min'],
        safe['focused_minutes_max'],safe['rationale'],safe['assumptions'],
        ledger=estimate.get('effort_ledger'),sources=sources_for_draft(draft))
    # Eligibility requires explicit absence, not missing/invalid facts. Only a
    # single clearly identified task can be converted by explicit user consent.
    known={'kind','title','scope','completion','evidence','deadline','start','end','location_text',
        'campus_id','commitment_kind','minutes','duration_evidence','uncertainties','possible_task_ref','estimate'}
    facts_clear = (not set(row)-known and all(k in row and _empty_time(row[k]) for k in ('deadline','start','end'))
        and row.get('location_text','missing') in ('',None)
        and row.get('commitment_kind','missing') is None
        and row.get('possible_task_ref','missing') is None
        and row.get('uncertainties') == []
        and row.get('minutes','missing') is None
        and row.get('campus_id','missing') is None)
    return dict(estimate=safe,effort_ledger=estimate.get('effort_ledger'),
        effort_provenance=receipt(sources_for_draft(draft),estimate.get('effort_ledger')),
        effort_grounding=digest(estimate['effort_ledger']) if estimate.get('effort_ledger') else None, quantity_claims=estimate.get('quantity_claims',[]), item_id=fingerprint(draft.source_fingerprint,index)[:24],
        simple_confirmation_allowed=not unresolved and singleton and facts_clear, scope_unresolved=unresolved,
        scope_confirmation_allowed=facts_clear,
        confirmation_fields=_blocking_confirmation_fields({'items': [row]}),
        scope_clarification=estimate.get('clarification_question') or next((
            issue.get('message', '') for issue in row.get('uncertainties', [])
            if isinstance(issue, dict) and issue.get('field') in ('identity','scope','completion')), ''))


def _action_estimate(obj, draft):
    """An explicitly estimatable action can exist without any formal items."""
    action=obj.get('actionability'); estimate=obj.get('estimate')
    if not isinstance(action,dict) or action.get('is_estimatable') is not True or not isinstance(estimate,dict):
        record('material_estimate','action_estimate','missing_estimate',field='estimate',parsed=True,valid=False)
        raise MaterialError('没有合法的可估时行为。')
    evidence=action.get('evidence'); reason=action.get('reason')
    from src.material_formal_validation import invariant,shape
    from src.material_actionability_sources import resolve
    evidence_refs,source_audit=resolve(action,draft)
    uses_refs='evidence_refs' in action
    checks=dict(evidence_present=uses_refs or ('evidence' in action and evidence is not None),
        evidence_string=uses_refs or isinstance(evidence,str),
        evidence_nonempty=uses_refs or (isinstance(evidence,str) and bool(evidence.strip())),
        evidence_length_valid=uses_refs or (isinstance(evidence,str) and len(evidence)<=600),
        verified_evidence_required=source_audit['verified_evidence_required'],
        reason_present='reason' in action and reason is not None,reason_string=isinstance(reason,str),
        reason_nonempty=isinstance(reason,str) and bool(reason.strip()),
        reason_length_valid=isinstance(reason,str) and len(reason)<=400)
    observed=dict(source_audit,**checks,evidence_shape=shape(evidence),reason_shape=shape(reason),
        is_estimatable=True,failed_conditions=[k for k,v in checks.items() if not v],combined_decision=all(checks.values()))
    from src.material_estimate_diagnostics import record_actionability
    record_actionability(observed)
    if not all(checks.values()):
        record('material_estimate','workload_evidence','evidence_mismatch',field='evidence',parsed=True,valid=False)
        invariant('action_estimate_grounded_evidence_and_reason',
            ['$.actionability.evidence_refs','$.actionability.evidence','$.actionability.reason','$.source.original_text','$.source.source_type'],
            'verified current material source refs (or unambiguous legacy quote); reason is nonempty <=400 characters',
            observed)
    from src.material_output_schema import ESTIMATE_REQUIRED,LEGACY_ESTIMATE_OPTIONAL
    from src.material_formal_validation import fields
    fields(estimate,ESTIMATE_REQUIRED,'$.estimate',LEGACY_ESTIMATE_OPTIONAL)
    unresolved = _scope_question(estimate)
    safe=validate_estimate(dict(task_name=action.get('task_name'),short_scope=action.get('short_scope'),
        focused_minutes_min=estimate.get('min_focus_minutes'),focused_minutes_max=estimate.get('max_focus_minutes'),
        recommended_minutes=estimate.get('recommended_minutes'),rationale=estimate.get('basis'),
        assumptions=estimate.get('assumptions')))
    from src.material_effort import ensure_effort,sources_for_draft,digest
    from src.material_effort_provenance import receipt
    ensure_effort(safe['recommended_minutes'],safe['focused_minutes_min'],
        safe['focused_minutes_max'],safe['rationale'],safe['assumptions'],
        ledger=estimate.get('effort_ledger'),sources=sources_for_draft(draft))
    waiting=action.get('waiting_note') or ''
    if not isinstance(waiting,str) or len(waiting)>300:
        raise MaterialError('等待时间说明无效。')
    allowed={'is_estimatable','task_name','short_scope','reason','evidence','evidence_refs','confirmation_required','waiting_note'}
    simple=(not unresolved and obj.get('items')==[] and action.get('confirmation_required')==[] and not set(action)-allowed)
    return dict(estimate=safe,effort_ledger=estimate.get('effort_ledger'),
        effort_provenance=receipt(sources_for_draft(draft),estimate.get('effort_ledger')),
        effort_grounding=digest(estimate['effort_ledger']) if estimate.get('effort_ledger') else None,quantity_claims=estimate.get('quantity_claims',obj.get('quantity_claims',[])),item_id=fingerprint(draft.source_fingerprint,'action')[:24],
        simple_confirmation_allowed=simple,waiting_note=waiting,scope_unresolved=unresolved,
        scope_confirmation_allowed=not _blocking_confirmation_fields(obj) and not set(action)-allowed,
        confirmation_fields=_blocking_confirmation_fields(obj),
        scope_clarification=estimate.get('clarification_question') or '')


def finish_result(result, responses, draft, refs, repair_used):
    recovered=recover_result(responses,draft,refs,repair_used,error_category='')
    # A workload-only completion intentionally has no formal rows. Keep the
    # previously validated snapshot; only exact task/scope matches can receive
    # its time. Unmatched workload stays separate and cannot bypass review.
    if not result.items and repair_used:
        for raw in reversed(responses[:-1]):
            try:
                previous = parse_extraction(raw, draft, refs)
                if previous.items:
                    result = previous
                    break
            except (ValueError, TypeError, KeyError, AgenticParseError):
                continue
    if not result.items:
        if not recovered.estimate_fallbacks and not repair_used:
            obj=extract_json_object(responses[-1])
            action=obj.get('actionability') if isinstance(obj,dict) else None
            if isinstance(action,dict) and action.get('is_estimatable') is True:
                # Preserve the actual failing ledger field instead of replacing
                # it with a generic root-level "requires valid estimate" error.
                _action_estimate(obj,draft)
                raise ValueError('estimatable action requires a valid estimate')
        result=recovered
        if not result.items:
            return result
    # Only merge estimate fields for an unambiguous exact task/scope. Never
    # transfer time/location/ref facts across responses, or overwrite an
    # explicitly stated duration. First valid estimate survives format repair.
    enriched=[]
    for row in result.items:
        for raw in responses:
            try:
                obj=extract_json_object(raw)
                try:
                    candidate=_action_estimate(obj,draft)
                except (ValueError,TypeError,KeyError):
                    candidate=_estimate(obj['items'][0],draft,0,len(obj['items'])==1)
                value=candidate['estimate']
                if (not candidate.get('scope_unresolved') and row.kind=='task'
                        and row.title==value['task_name'] and row.scope==value['short_scope']
                        and sum(i.title==row.title and i.scope==row.scope for i in result.items)==1):
                    if row.duration_source!='material_explicit':
                        row=replace(row,minutes=value['recommended_minutes'],duration_source='ai_estimated',
                            estimate_min_minutes=value['focused_minutes_min'],estimate_max_minutes=value['focused_minutes_max'],
                            estimate_basis=value['rationale'],estimate_assumptions=value['assumptions'])
                        row=replace(row,estimate_basis_fingerprint=estimate_basis(item_values(row,result)))
                    row=replace(row,estimate_waiting_note=candidate.get('waiting_note',''))
                    break
            except (ValueError,TypeError,KeyError,IndexError,AgenticParseError):
                continue
        enriched.append(row)
    result=replace(result,items=tuple(enriched))
    # Preserve a separate identifiable action even when other formal rows are
    # already usable. Never duplicate the task just enriched above.
    remaining=tuple(dict(entry, simple_confirmation_allowed=False, scope_confirmation_allowed=False) if result.items else entry
        for entry in recovered.estimate_fallbacks if not any(
        item.minutes and item.title==entry['estimate']['task_name'] and item.scope==entry['estimate']['short_scope']
        for item in result.items))
    result=replace(result,estimate_fallbacks=remaining)
    return replace(result,diagnostics=result_diagnostics(result,repair_used,
        'estimate_fallback' if remaining else 'full_structured',
        unresolved_count=recovered.diagnostics.get('unresolved_count',0)))


def recover_result(responses, draft, refs, repair_used, error_category='structure_invalid'):
    choices=[]
    for raw in responses:
        try:
            obj=extract_json_object(raw)
            if not isinstance(obj,dict) or obj.get('schema_version') != SCHEMA:
                continue
            full=[]; estimates=[]; missing=0
            action_estimate=None
            try:
                action_estimate=_action_estimate(obj,draft)
            except (ValueError,TypeError,KeyError):
                pass
            rows=obj.get('items')
            if not isinstance(rows,list) or len(rows)>12:
                if not action_estimate:
                    continue
                action_estimate.update(simple_confirmation_allowed=False, scope_confirmation_allowed=False,
                    confirmation_fields=('details',))
                choices.append(([],[action_estimate],1,draft.reference_date))
                continue
            for index,row in enumerate(rows):
                try:
                    result=parse_extraction(json.dumps(dict(obj,items=[row])),draft,refs)
                    full.append(replace(result.items[0],item_id=fingerprint(draft.source_fingerprint,index)[:24]))
                except (ValueError,TypeError,KeyError,IndexError,AgenticParseError):
                    try:
                        estimates.append(_estimate(row,draft,index,len(obj['items'])==1))
                    except (ValueError,TypeError,KeyError):
                        missing+=1
            # Keep reference-date validation from the complete parser. If a
            # partial result uses material reference dates, validate envelope.
            try:
                envelope=parse_extraction(json.dumps(dict(obj,items=[])),draft,refs)
            except (ValueError,TypeError,KeyError,AgenticParseError):
                # Invalid formal envelope does not invalidate independent
                # workload evidence, but can never permit promotion.
                full=[]
                for entry in estimates:
                    entry.update(simple_confirmation_allowed=False, scope_confirmation_allowed=False,
                        confirmation_fields=('details',))
                if action_estimate:
                    action_estimate.update(simple_confirmation_allowed=False, scope_confirmation_allowed=False,
                        confirmation_fields=('details',))
                envelope=draft
            if action_estimate:
                for entry in estimates:
                    if (entry['estimate']['task_name']==action_estimate['estimate']['task_name']
                            and entry['estimate']['short_scope']==action_estimate['estimate']['short_scope']):
                        entry['waiting_note']=action_estimate['waiting_note']
                        # A top-level uncertainty cannot disappear through an item.
                        entry['simple_confirmation_allowed'] &= obj['actionability'].get('confirmation_required')==[]
                        if _blocking_confirmation_fields(obj):
                            entry.update(scope_confirmation_allowed=False,
                                confirmation_fields=_blocking_confirmation_fields(obj))
                for index,item in enumerate(full):
                    if (item.title==action_estimate['estimate']['task_name']
                            and item.scope==action_estimate['estimate']['short_scope']):
                        full[index]=replace(item,estimate_waiting_note=action_estimate['waiting_note'])
            if action_estimate and not any(
                    entry['estimate']['task_name']==action_estimate['estimate']['task_name']
                    and entry['estimate']['short_scope']==action_estimate['estimate']['short_scope']
                    for entry in estimates) and not any(
                    item.minutes and item.title==action_estimate['estimate']['task_name']
                    and item.scope==action_estimate['estimate']['short_scope'] for item in full):
                estimates.append(action_estimate)
                missing=max(0,missing-1)
            choices.append((full,estimates,missing,envelope.reference_date))
        except (ValueError,TypeError,KeyError,AgenticParseError):
            continue
    full, estimates, missing, reference = max(choices,
        key=lambda c:(bool(c[1] or any(item.minutes for item in c[0])), len(c[0])+len(c[1])),
        default=([],[],1,draft.reference_date))
    message=('还有{}项内容未能确定工作范围，当前只估已识别的部分。'.format(missing) if missing else '')
    if not full and not estimates:
        message='这次未能生成可靠的时间估计，材料和补充说明已保留。'
    result=replace(draft,items=tuple(full),estimate_fallbacks=tuple(estimates),reference_date=reference,
        status='ready',message=message,extraction_revision=draft.extraction_revision+1)
    return replace(result,diagnostics=result_diagnostics(result,repair_used,
        'estimate_fallback' if estimates else 'full_structured',error_category,missing))


def matching_unestimated_item(entry, items):
    """Bind only one untouched, unconstrained recognition row to its estimate.

    Exact identity/scope equality is required; no text similarity or cross-task
    inference. Existing durations, edits, constraints and ambiguities survive.
    """
    if len(items) != 1:
        return None
    row=items[0]; value=entry.get('estimate',{})
    from src.material_inbox import MaterialTime
    if (row.kind=='task' and row.title==value.get('task_name')
            and row.scope==value.get('short_scope') and row.minutes is None
            and row.estimate is None and not row.user_edits and not row.ambiguities
            and not row.possible_task_ref and not row.location_text and not row.campus_id
            and not row.commitment_kind and not row.duration_source
            and not row.estimate_completed_minutes
            and all(t==MaterialTime() for t in (row.deadline,row.start,row.end))):
        return row
    return None


def confirm_minimal_task(draft, item_id, title, minutes, user_confirmed=False, confirmed_scope=None):
    """Consent creates only a draft, then the unchanged atomic confirmation gate runs."""
    if draft.status!='ready' or not user_confirmed:
        raise MaterialError('请先确认这是一项没有截止或指定地点的新任务。')
    entry=next((v for v in draft.estimate_fallbacks if v['item_id']==item_id),None)
    if not entry or minimal_confirmation_mode(entry) == 'blocked':
        raise MaterialError(adoption_next_step(entry or {}))
    if (minimal_confirmation_mode(entry) == 'scope'
            and (not isinstance(confirmed_scope, str) or not confirmed_scope.strip())):
        raise MaterialError('请先确认本次采用的任务范围。')
    value=validate_estimate(entry['estimate'])
    if not isinstance(title,str) or not title.strip() or len(title)>100 or type(minutes) is not int or not 1<=minutes<=1440:
        raise MaterialError('请确认任务名称和合理的采用分钟。')
    scope = value['short_scope'] if confirmed_scope is None else confirmed_scope
    if not isinstance(scope, str) or not scope.strip() or len(scope) > 500:
        raise MaterialError('请填写本次采用的任务范围（500字以内）。')
    existing=next((row for row in draft.items if row.item_id==item_id),None)
    if existing is not None and matching_unestimated_item(entry,draft.items) != existing:
        raise MaterialError('任务内容已变化，请重新核对后采用估时。')
    base=existing or MaterialItem(item_id,'task',title.strip(),scope.strip(),'',
        '用户确认的简单任务；工作量来自未完整整理结果的估时')
    row=replace(base,title=title.strip(),scope=scope.strip(),minutes=value['recommended_minutes'],
        duration_source='user_confirmed',estimate_min_minutes=value['focused_minutes_min'],
        estimate_max_minutes=value['focused_minutes_max'],estimate_basis=value['rationale'],
        estimate_assumptions=value['assumptions'],estimate_waiting_note=entry.get('waiting_note',''),
        user_edits={'reviewed':True,'minutes':str(minutes)})
    row=replace(row,estimate_basis_fingerprint=estimate_basis(item_values(row,draft)))
    items=tuple(row if old.item_id==item_id else old for old in draft.items) if existing else draft.items+(row,)
    return replace(draft,items=items,
        estimate_fallbacks=tuple(v for v in draft.estimate_fallbacks if v['item_id']!=item_id))


def reestimate_only(draft, item_id, caller, supplement='', default_context=''):
    from src.material_inbox import estimate_item
    if draft.status!='ready':
        raise MaterialError('材料已变化，请先重新整理。')
    entry=next((v for v in draft.estimate_fallbacks if v['item_id']==item_id),None)
    if not entry:
        raise MaterialError('这份估时已变化，请重新查看。')
    value=validate_estimate(entry['estimate'])
    row=MaterialItem(item_id,'task',value['task_name'],value['short_scope'],'','',
        user_edits={'supplement':str(supplement)[:1200]})
    updated=estimate_item(row,draft,caller,default_context)
    result=updated.estimate.result
    if not result.ready:
        raise MaterialError(result.clarification_question or '请补充你具体准备做什么。')
    # An estimate call cannot turn unknown constraints into absent constraints.
    revised=validate_estimate(dict(value,focused_minutes_min=result.min_focus_minutes,
        focused_minutes_max=result.max_focus_minutes,recommended_minutes=result.recommended_minutes,
        rationale=result.basis,assumptions=result.assumptions))
    from src.material_estimate_contract import refresh_final_estimate
    return replace(draft,estimate_fallbacks=tuple(refresh_final_estimate(dict(v,estimate=revised,effort_ledger=None,effort_grounding=None,effort_provenance=None,
        simple_confirmation_allowed=v['simple_confirmation_allowed'] and not (result.location_text or result.deadline_time),
        scope_confirmation_allowed=v.get('scope_confirmation_allowed', False) and not (result.location_text or result.deadline_time),
        confirmation_fields=tuple(sorted(set(v.get('confirmation_fields', ()))
            | ({'location'} if result.location_text else set())
            | ({'deadline'} if result.deadline_time else set()))))) if v['item_id']==item_id
        else v for v in draft.estimate_fallbacks))
