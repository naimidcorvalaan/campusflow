"""Explicit length requirements retain their deliverable/subtask owner.

This is a small typed vocabulary, not general-purpose prose entailment. Only
adjacent, explicit subject/length pairs are promoted to structured facts.
Unknown ownership stays in the original material, never becomes a total.
"""
import re
from src.material_scope import number
from src.material_structure import content_text
from src.material_estimate_diagnostics import record
from src.material_quantity_semantics import (SUBJECT_OWNERS,MATERIAL_OWNERS,requirement_value,
    valid_claim,requirement_decision,to_legacy,legacy_semantics,semantic_value)

SUBJECTS = {'正文':'report_body','报告':'report_body','论文':'paper','申请书':'application',
            '讨论':'discussion','摘要':'abstract','结论':'conclusion','说明':'explanation',
            '原始材料':'source_document','源文件':'source_document','材料':'source_document','PDF':'source_document'}
LOCAL = {'discussion','abstract','conclusion','explanation'}
_SUBJECT = '|'.join(SUBJECTS)
_COUNT = r'(?:\d{1,7}|[零〇一二两三四五六七八九十百]{1,5})'
_LENGTH = re.compile(r'(?<!\d)('+_COUNT+r')(?:\s*(?:至|到|[-–—~～])\s*('+_COUNT+r'))?\s*(字|页)')
_QUALIFIER = r'大约|大概|约|至少|不少于|不低于|必须达到|不超过|不得超过|至多|最多|恰好|正好|必须|严格'
_LEFT = re.compile('('+_SUBJECT+r')(?:部分)?\s*(?:应|需|需要|要求|包含|为|写|总计|总共|共|控制在|长度|篇幅|页数|字数|：|:|'+_QUALIFIER+r'|\s){0,12}$')
_RIGHT = re.compile(r'\s*(?:左右|上下|以内|以上)?\s*(?:的|长文本|结果)?\s*('+_SUBJECT+')(?:部分)?')
INSTRUCTIONS = (
    ' material_requirements是从原文核实的交付要求，不是当前输入材料的页数或工作动作数。'
    'subject和scope明确要求归属：overall_deliverable约束整体交付物，subtask仅约束该局部。'
    '例如报告正文页数与讨论字数必须分别保留；局部讨论字数不能改称整份报告字数。'
    'features.total_words仅为对应写作动作的字数，不代表整份交付物长度；以material_requirements的归属为准。'
    '涉及对应交付物时，依据/假设简洁体现整体与局部要求；不得把已完成或所选scope之外的要求加入工作量。'
    '交付物页数的quantity_claims必须属于deliverable_body，不能属于source_document。'
    '归属未知时保留原文语境，不把局部要求升级为整体。'
    'qualifier必须保留：approximately=约/左右，minimum=至少，maximum=不超过，exact=精确，range=原始区间；彼此不能替换。'
)
REPAIR_INSTRUCTIONS = (
    ' requirement_scope_mismatch/requirement_value_mismatch/requirement_qualifier_mismatch指出要求归属、数值或限定词错误。'
    '优先使用原始结构中的owner、metric、qualifier和完整端点重写依据，约数不能改成下限，区间不能改为中值。'
    '依据material_requirements只修正依据/假设，区分整体交付物和局部子任务；合理的建议分钟和区间保持不变。'
)


def length_claims(text):
    for m in _LENGTH.finditer(text):
        left=_LEFT.search(text[max(0,m.start()-30):m.start()])
        right=_RIGHT.match(text[m.end():])
        # “报告包含400字讨论” belongs to discussion, not report.
        subject=SUBJECTS[right[1]] if right else SUBJECTS[left[1]] if left else None
        if not subject:
            continue
        minimum=number(m[1]);maximum=number(m[2]) if m[2] else minimum
        if not 0<minimum<=maximum<=100000:
            continue
        prefix=re.split(r'[。；;，,]',text[:m.start()])[-1]
        suffix=text[m.end():]
        if m[2]:qualifier='range'
        elif re.search(r'(?:至少|不少于|不低于|必须达到)\s*$',prefix) or re.match(r'\s*以上',suffix):qualifier='minimum'
        elif re.search(r'(?:不超过|不得超过|至多|最多)\s*$',prefix) or re.match(r'\s*以内',suffix):qualifier='maximum'
        elif re.search(r'(?:大约|大概|约)\s*$',prefix) or re.match(r'\s*(?:左右|上下)',suffix):qualifier='approximately'
        else:qualifier='exact'
        clause=re.split(r'[。；;，,]',text)[0] if not prefix else prefix+re.split(r'[。；;，,]',suffix)[0]
        derived=bool(re.search(r'(?:按|以|取)\s*(?:约)?\s*$',prefix) and re.search(r'估算|折算|测算',clause))
        yield dict(subject=subject,scope='source_document' if subject=='source_document' else 'subtask' if subject in LOCAL else 'overall_deliverable',
                   unit={'字':'words','页':'pages'}[m[3]],
                   **semantic_value(SUBJECT_OWNERS[subject],{'字':'word_count','页':'page_count'}[m[3]],
                       qualifier,minimum,maximum,'derived_estimate' if derived else 'original_fact')),m.span(3)


