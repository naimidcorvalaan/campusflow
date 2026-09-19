"""Opt-in, in-memory development diagnostics. Never stored in user drafts.

Only code-owned labels, validated identifiers and numeric structure may be emitted. In particular,
field values, exception messages and response text are not logged. Exact field
names/paths are retained for formal schema errors.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from collections import Counter
import math
import re

_sink = ContextVar('material_estimate_diagnostics', default=None)


@contextmanager
def capture_estimate_diagnostics():
    events = []
    token = _sink.set(events)
    try:
        yield events
    finally:
        _sink.reset(token)


def record(stage, validator, code, *, field=None, parsed=None, valid=None,
           fallback=False, fallback_reason=None, count=None):
    sink = _sink.get()
    if sink is not None:
        sink.append(dict(stage=stage, validator=validator, code=code,
            field=field, response_parsed=parsed, schema_valid=valid,
            fallback=fallback, fallback_reason=fallback_reason, count=count))


def record_formal_failure(error,stage,attempt=None):
    from src.material_formal_validation import feedback
    safe=feedback(error)
    record(stage,'formal_validation',safe['code'],field=safe['field_path'],parsed=True,valid=False)
    if _sink.get() is not None:
        _sink.get()[-1]['formal_validation']=dict(safe,attempt=attempt,validation_stage=stage)


def record_recognition_boundary(safe):
    record('material_recognition','response_boundary',safe.get('error_code',safe['decision']),
        valid=safe['decision']=='canonicalized')
    if _sink.get() is not None:_sink.get()[-1]['recognition_boundary']=safe


def record_actionability(safe):
    record('material_recognition','actionability_invariant',
        'accepted' if safe['combined_decision'] else 'rejected',valid=safe['combined_decision'])
    if _sink.get() is not None:_sink.get()[-1]['actionability_conditions']=safe


def record_recognition_repair(safe):
    record('material_recognition','recognition_repair',safe['decision'],valid=safe.get('accepted'))
    if _sink.get() is not None:_sink.get()[-1]['recognition_repair']=safe


def record_serialization_repair(safe):
    record('material_serialization_repair','serialization_contract',safe['decision'],valid=safe['decision']=='accepted')
    if _sink.get() is not None:_sink.get()[-1]['serialization_repair']=safe


def record_recognition_prompt(safe):
    record('material_recognition','prompt_shape','observed')
    if _sink.get() is not None:_sink.get()[-1]['prompt_shape']=safe


def record_quantities(stage, facts, action_counts=(), issues=(), claims=()):
    """Only fixed quantity names, numeric values and code-owned provenance."""
    if _sink.get() is None:
        return
    from src.material_quantity_facts import UNITS
    from src.material_quantity_claims import ENTITIES,AGGREGATIONS
    keys=set(UNITS)|{'records_per_region','records_per_group','records_per_section'}
    codes={'material_quantity_mismatch','unsupported_material_quantity','quantity_scope_unknown',
        'subset_exceeds_total','scope_count_mismatch','per_scope_total_mismatch',
        'quantity_scope_conflict','invalid_quantity_claim','quantity_render_too_long'}
    sources={'explicit_source_count','explicit_source_total','explicit_source_per_region','file_metadata'}
    safe_facts={k:dict(value=v['value'],unit=k,source=v['source']) for k,v in facts.items()
        if k in keys and type(v.get('value')) is int and v.get('source') in sources}
    safe_issues=[{k:v for k,v in issue.items() if k in ('code','field','entity','claimed','expected','aggregation','scope_entity','scope_count')}
        for issue in issues if issue.get('entity') in keys|{None} and issue.get('code') in codes
        and issue.get('field') in ('basis','assumptions','quantity_claims')
        and (issue.get('claimed') is None or type(issue.get('claimed')) is int)
        and issue.get('aggregation') in AGGREGATIONS|{None}
        and issue.get('scope_entity') in (None,'region','group','section')
        and (issue.get('scope_count') is None or type(issue.get('scope_count')) is int)
        and (issue.get('expected') is None or type(issue.get('expected')) is int)]
    safe_claims=[{k:v for k,v in c.items() if k in ('entity','count','aggregation','scope_entity','scope_count',
        'field','origin','decision','mismatch_reason','expected_material_fact','expected')}
        for c in claims if c.get('entity') in ENTITIES and type(c.get('count')) is int
        and c.get('aggregation') in AGGREGATIONS and c.get('scope_entity') in (None,'region','group','section')
        and (c.get('scope_count') is None or type(c.get('scope_count')) is int)
        and c.get('field') in ('basis','assumptions','quantity_claims')
        and c.get('origin') in ('structured','visible_copy') and c.get('decision') in ('accepted','clarify','rejected')
        and c.get('mismatch_reason') in codes|{'','bounded_subset_not_total','bounded_local_not_total','per_scope_product_matches_total'}
        and c.get('expected_material_fact') in keys and (c.get('expected') is None or type(c.get('expected')) is int)]
    from src.material_workload import RATES
    safe_actions=[{k:v for k,v in counts.items() if k in {kind+'_actions' for kind in RATES}
        and type(v) is int} for counts in action_counts]
    record(stage,'material_quantity','rejected' if safe_issues else 'observed',valid=not safe_issues)
    _sink.get()[-1]['quantities']=dict(material_facts=safe_facts,
        workload_action_counts=safe_actions,validation_errors=safe_issues,claims=safe_claims)


def _number(value):
    return value if type(value) in (int,float) and abs(value)<=10080 and math.isfinite(value) else None


def record_semantic_quantities(stage,facts,requirements,claims,issues):
    """Closed owner/metric/qualifier vocabulary and numbers; never prose."""
    if _sink.get() is None:return
    from src.material_quantity_semantics import (OWNER_METRICS,MATERIAL_OWNERS,QUALIFIERS,
        SOURCE_TYPES,requirement_value,semantic_value)
    from src.material_quantity_claims import AGGREGATIONS
    codes={'material_quantity_mismatch','material_qualifier_mismatch','invalid_quantity_claim',
        'requirement_scope_mismatch','requirement_value_mismatch','requirement_qualifier_mismatch',
        'unsupported_requirement_claim','unsupported_derived_quantity','workload_quantity_as_fact',
        'quantity_scope_unknown','unsupported_material_quantity','scope_count_mismatch',
        'subset_exceeds_total','per_scope_total_mismatch','quantity_scope_conflict','quantity_render_too_long'}
    metrics=set().union(*OWNER_METRICS.values())
    def safe(row):
        result={}
        for key,allowed in (('owner',set(OWNER_METRICS)),('metric',metrics),('qualifier',QUALIFIERS),
            ('source_type',SOURCE_TYPES),('decision',{'accepted','rejected','clarify'}),
            ('mismatch_reason',codes|{''}),('code',codes),('field',{'basis','assumptions','quantity_claims'}),
            ('aggregation',AGGREGATIONS),('scope_entity',{'region','group','section'})):
            value=row.get(key)
            if isinstance(value,str) and value in allowed:result[key]=value
        for key in ('minimum','maximum','value','scope_count','claimed_min','claimed_max'):
            if type(row.get(key)) is int and 0<=row[key]<=10000000:result[key]=row[key]
        return result
    originals=[]
    for key,value in facts.items():
        entity=(key[:-6] if key.endswith('_count') else key)
        if entity in MATERIAL_OWNERS and type(value.get('value')) is int:
            originals.append(semantic_value(MATERIAL_OWNERS[entity],key,'exact',value['value'],value['value']))
    originals.extend(requirement_value(r) for r in requirements)
    record(stage,'quantity_semantics','rejected' if issues else 'accepted',valid=not issues)
    _sink.get()[-1]['semantic_quantities']=dict(source_facts=[safe(r) for r in originals],
        claims=[safe(c) for c in claims],validation_errors=[safe(e) for e in issues])


def _type(value):
    return ('null' if value is None else 'boolean' if type(value) is bool else
        'integer' if type(value) is int else 'number' if type(value) is float else
        'string' if isinstance(value,str) else 'object' if isinstance(value,dict) else
        'array' if isinstance(value,list) else 'other')


def safe_category(label):
    """A closed vocabulary, never a truncated substring of model/user text."""
    for category,pattern in (
        ('buffer',r'缓冲|返工|机动|buffer'),('lookup',r'查资料|查笔记|查找|lookup'),
        ('setup',r'准备|setup'),('review',r'检查|复核|核对|review'),
        ('submit',r'提交|上传'),('calculation',r'计算|检验|区间|推导'),
        ('writing',r'写|讨论|描述'),('chart',r'图|表'),('code',r'代码|脚本|编程'),
        ('reading',r'阅读'),('core',r'核心|基础工作|分项合计')):
        if re.search(pattern,label,re.I):return category
    return 'work'


def arithmetic_structure(recommended,minimum,maximum,basis,assumptions):
    """Safe minute ledger plus observations that let a developer audit parsing."""
    from src.estimate_arithmetic import check_estimate_arithmetic,_TIME
    check=check_estimate_arithmetic(recommended,minimum,maximum,basis,assumptions)
    parts=[]
    for role,items in (('core',check.parts),('extra',check.extras)):
        for item in items:
            parts.append(dict(label=safe_category(item['label']),role=role,
                min_minutes=item['min'],max_minutes=item['max'],
                suggested_minutes=item['min'] if item['min']==item['max'] else None))
    # Also retain numeric mentions not selected as additive by the parser.
    # Flags/categories are closed vocabulary; source text never leaves here.
    mentions=[]
    for source,text in [('basis',basis)]+[('assumptions',a) for a in assumptions]:
        for clause in re.split(r'[；;。\n]',text):
            for match in _TIME.finditer(clause):
                prefix=re.split(r'[，,、]',clause[:match.start()])[-1]
                label=prefix.strip().rstrip('(:：（ （').strip()
                low=_number(float(match['low']));high=_number(float(match['high'] or match['low']))
                mentions.append(dict(source=source,label=safe_category(prefix),min_minutes=low,max_minutes=high,
                    selected_by_parser=any(p['label']==label and p['min']==low and p['max']==high
                        for p in check.parts+check.extras),
                    parenthesized=bool(re.search(r'[（(]\s*(?:约|大约)?\s*$',prefix)),
                    explicitly_included=bool(re.search(r'已计入|已包含|其中',prefix) or
                        re.match(r'\s*[，,]?\s*已(?:经)?(?:包含|计入)',clause[match.end():])),
                    summary=bool(re.search(r'总计|合计|建议|推荐',prefix)),
                    per_unit=bool(re.search(r'每题|每道|每次|每项|/题',prefix))))
    return dict(check.feedback(recommended,minimum,maximum),applicable=check.applicable,valid=check.valid,
        breakdown_sum_min=sum(p['min'] for p in check.parts+check.extras),
        breakdown_sum_max=sum(p['max'] for p in check.parts+check.extras),
        breakdown=parts,minute_mentions=mentions)


def ledger_snapshot(ledger,stage,suggested,work_id=None,id_mapper=None):
    """Capture safe numeric evidence even when the formal ledger is invalid."""
    from src.material_effort import validate_ledger,CORE,ADJUST,LEDGER_FIELDS,CORE_FIELDS,ADJUSTMENT_FIELDS
    from src.material_formal_validation import feedback
    from src.material_effort_provenance import LEGACY_FIELDS,diagnostic_provenance
    result=dict(ledger_stage=stage,valid=False,work_id=work_id,suggested_minutes=_number(suggested),
        core_item_count=0,adjustment_item_count=0,core=[],adjustments=[],unknown_fields=[])
    if isinstance(ledger,dict):
        result['present_fields']=sorted(ledger)
        result['unknown_fields'].extend('$.effort_ledger.'+k for k in sorted(set(ledger)-LEDGER_FIELDS))
        result['completeness']='complete' if ledger.get('completeness')=='complete' else 'unknown'
        for group,labels,allowed in [('core',CORE,CORE_FIELDS),('adjustments',ADJUST,ADJUSTMENT_FIELDS)]:
            rows=ledger.get(group)
            if not isinstance(rows,list):continue
            result['core_item_count' if group=='core' else 'adjustment_item_count']=len(rows)
            for i,row in enumerate(rows):
                safe=dict(index=i,type=_type(row),work_id=work_id)
                if isinstance(row,dict):
                    safe.update(category=row.get('category') if isinstance(row.get('category'),str) and row['category'] in labels else 'unknown',
                        present_fields=sorted(row),minutes=_number(row.get('minutes')))
                    if id_mapper and 'work_id' in row:safe['work_id']=id_mapper(row['work_id'])
                    for k in ('min_minutes','max_minutes','suggested_minutes'):
                        if k in row:safe[k]=_number(row[k])
                    row_allowed=allowed
                    if group=='adjustments':
                        safe['included_in']=row.get('included_in') if type(row.get('included_in')) is int else None
                        safe.update(diagnostic_provenance(row))
                        if 'evidence' in row or 'evidence_ref' in row:row_allowed=LEGACY_FIELDS
                    result['unknown_fields'].extend('$.effort_ledger.{}[{}].{}'.format(group,i,k) for k in sorted(set(row)-row_allowed))
                result[group].append(safe)
        result['core_subtotal']=sum(r.get('minutes') or 0 for r in result['core'])
        result['explicit_adjustments']=sum(r.get('minutes') or 0 for r in result['adjustments'] if r.get('included_in') is None)
        result['numeric_items_complete']=all(r.get('minutes') is not None for r in result['core']+result['adjustments'])
        result['total']=result['core_subtotal']+result['explicit_adjustments']
        result['gap']=suggested-result['total'] if _number(suggested) is not None else None
    try:validate_ledger(ledger);result['valid']=True
    except (ValueError,TypeError,KeyError) as exc:
        result['validation_error']=feedback(exc)
    return result


class EstimateTrace:
    """One opt-in snapshot per actual response; no raw responses retained.

    Unknown IDs are stable local aliases, so arbitrary text placed in work_id
    cannot exfiltrate a key. Only expected program-generated IDs are shown.
    """
    FIELDS=('work_id','recommended_minutes','min_focus_minutes','max_focus_minutes',
        'basis','assumptions','adjustment_basis','clarification_question','clarification_needed','quantity_claims','effort_ledger')

    def __init__(self,stage,expected_ids=()):
        self.stage=stage
        self.expected=set(expected_ids)
        self.unknown={}
        self.previous=None
        self.attempt=0

    def _id(self,value):
        if value is None:return None
        if not isinstance(value,str):return 'invalid_type'
        if value in self.expected and re.fullmatch(r'[0-9a-f]{24}',value):return value
        if value not in self.unknown:self.unknown[value]='unknown_'+str(len(self.unknown)+1)
        return self.unknown[value]

    def response(self,raw,errors=(),attempt_kind='initial'):
        if _sink.get() is None:return
        from src.p2_agentic_parser import extract_json_object,AgenticParseError
        self.attempt+=1
        parsed=True
        try:obj=extract_json_object(raw)
        except (ValueError,TypeError,AgenticParseError):obj=None;parsed=False
        values=[]
        if isinstance(obj,dict):
            if 'estimates' in obj:
                if isinstance(obj['estimates'],list):values=obj['estimates']
            elif 'estimate' in obj or 'items' in obj:
                if obj.get('estimate') is not None:values.append(obj['estimate'])
                if isinstance(obj.get('items'),list):
                    values.extend(r.get('estimate') for r in obj['items'] if isinstance(r,dict) and r.get('estimate') is not None)
            elif 'recommended_minutes' in obj:values=[obj]
        rows=[]
        for index,value in enumerate(values):
            row=dict(index=index,type=_type(value),work_id=None)
            if isinstance(value,dict):
                row.update(work_id=self._id(value.get('work_id')),
                    field_types={k:_type(value[k]) for k in self.FIELDS if k in value},
                    unknown_field_count=len(set(value)-set(self.FIELDS)),
                    suggested_minutes=_number(value.get('recommended_minutes')),
                    duration_min=_number(value.get('min_focus_minutes')),
                    duration_max=_number(value.get('max_focus_minutes')),
                    has_unexpected_breakdown='breakdown' in value)
                # Observe known numeric shapes even when they are unsupported
                # protocol fields. This never makes them valid parser input.
                if isinstance(value.get('breakdown'),list):
                    row['unexpected_breakdown']=[dict(index=i,type=_type(part),
                        label=safe_category(part.get('label','')) if isinstance(part,dict) and isinstance(part.get('label'),str) else 'work',
                        **{k:_number(part.get(k)) if isinstance(part,dict) else None for k in
                            ('min_minutes','max_minutes','suggested_minutes','minutes')})
                        for i,part in enumerate(value['breakdown'])]
                row['explicit_extra_minutes']={k:_number(value[k]) for k in
                    ('buffer_minutes','review_minutes','lookup_minutes','setup_minutes') if k in value}
                if value.get('effort_ledger') is not None:
                    row['effort_ledger']=ledger_snapshot(value['effort_ledger'],self.stage,
                        value.get('recommended_minutes'),row['work_id'],self._id)
                numbers=[value.get(k) for k in ('recommended_minutes','min_focus_minutes','max_focus_minutes')]
                basis=value.get('basis');assumptions=value.get('assumptions')
                if (all(type(n) is int and 0<n<=10080 for n in numbers) and isinstance(basis,str)
                        and isinstance(assumptions,list) and all(isinstance(a,str) for a in assumptions)):
                    extra=[value['adjustment_basis']] if isinstance(value.get('adjustment_basis'),str) else []
                    numeric=row.get('effort_ledger',{})
                    if 'core_subtotal' in numeric:
                        bridge='核心工作({}分钟)'.format(numeric['core_subtotal'])
                        if numeric['explicit_adjustments']:
                            bridge+='；额外缓冲({}分钟)'.format(numeric['explicit_adjustments'])
                        row['arithmetic']=arithmetic_structure(*numbers,bridge,[])
                    elif value.get('effort_ledger') is None:
                        row['arithmetic']=arithmetic_structure(*numbers,basis,assumptions+extra)
            rows.append(row)
        ids=[r['work_id'] for r in rows if r['work_id'] is not None]
        safe_errors=[{k:v for k,v in error.items() if k in ('code','field','index')} for error in errors]
        snapshot=dict(attempt_id=self.stage+':'+str(self.attempt),attempt_kind=attempt_kind,
            response_parsed=parsed,envelope_type=_type(obj),work_ids=ids,work_id_counts=dict(Counter(ids)),
            estimates=rows,validator_errors=safe_errors)
        if self.previous is not None:
            before=self.previous
            snapshot['repair_diff']=dict(previous_attempt_id=before['attempt_id'],
                object_count_before=len(before['estimates']),object_count_after=len(rows),
                work_id_counts_before=before['work_id_counts'],work_id_counts_after=snapshot['work_id_counts'],
                error_codes_before=[e['code'] for e in before['validator_errors']],
                error_codes_after=[e['code'] for e in safe_errors],
                changed_fields=sorted({k for old,new in zip(before['estimates'],rows) for k in
                    ('suggested_minutes','duration_min','duration_max','arithmetic','field_types') if old.get(k)!=new.get(k)}))
        # Use the normal event envelope so existing diagnostics consumers remain
        # compatible. The nested snapshot is never attached to a user draft.
        record(self.stage,'estimate_snapshot','captured',parsed=parsed)
        _sink.get()[-1]['snapshot']=snapshot
        self.previous=snapshot

    def outcome(self,accepted_ids,fallback_ids):
        if self.previous is None:return
        self.previous['adopted_work_ids']=[self._id(k) for k in accepted_ids]
        self.previous['fallback_work_ids']=[self._id(k) for k in fallback_ids]
        self.previous['fallback']=bool(fallback_ids)
