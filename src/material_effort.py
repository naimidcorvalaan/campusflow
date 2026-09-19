"""Explicit complete effort ledger, with grounded additive adjustments.

Uses the unchanged arithmetic tolerance; it never invents an adjustment or
changes recommended minutes. Legacy estimates retain their old validator.
"""
import hashlib
import json
import re
from src.material_formal_validation import fields,failure
from src.estimate_arithmetic import check_estimate_arithmetic,EstimateArithmeticError
from src.material_estimate_diagnostics import record,_sink
from src.material_effort_provenance import (NEW_FIELDS,LEGACY_FIELDS,TYPES,POLICIES,
    ACTIVE,catalog_for,validate_row,check_policy_total,REPAIR as PROVENANCE_REPAIR)

CORE={'calculation':'计算','writing':'写作','review':'复核','submission':'提交',
    'code':'代码','chart':'图表','reading':'阅读','setup':'准备','lookup':'查资料','other':'其他工作'}
ADJUST={'uncertainty':'不确定性预留','setup':'额外准备','lookup':'额外查资料',
    'review':'额外复核','user_pace':'个人速度调整','context_switching':'切换工作','rest_buffer':'休息余量'}
LEDGER_FIELDS=frozenset(('completeness','core','adjustments'))
CORE_FIELDS=frozenset(('category','minutes'))
ADJUSTMENT_FIELDS=NEW_FIELDS


def contract():
    return {'effort_ledger':sorted(LEDGER_FIELDS),'core_item':sorted(CORE_FIELDS),
        'adjustment_item':sorted(ADJUSTMENT_FIELDS),'core_categories':sorted(CORE),
        'adjustment_categories':sorted(ADJUST),'provenance_types':sorted(TYPES),'extra_fields':'forbidden'}


INSTRUCTIONS=(
    ' effort_ledger是完整分钟分解：completeness=complete，core列全部核心工作，adjustments列尚未计入的额外工作。'
    '每项category使用模板枚举，minutes为正整数。core与adjustments不重复加总；'
    '若某调整已计入核心项，included_in写该core项从0开始的索引，否则null。'
    'adjustment必须有具体reason、provenance_type、provenance_ref。只引用输入列出的正式ref，不复述evidence。'
    'user_pace仅引用明确速度信息的user_fact；休息余量用rest_buffer和对应有界policy，不能伪称用户速度事实。'
    'policy上限取核心分钟乘比例的向下取整与绝对上限的较小值；所有policy合计也须受policy_total_limit约束。'
    'basis解释工作量，分钟明细以effort_ledger为准，避免在自由文本重复一套分项。'
    '建议在自身区间内；核心subtotal加未包含adjustments应与建议大致一致，区间上下界无需等于分项。'
    'assumptions至多6条，每条不超过200字；basis不超过400字。'
    'core每项严格只有category和minutes，禁止reason/label/description/work_id等字段。'
    'reason/provenance_type/provenance_ref/included_in只属于adjustments，不得复制到core。'
    '如已列任何调整，不得再笼统声称“未计入缓冲/没有余量”；应写明已计入哪些调整，未再增加其他缓冲。'
    'adjustments=[]时不得声称已额外预留具体缓冲分钟。'
)+PROVENANCE_REPAIR
REPAIR=(
    ' 反馈给出suggested、核心subtotal、已解释调整和gap，这些是诊断事实，不是要求补同样数额的buffer。'
    '依据原scope和工作量判断：若确有未计入工作，增加有原文依据的调整；否则降低或修正建议分钟。'
    '不能把完整明细改成示例、删除明细或虚构依据来绕过校验。'
)

def example():
    return dict(completeness='complete',core=[dict(category='|'.join(CORE),minutes='正整数')],
        adjustments=[dict(category='|'.join(ADJUST),minutes='正整数',
            reason='额外工作或有界策略的具体原因；无则adjustments=[]',provenance_type='|'.join(sorted(TYPES)),
            provenance_ref='verified_evidence_refs或allowed_estimation_policies中匹配的ref',included_in=None)])

