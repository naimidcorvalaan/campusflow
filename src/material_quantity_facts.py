"""Small source-grounded quantity vocabulary, separate from effort actions.

No general fact extraction or NLP entailment. Only explicit, unambiguous totals
are authoritative; conflicting or hypothetical counts stay unknown. Quotes,
entity names and source text never enter the diagnostic snapshot.
"""
import re
import json

from src.material_scope import number

NUM = r'(?:[0-9]{1,7}|[零〇一二两三四五六七八九十百]{1,5})'
UNITS = {
    'region_count': r'(?:个|类|处)区域',
    'group_count': r'(?:个)?组(?:数据|样本)',
    'record_count': r'(?:条|次)(?:原始)?(?:记录|观测|观察)',
    'page_count': r'页',
    'table_count': r'(?:张|个)(?:数据)?表格',
    'section_count': r'个(?:任务|作业)部分',
}
INSTRUCTIONS = (
    ' material_facts是原材料中已核实的客观数量，包含单位与来源；'
    'workload_action_counts和features.action_count仅是工作动作数，不是记录、区域、组、页或表格数量。'
    '例如复核动作数不能改写为记录数，计算动作数不能改写为区域数。'
    '引用材料实体数量时必须使用material_facts，不得从动作数推导实体数。'
    '没有可靠数量时用“多条记录”“多个区域”等非精确表述；每组数量不能冒充总数。'
    'basis和assumptions均须遵守，工作动作数量仍用于估时。'
)
REPAIR_INSTRUCTIONS = (
    ' 仅修正与material_facts冲突或缺乏来源的实体数量表述，必要时改为非精确表述。'
    'quantity_feedback给出字段、错误值和可用事实；保留已确认scope。'
    '若分钟区间和建议本来合理，保持原值，不必为了修正事实依据重做分钟估计。'
    'quantity_scope_unknown表示数量归属尚不明确，并非总数错误；请明确总共、每组或其中。'
    '每组数量不得直接与全局总量比较，只有明确总量冲突才修正总数。'
)


def source_quantity_facts(text, page_count=0):
    candidates = {}
    def add(key, value, source):
        if 0 < value <= 10000000:
            candidates.setdefault(key, []).append((value, source))
    for clause in re.split(r'[。；;\n]', text):
        # Do not promote possible future work, examples or per-group counts
        # into material-wide facts. This intentionally favors unknown counts.
        if re.search(r'例如|假设|如果|未来|建议增加|计划增加|至少|至多|不超过', clause):
            continue
        for key, unit in UNITS.items():
            if key in ('page_count', 'record_count'):
                continue
            for m in re.finditer('('+NUM+r')\s*'+unit, clause):
                if not re.search(r'第\s*$', clause[:m.start()]):
                    add(key, number(m[1]), 'explicit_source_count')
        for m in re.finditer(r'(?:总计|合计|总共|共有|共|全部)\s*('+NUM+r')\s*'+UNITS['record_count'], clause):
            add('record_count', number(m[1]), 'explicit_source_total')
        for m in re.finditer(r'(?:总行数|记录总数|观测总数)\s*(?:为|是|[:：])?\s*('+NUM+')', clause):
            add('record_count', number(m[1]), 'explicit_source_total')
        for m in re.finditer(r'(?:每(?:个)?区域|各区域|各)(?:有)?\s*('+NUM+r')\s*'+UNITS['record_count'], clause):
            add('records_per_region', number(m[1]), 'explicit_source_per_region')
    if type(page_count) is int and page_count > 0:
        add('page_count', page_count, 'file_metadata')
    from src.material_quantity_semantics import MATERIAL_OWNERS
    return {key: dict(value=values[0][0], unit=key, source=values[0][1],
        owner=MATERIAL_OWNERS.get((key[:-6] if key.endswith('_count') else key),'material_record'),
        metric=key if key.endswith('_count') else 'record_count',qualifier='exact',source_type='original_fact')
        for key, values in candidates.items() if len({v for v, _ in values}) == 1}


def quantity_issues(basis, assumptions, facts, requirements=()):
    from src.material_requirements import evaluate_estimate_facts
    return evaluate_estimate_facts(basis,assumptions,facts,requirements=requirements)[0]


def result_quantity_issues(result, facts, requirements=()):
    return result_quantity_check(result,facts,requirements)[0]


def result_quantity_check(result,facts,requirements=()):
    from src.material_requirements import evaluate_estimate_facts,record_requirement_check
    issues=[];observations=[]
    for entry in result.estimate_fallbacks:
        value = entry['estimate']
        errors,claims,_,_=evaluate_estimate_facts(value['rationale'],value['assumptions'],facts,
            entry.get('quantity_claims'),requirements)
        record_requirement_check('material_copy',value['rationale'],value['assumptions'],requirements)
        issues.extend(errors);observations.extend(claims)
    for item in result.items:
        if item.estimate_basis:
            errors,claims,_,_=evaluate_estimate_facts(item.estimate_basis,item.estimate_assumptions,facts,requirements=requirements)
            record_requirement_check('material_copy',item.estimate_basis,item.estimate_assumptions,requirements)
            issues.extend(errors);observations.extend(claims)
    return issues,observations


def without_conflicting_estimates(raw, facts, requirements=()):
    """An older rejected estimate cannot override a repair during recovery.

    Keep formal facts and explicit source minutes intact; do not invent or
    edit model minutes. Current attempts still undergo full normal parsing.
    """
    from src.p2_agentic_parser import extract_json_object,AgenticParseError
    try:
        obj=extract_json_object(raw)
    except (ValueError,TypeError,AgenticParseError):
        return raw
    if not isinstance(obj,dict):return raw
    containers=[obj]
    if isinstance(obj.get('items'),list):
        containers.extend(row for row in obj['items'] if isinstance(row,dict))
    for container in containers:
        estimate=container.get('estimate')
        if not isinstance(estimate,dict):continue
        basis=estimate.get('basis');assumptions=estimate.get('assumptions')
        if (isinstance(basis,str) and isinstance(assumptions,list)
                and all(isinstance(a,str) for a in assumptions)
                and quantity_issues(basis,assumptions,facts,requirements)):
            container['estimate']=None
    return json.dumps(obj,ensure_ascii=False)
