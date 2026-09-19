"""Session-scoped verified facts and bounded estimation policies for adjustments.

References are program-issued content identities, never model-authored quotes.
Policy bounds are engineering guardrails, not calibrated workload predictions.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
import math
import re
from src.material_formal_validation import FormalValidationError,fields

ACTIVE=ContextVar('material_effort_sources',default=None)
TYPES=frozenset(('material_fact','workload_fact','user_fact','estimation_policy'))
POLICIES={
    'bounded_rest_buffer_v1':dict(category='rest_buffer',ratio=.15,maximum_minutes=15),
    'bounded_context_switching_v1':dict(category='context_switching',ratio=.10,maximum_minutes=15),
    'bounded_uncertainty_v1':dict(category='uncertainty',ratio=.20,maximum_minutes=30),
}
POLICY_TOTAL_RATIO=.25
POLICY_TOTAL_MAX=45
NEW_FIELDS=frozenset(('category','minutes','reason','provenance_type','provenance_ref','included_in'))
LEGACY_FIELDS=frozenset(('category','minutes','reason','evidence_ref','evidence','included_in'))
REPAIR=(' adjustment provenance仅从verified_evidence_refs或allowed_estimation_policies中选择。'
    '真实工作绑定匹配类别的事实ref；通用休息/切换/不确定性使用有界policy，不假造原文证据。'
    '没有事实也不符合policy时删除该调整，必要时重新计算建议与区间；仅绑定来源且分钟仍合理时保持原分钟不变。'
    'policy_limit_exceeded时可缩减已超限项至policy的比例与绝对上限以内，或删除；必要时重算建议与区间。'
    '若单项均合法但policy合计超限，只缩减已有policy分钟直至合计合法；不得增加其他项或改动core。'
    '不得提高上限；保留core、未受本次超限影响的调整及任务范围。只返回一个符合原正式contract的JSON object，不要Markdown或解释。'
    '不要输出evidence/evidence_ref或编造新ref。reason可以自然改写，它不是待逐字验证的证据。')


def _id(kind,value):
    return kind+'_'+hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:24]


def add(catalog,kind,value,categories,description='',legacy=()):
    ref=_id(kind,value)
    catalog[ref]=dict(provenance_type=kind,allowed_categories=sorted(categories),
        description=description,legacy_sources=list(legacy))
    return ref


_CATEGORIES={'review':{'review'},'research':{'lookup'},'attachment':{'setup'},
    'code':{'setup'},'reading_100_words':{'lookup'},'submit':{'review','setup'}}


def catalog_for(workloads=(),supplement='',default_context='',user_note='',facts=None,requirements=()):
    catalog={}
    for work in workloads:
        for i,feature in enumerate(work.features):
            kind='material_fact' if work.origin=='source' else 'workload_fact'
            add(catalog,kind,dict(work_id=work.work_id,index=i,kind=feature.kind,evidence=feature.evidence),
                _CATEGORIES.get(feature.kind,set()),feature.evidence,('material','feature:'+feature.kind))
    for row in requirements:
        add(catalog,'material_fact',row,{'setup','review','lookup'},
            json.dumps(row,ensure_ascii=False),('material',))
    for name,row in (facts or {}).items():
        # Source-file size/page counts are factual context, never a time rate.
        add(catalog,'material_fact',dict(name=name,value=row),set(),json.dumps(row,ensure_ascii=False),('material',))
    for alias,text in [('supplement',supplement),('default_context',default_context),('user_note',user_note)]:
        if not isinstance(text,str) or not text.strip():continue
        categories=set()
        if re.search(r'慢|较快|很快|熟悉|熟练|速度|效率|基础薄弱',text):categories.add('user_pace')
        if re.search(r'复核|检查|核对',text):categories.add('review')
        if re.search(r'查资料|查找|查询|查阅',text):categories.add('lookup')
        if re.search(r'准备|安装|配置',text):categories.add('setup')
        add(catalog,'user_fact',dict(source=alias,text=text),categories,text,(alias,))
    return catalog


def payload(catalog):
    return dict(verified_evidence_refs=[dict(provenance_ref=ref,provenance_type=row['provenance_type'],
        allowed_categories=row['allowed_categories'],fact=row.get('description','')) for ref,row in sorted(catalog.items())],
        allowed_provenance_types=sorted(TYPES),
        allowed_estimation_policies=[dict(provenance_ref=k,provenance_type='estimation_policy',**v)
            for k,v in POLICIES.items()],policy_total_limit=dict(ratio=POLICY_TOTAL_RATIO,maximum_minutes=POLICY_TOTAL_MAX))


@contextmanager
def source_context(catalog):
    token=ACTIVE.set(catalog)
    try:yield
    finally:ACTIVE.reset(token)


def safe_ref(ref):
    if isinstance(ref,str) and (ref in POLICIES or re.fullmatch(r'(?:material_fact|workload_fact|user_fact)_[0-9a-f]{24}',ref)):
        return ref
    return 'unknown_ref'


def reject(row,index,reason,field='provenance_ref'):
    error=FormalValidationError('unverified_adjustment','$.effort_ledger.adjustments[{}].{}'.format(index,field),
        'verified fact reference or bounded allowed estimation policy')
    error.feedback.update(adjustment_index=index,category=row.get('category'),minutes=row.get('minutes'),
        provenance_type=row.get('provenance_type') if isinstance(row.get('provenance_type'),str) and row['provenance_type'] in TYPES else 'unknown',
        provenance_ref=safe_ref(row.get('provenance_ref')),decision='rejected',failure_reason=reason)
    raise error


def validate_row(row,index,core,catalog=None):
    """Explicit legacy migration only with one exact, unambiguous fact match."""
    legacy='evidence_ref' in row or 'evidence' in row
    if legacy:
        fields(row,LEGACY_FIELDS,'$.effort_ledger.adjustments[{}]'.format(index))
        if catalog is None:return  # reading a previously sealed historical ledger
        quote=row.get('evidence');alias=row.get('evidence_ref')
        candidates=[ref for ref,v in catalog.items() if isinstance(quote,str) and quote.strip()
            and alias in v.get('legacy_sources',()) and quote in v.get('description','')
            and row.get('category') in v['allowed_categories']]
        if len(candidates)!=1:reject(row,index,'legacy_free_text_evidence','evidence')
        ref=candidates[0]
        row.pop('evidence');row.pop('evidence_ref')
        row.update(provenance_ref=ref,provenance_type=catalog[ref]['provenance_type'])
        from src.material_estimate_diagnostics import record
        record('material_estimate','adjustment_provenance','legacy_canonicalized',valid=True)
    if 'provenance_type' not in row or 'provenance_ref' not in row:
        reject(row,index,'missing_provenance')
    fields(row,NEW_FIELDS,'$.effort_ledger.adjustments[{}]'.format(index))
    kind=row['provenance_type'];ref=row['provenance_ref']
    if not isinstance(kind,str) or kind not in TYPES:reject(row,index,'wrong_source_type','provenance_type')
    if kind=='estimation_policy':
        if not isinstance(ref,str) or ref not in POLICIES:reject(row,index,'unsupported_policy')
        policy=POLICIES[ref]
        if row['category']!=policy['category']:reject(row,index,'wrong_source_type')
        if row['minutes']>min(policy['maximum_minutes'],math.floor(core*policy['ratio'])):
            reject(row,index,'policy_limit_exceeded','minutes')
    elif catalog is not None:
        if not isinstance(ref,str) or ref not in catalog:reject(row,index,'unknown_ref')
        source=catalog[ref]
        if kind!=source['provenance_type'] or row['category'] not in source['allowed_categories']:
            reject(row,index,'wrong_source_type')


def receipt(catalog,ledger):
    if not ledger:return {}
    # Final validation needs verified identity/type/category, never source text.
    refs={r.get('provenance_ref') for r in ledger['adjustments']}
    return {k:dict(provenance_type=v['provenance_type'],allowed_categories=v['allowed_categories'])
        for k,v in catalog.items() if k in refs}


def check_policy_total(rows,core):
    policies=[(i,r) for i,r in enumerate(rows) if r.get('provenance_type')=='estimation_policy']
    if sum(r['minutes'] for _,r in policies)>policy_total_limit(core):
        i,row=policies[-1];reject(row,i,'policy_limit_exceeded','minutes')


def policy_total_limit(core):
    return min(POLICY_TOTAL_MAX,math.floor(core*POLICY_TOTAL_RATIO))


def diagnostic_provenance(row):
    return dict(provenance_type=row.get('provenance_type') if isinstance(row.get('provenance_type'),str)
        and row['provenance_type'] in TYPES else 'legacy' if 'evidence' in row else 'unknown',
        provenance_ref=safe_ref(row.get('provenance_ref')))


def assert_provenance_repair(before,after):
    """Rebinding cannot reestimate time; deletion may require recomputation."""
    from src.material_effort import assert_copy_only
    from src.material_formal_validation import failure
    def estimates(obj):
        if isinstance(obj,dict) and 'recommended_minutes' in obj:return [obj]
        values=list(obj.get('estimates',[]))
        if isinstance(obj.get('estimate'),dict):values.append(obj['estimate'])
        values.extend(r['estimate'] for r in obj.get('items',[]) if isinstance(r,dict) and isinstance(r.get('estimate'),dict))
        return values
    normalized=deepcopy(after)
    originals=estimates(before);revised=estimates(normalized)
    if len(originals)!=len(revised):failure('provenance_repair_changed_estimate','$.estimate','retain estimate identities')
    for old,new in zip(originals,revised):
        a=old.get('effort_ledger',{});b=new.get('effort_ledger',{})
        original_rows=a.get('adjustments',[]);rows=b.get('adjustments',[])
        if a.get('core')!=b.get('core') or len(rows)>len(original_rows):
            failure('provenance_repair_changed_estimate','$.effort_ledger','retain core work; bind or remove adjustments')
        deleted=len(rows)<len(original_rows)
        policy_reduced=False
        if not deleted:
            core=sum(r.get('minutes',0) for r in a.get('core',[]) if type(r.get('minutes')) is int)
            total_exceeded=sum(r.get('minutes',0) for r in original_rows
                if r.get('provenance_type')=='estimation_policy' and type(r.get('minutes')) is int)>policy_total_limit(core)
            for x,y in zip(original_rows,rows):
                policy=POLICIES.get(x.get('provenance_ref'))
                limit=min(policy['maximum_minutes'],math.floor(core*policy['ratio'])) if policy else 0
                if (policy and x.get('provenance_type')=='estimation_policy' and type(x.get('minutes')) is int
                        and (x['minutes']>limit or total_exceeded) and type(y.get('minutes')) is int
                        and 0<y['minutes']<x['minutes'] and y['minutes']<=limit
                        and all(x.get(k)==y.get(k) for k in ('category','provenance_type','provenance_ref','included_in'))):
                    policy_reduced=True
                elif x.get('minutes')!=y.get('minutes') or x.get('included_in')!=y.get('included_in'):
                    failure('provenance_repair_changed_estimate','$.effort_ledger.adjustments','only reduce policy rows to repair an exceeded individual or total limit')
        if not deleted and not policy_reduced:
            if any(old.get(k)!=new.get(k) for k in ('recommended_minutes','min_focus_minutes','max_focus_minutes')) or any(
                    x.get(k)!=y.get(k) for x,y in zip(original_rows,rows) for k in ('minutes','included_in')):
                failure('provenance_repair_changed_estimate','$.estimate.recommended_minutes','binding alone preserves minutes and range')
        for key in ('effort_ledger','recommended_minutes','min_focus_minutes','max_focus_minutes'):
            if key in old:new[key]=deepcopy(old[key])
    assert_copy_only(before,normalized)