def source_requirements(text):
    candidates={}
    # Rejoin physical PDF wraps; preserve sentence boundaries and page locators
    # in the real source/prompt. This copy is solely for requirement extraction.
    for clause in re.split(r'[。；;]',content_text(text)):
        if re.search(r'例如|假设|如果|不要求|无需',clause):
            continue
        for claim,_ in length_claims(clause.replace('\n','')):
            if claim['subject']=='source_document' or claim['source_type']!='original_fact':continue
            key=(claim['subject'],claim['unit'])
            candidates.setdefault(key,[]).append(claim)
    return [dict(values[0],source='explicit_source_requirement') for values in candidates.values()
            if len({(v['minimum'],v['maximum'],v['qualifier']) for v in values})==1]


def requirement_issues(basis,assumptions,requirements):
    issues=[]
    for field,text in [('basis',basis)]+[('assumptions',a) for a in assumptions]:
        for claim,_ in length_claims(text):
            if claim['subject']=='source_document':continue
            relevant=[r for r in requirements if r['unit']==claim['unit']]
            if not relevant:continue
            code=requirement_decision(claim,requirements)
            if code:
                issues.append(dict(code=code,field=field,subject=claim['subject'],unit=claim['unit'],
                    owner=claim['owner'],metric=claim['metric'],qualifier=claim['qualifier'],source_type=claim['source_type'],
                    claimed_min=claim['minimum'],claimed_max=claim['maximum'],
                    expected_requirements=relevant))
    return issues


def evaluate_estimate_facts(basis,assumptions,facts,claims=None,requirements=(),stage='material_copy'):
    """Route known deliverable lengths to their owner check, not file counts.

    Quantity aggregation itself is unchanged. A report's required page length
    and the input file's page_count are different typed facts.
    """
    from src.material_quantity_claims import evaluate_quantities
    errors=requirement_issues(basis,assumptions,requirements)
    legacy=[];typed_observations=[]
    def typed_observe(claim,field):
        if claim['owner'] in MATERIAL_OWNERS.values():
            if claim['source_type']!='original_fact':code='workload_quantity_as_fact'
            elif claim['qualifier']!='exact':code='material_qualifier_mismatch'
            else:
                code=''
                # Reuse the formal per-group/subset/total validator unchanged.
                qerrors,qclaims,_,_=evaluate_quantities('',[],facts,[to_legacy(claim)])
                if qerrors:code=qerrors[0]['code']
            if not code:legacy.append(to_legacy(claim))
        else:code=requirement_decision(claim,requirements)
        typed_observations.append(dict(claim,field=field,decision='rejected' if code else 'accepted',mismatch_reason=code))
        if code:errors.append(dict(code=code,field=field,**{k:claim[k] for k in ('owner','metric','qualifier','minimum','maximum','source_type')}))
    if isinstance(claims,list):
        if len(claims)>32:errors.append(dict(code='invalid_quantity_claim',field='quantity_claims'))
        else:
            for claim in claims:
                if isinstance(claim,dict) and 'owner' in claim:
                    if valid_claim(claim):typed_observe(claim,'quantity_claims')
                    else:
                        errors.append(dict(code='invalid_quantity_claim',field='quantity_claims'))
                        typed_observations.append(dict(claim,field='quantity_claims',decision='rejected',mismatch_reason='invalid_quantity_claim'))
                else:legacy.append(claim)
    elif claims is not None:errors.append(dict(code='invalid_quantity_claim',field='quantity_claims'))
    for field,text in [('basis',basis)]+[('assumptions',a) for a in assumptions]:
        for claim,_ in length_claims(text):
            if claim['owner']=='source_document':typed_observe(claim,field)
            elif any(r['unit']==claim['unit'] for r in requirements):
                code=requirement_decision(claim,requirements)
                typed_observations.append(dict(claim,field=field,decision='rejected' if code else 'accepted',mismatch_reason=code))
    replacements={}
    def protect(text):
        for claim,(start,end) in reversed(list(length_claims(text))):
            if claim['owner']=='source_document' or any(r['subject']==claim['subject'] and r['unit']==claim['unit'] for r in requirements):
                # Single-character typed placeholder: preserve length checks
                # and never collide with content already present in the copy.
                tag=chr(0xE000+len(replacements))
                while tag in basis or any(tag in a for a in assumptions) or tag in replacements:
                    tag=chr(ord(tag)+1)
                replacements[tag]=text[start:end]
                text=text[:start]+tag+text[end:]
        return text
    def restore(text):
        for tag,value in replacements.items():text=text.replace(tag,value)
        return text
    protected_basis=protect(basis)
    protected_assumptions=[protect(a) for a in assumptions]
    qerrors,observations,rendered,rendered_assumptions=evaluate_quantities(
        protected_basis,protected_assumptions,facts,legacy if claims is not None or legacy else None)
    from src.material_estimate_diagnostics import record_semantic_quantities
    record_semantic_quantities(stage,facts,requirements,typed_observations+
        [dict(c,**legacy_semantics(c)) for c in observations],errors+qerrors)
    return errors+qerrors,observations,restore(rendered),[restore(a) for a in rendered_assumptions]


def record_requirement_check(stage,basis,assumptions,requirements):
    errors=requirement_issues(basis,assumptions,requirements)
    if requirements:
        record(stage,'material_requirements',errors[0]['code'] if errors else 'requirements_consistent',
               field=errors[0]['field'] if errors else None,valid=not errors)
    return errors