def digest(ledger):
    return hashlib.sha256(json.dumps(ledger,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def sources_for_workloads(workloads,supplement='',default_context='',user_note='',facts=None,requirements=()):
    return catalog_for(workloads,supplement,default_context,user_note,facts,requirements)

def sources_for_draft(draft):
    if ACTIVE.get() is not None:return ACTIVE.get()
    from src.material_workload import source_workload
    from src.material_requirements import source_requirements
    return catalog_for(source_workload(draft.original_text,draft.supplemental_context),
        draft.supplemental_context,requirements=source_requirements(draft.original_text))

def validate_ledger(ledger,sources=None):
    if sources is not None and sources and all(isinstance(v,str) for v in sources.values()):
        # Explicit old test/session source map compatibility, not new output.
        sources=catalog_for(supplement=sources.get('supplement',''),default_context=sources.get('default_context',''),user_note=sources.get('user_note',''))
    fields(ledger,LEDGER_FIELDS,'$.effort_ledger')
    if ledger['completeness']!='complete':
        failure('incomplete_effort_ledger','$.effort_ledger.completeness','complete',ledger['completeness'])
    for group,labels in [('core',CORE),('adjustments',ADJUST)]:
        rows=ledger[group];path='$.effort_ledger.'+group
        if not isinstance(rows,list) or len(rows)>16 or (group=='core' and not rows):
            failure('invalid_effort_items',path,'1..16 core rows; 0..16 adjustments',rows)
        for i,row in enumerate(rows):
            here=path+'[{}]'.format(i)
            required=CORE_FIELDS if group=='core' else ADJUSTMENT_FIELDS
            if group=='core':fields(row,required,here)
            elif not isinstance(row,dict):fields(row,required,here)
            else:
                allowed=LEGACY_FIELDS if 'evidence_ref' in row or 'evidence' in row else ADJUSTMENT_FIELDS
                # Missing provenance has its own safe diagnosis below.
                fields(row,{'category','minutes','reason','included_in'},here,allowed-{'category','minutes','reason','included_in'})
            if not isinstance(row['category'],str) or row['category'] not in labels:
                failure('invalid_enum',here+'.category','known work category',row['category'])
            if type(row['minutes']) is not int or not 0<row['minutes']<=10080:
                failure('invalid_minutes',here+'.minutes','positive integer <=10080',row['minutes'])
            if group=='adjustments':
                for field in ('reason',):
                    if not isinstance(row[field],str) or not row[field].strip() or len(row[field])>160:
                        failure('missing_adjustment_grounding',here+'.'+field,'nonempty short string <=160',row[field])
                if row['reason'].strip() in ('考虑复杂性','留一点余量','补足差额','凑够建议时间'):
                    failure('unsupported_adjustment_reason',here+'.reason','specific omitted work, not gap filling')
                included=row['included_in']
                if included is not None and (type(included) is not int or not 0<=included<len(ledger['core'])
                        or row['minutes']>ledger['core'][included]['minutes']):
                    failure('invalid_inclusion',here+'.included_in','existing core index; adjustment <= included core',included)
                validate_row(row,i,sum(r['minutes'] for r in ledger['core']),sources)
    core=sum(r['minutes'] for r in ledger['core'])
    adjustments=sum(r['minutes'] for r in ledger['adjustments'] if r['included_in'] is None)
    check_policy_total(ledger['adjustments'],core)
    return core,adjustments

def check_effort(recommended,minimum,maximum,basis,assumptions=(),ledger=None,sources=None,stage='material_estimate'):
    if ledger is None:return check_estimate_arithmetic(recommended,minimum,maximum,basis,assumptions)
    core,extra=validate_ledger(ledger,sources)
    # Canonical numeric bridge into the one existing tolerance implementation.
    text='核心工作({}分钟)'.format(core)
    if extra:text+='；额外缓冲({}分钟)'.format(extra)
    check=check_estimate_arithmetic(recommended,minimum,maximum,text,[])
    # A numeric failure needs the existing estimate repair. Only request a
    # minute-locked copy repair once the numeric structure is itself valid.
    if check.valid:check_copy(ledger,basis,assumptions)
    record(stage,'effort_ledger',check.code,field='effort_ledger',valid=check.valid)
    if _sink.get() is not None:
        from src.material_effort_provenance import diagnostic_provenance
        _sink.get()[-1]['effort_ledger']=dict(completeness='complete',core_subtotal=core,
            explicit_adjustments=extra,total=core+extra,suggested=recommended,
            gap=recommended-core-extra,tolerance=check.tolerance,
            core=[dict(category=r['category'],minutes=r['minutes']) for r in ledger['core']],
            adjustments=[dict(category=r['category'],minutes=r['minutes'],included_in=r['included_in'],
                decision='accepted',**diagnostic_provenance(r))
                for r in ledger['adjustments']])
    return check

def ensure_effort(*args,**kwargs):
    check=check_effort(*args,**kwargs)
    if not check.valid:raise EstimateArithmeticError(check)
    return check


# A small explicit-negation guard, not a prose quantity or entailment parser.
_NEGATION=re.compile(r'(?:未(?:计入|预留|留出|留|加|增加)|没有|不(?:含|包含|计入|留)|无)\s*(?:任何|额外)?\s*(?:缓冲|余量|调整)|\bno\s+(?:extra\s+)?(?:buffer|margin|adjustments?)\b',re.I)
_OTHER=re.compile(r'(?:未|没有|不|无).{0,10}(?:其他|其它)|\bno\s+(?:other|further)\s+(?:buffer|margin)',re.I)
_POSITIVE=re.compile(r'(?:已(?:经)?(?:计入|预留|留出|包含)|另(?:外)?(?:加|预留)|包含|预留).{0,12}?(\d+)\s*分钟.{0,8}?(?:缓冲|余量)|(?:缓冲|余量).{0,5}?(\d+)\s*分钟|\b(?:includes?|reserved?)\s+(\d+)\s*(?:minutes?|mins?)\s*(?:buffer|margin)',re.I)
COPY_REPAIR=('只修effort_copy_conflict指出的basis/assumptions文案，分钟区间、建议、effort_ledger、任务身份和scope必须完全不变。'
    '结构是事实源：已计入调整时明确说明所列调整已包含，可说明未再增加其他缓冲；无调整时不要声称存在具体缓冲。'
    '不要通过改分钟、改ledger或改变范围来修复文字矛盾；仍输出同一完整正式JSON。')


def check_copy(ledger,basis,assumptions):
    adjustments=ledger['adjustments']
    for path,text in [('$.basis',basis)]+[('$.assumptions[{}]'.format(i),a) for i,a in enumerate(assumptions)]:
        if not isinstance(text,str):continue
        for clause in re.split(r'[。；;\n]',text):
            conflict=bool(adjustments and _NEGATION.search(clause) and not _OTHER.search(clause))
            if not adjustments:
                # A denial is not a positive claim; do not turn "未预留30分钟" into one.
                positive=any(any(n is not None and int(n)>0 for n in m.groups()) for m in _POSITIVE.finditer(clause))
                conflict=bool(positive and not re.search(r'未|没有|不|\bno\b',clause,re.I))
            if conflict:
                from src.material_formal_validation import FormalValidationError
                error=FormalValidationError('effort_copy_conflict',path,'copy faithful to explicit adjustments')
                error.feedback.update(copy_only=True,adjustment_count=len(adjustments),
                    adjustment_minutes=sum(r['minutes'] for r in adjustments if r['included_in'] is None))
                raise error


def assert_copy_only(before,after):
    """No hidden numeric/scope change during a repair requested only for prose."""
    from copy import deepcopy
    def locked(obj):
        obj=deepcopy(obj)
        values=[]
        if isinstance(obj,dict):
            if isinstance(obj.get('estimates'),list):values.extend(obj['estimates'])
            if isinstance(obj.get('estimate'),dict):values.append(obj['estimate'])
            if 'recommended_minutes' in obj:values.append(obj)
            if isinstance(obj.get('items'),list):
                values.extend(r.get('estimate') for r in obj['items'] if isinstance(r,dict))
        for value in values:
            if isinstance(value,dict):
                value.pop('basis',None);value.pop('assumptions',None)
        return obj
    if locked(before)!=locked(after):
        failure('copy_repair_changed_estimate','$.estimate','copy repair must retain minutes, ledger, scope and identity')


def repair_feedback(check,recommended,minimum,maximum,ledger=None):
    result=check.feedback(recommended,minimum,maximum)
    if ledger is not None:
        core,extra=validate_ledger(ledger)
        result.update(core_subtotal=core,explicit_adjustments=extra,
            expected_total=core+extra,completeness='complete')
    return result

def render_ledger(ledger):
    if not ledger:return ''
    core,extra=validate_ledger(ledger)
    parts=[CORE[r['category']]+'{}分钟'.format(r['minutes']) for r in ledger['core']]
    adjustments=[ADJUST[r['category']]+'{}分钟（{}{}）'.format(r['minutes'],r['reason'],
        '，已计入核心工作' if r['included_in'] is not None else '') for r in ledger['adjustments']]
    return '核心工作：'+'、'.join(parts)+'，小计{}分钟。'.format(core)+(
        '调整：'+'；'.join(adjustments)+'。' if adjustments else '')+'可解释合计{}分钟。'.format(core+extra)
