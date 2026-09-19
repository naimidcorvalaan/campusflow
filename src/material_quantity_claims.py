"""Typed quantity claims plus a small compatibility bridge for visible copy.

The bridge recognizes only explicit scope markers. Ambiguous copy requests
attribution instead of guessing a total. No source prose enters diagnostics.
"""
import re

from src.material_scope import number
from src.material_quantity_facts import NUM,UNITS

ENTITIES=frozenset(k[:-6] for k in UNITS)
AGGREGATIONS=frozenset(('total','per_region','per_group','per_section','scoped_subset','unknown'))
FIELDS=frozenset(('entity','count','aggregation','scope_entity','scope_count'))
INSTRUCTIONS=(
    ' 每个estimate可包含quantity_claims数组；新版owner/metric/qualifier格式见输出模板。'
    '旧版五字段entity/count/aggregation/scope_entity/scope_count仍兼容原材料实体。'
    '仅旧版格式的entity为record/region/group/section/table/page，count是正整数。'
    'aggregation只能是total/per_region/per_group/per_section/scoped_subset/unknown。'
    'total的scope_entity和scope_count为null；per_region的scope_entity为region，'
    'scope_count为可靠的区域数，未知则null；per_group/per_section同理。'
    'scoped_subset只表示其中一部分，不能冒充总数；无法确认归属用unknown或改成非精确表述。'
    '例如区域数×每区记录数=总记录数，三者数量不同但可同时成立。'
    'basis中也明确写共、每区域、其中等归属；不得在结构写subset而在正文写共。'
    '旧版格式仅描述材料实体；交付长度和写作字数必须用新版带owner和qualifier的格式，不把分钟或动作数当事实。'
)


def claim_format():
    return dict(entity='record|region|group|section|table|page',count='正整数',
        aggregation='total|per_region|per_group|per_section|scoped_subset|unknown',
        scope_entity='region|group|section或null',scope_count='范围实体数量或null')


def _integer(value):
    return type(value) is int and 0<value<=10000000


def _value(facts,key):
    return facts.get(key,{}).get('value')


def _decision(claim,facts):
    entity,count,scope=claim['entity'],claim['count'],claim['aggregation']
    expected=_value(facts,entity+'_count')
    fact=entity+'_count';reason='';decision='accepted'
    if scope=='unknown':
        # Equal to a reliable total is supported without guessing the scope
        # of any conflicting count. A different count needs attribution.
        if expected!=count:
            decision='clarify';reason='quantity_scope_unknown'
    elif scope=='total':
        if expected is None:decision='clarify';reason='unsupported_material_quantity'
        elif expected!=count:decision='rejected';reason='material_quantity_mismatch'
    elif scope=='scoped_subset':
        if expected is None:decision='clarify';reason='unsupported_material_quantity'
        elif count>expected:decision='rejected';reason='subset_exceeds_total'
        else:reason='bounded_subset_not_total'
    else:
        parent=scope[4:]
        parent_total=_value(facts,parent+'_count')
        scope_count=claim['scope_count']
        per_key=entity+'s_per_'+parent
        per=_value(facts,per_key)
        if scope_count is not None and parent_total is not None and scope_count!=parent_total:
            return dict(decision='rejected',mismatch_reason='scope_count_mismatch',
                expected_material_fact=parent+'_count',expected=parent_total)
        if per is not None:
            fact=per_key;expected=per
            if count!=per:decision='rejected';reason='material_quantity_mismatch'
            elif parent_total is not None and _value(facts,entity+'_count') is not None:
                if count*parent_total!=_value(facts,entity+'_count'):
                    decision='rejected';reason='per_scope_total_mismatch'
                else:reason='per_scope_product_matches_total'
        elif parent_total is not None and expected is not None:
            # Only a reliable material parent count can establish this product.
            if count*parent_total!=expected:
                decision='rejected';reason='per_scope_total_mismatch'
            else:reason='per_scope_product_matches_total'
        elif expected is not None:
            # Unknown number of sections: a local amount can fit inside the
            # total, but is never promoted to a verified aggregate fact.
            if count>expected:decision='rejected';reason='subset_exceeds_total'
            else:reason='bounded_local_not_total'
        else:decision='clarify';reason='unsupported_material_quantity'
    return dict(decision=decision,mismatch_reason=reason,
        expected_material_fact=fact,expected=expected)


def _mentions(text,facts):
    patterns=dict(UNITS)
    # Bare “条” is recognized only at a boundary or a review predicate. This
    # does not reinterpret numbered clauses, suggestions or work-action counts.
    patterns['record_count']+=r'|条(?=需要复核|需复核|需检查|[，,。；;）)\s]|$)'
    tokens=sorted((m.start(),key,m) for key,unit in patterns.items()
        for m in re.finditer('('+NUM+r')\s*(?:'+unit+')',text))
    previous_end=0
    for _,key,m in tokens:
        before=text[:m.start()]
        # A modifier belongs to its next quantity, never to a later one.
        # Retain the sentence only to resolve the parent of “各/每组”.
        prefix=re.split(r'[。；;\n，,]',text[previous_end:m.start()])[-1]
        sentence=re.split(r'[。；;\n]',before)[-1]
        suffix=re.split(r'[。；;\n，,]',text[m.end():])[0]
        previous_end=m.end()
        if re.search(r'第\s*$',prefix):continue
        entity=key[:-6];scope='unknown';parent=None;parents=None
        if re.search(r'总计|合计|总共|一共|共有|共|全部|总数|材料有',prefix):scope='total'
        elif re.search(r'每(?:个)?区域|每区|各区域|区域(?:各|每个)',prefix):scope='per_region';parent='region'
        elif re.search(r'每(?:个)?组|各组|每组',prefix):
            parent='region' if '区域' in sentence else 'group'
            scope='per_'+parent
        elif re.search(r'每(?:个)?部分|每节|各部分',prefix):scope='per_section';parent='section'
        elif re.search(r'各\s*$',prefix) and '区域' in sentence:scope='per_region';parent='region'
        elif (re.search(r'其中|抽查|抽样检查|选取|第'+NUM+r'部分',prefix)
                or re.match(r'(?:需要|需)复核',suffix)):
            scope='scoped_subset';parent='section' if re.search(r'第'+NUM+r'部分',prefix) else None
            parents=1 if parent else None
        if scope.startswith('per_'):parents=_value(facts,parent+'_count')
        yield dict(entity=entity,count=number(m[1]),aggregation=scope,
            scope_entity=parent,scope_count=parents),m.span()


def evaluate_quantities(basis,assumptions,facts,claims=None):
    """Return issues, safe observations and copy with only scope clarified.

    Typed claims cannot override explicit total/per-scope wording. For legacy
    copy, the same small scope-marker bridge is used by the final result gate.
    """
    observations=[];issues=[];declared=[]
    def observe(claim,field,origin):
        decision=_decision(claim,facts)
        row=dict(claim,field=field,origin=origin,**decision)
        observations.append(row)
        if decision['decision']!='accepted':
            fact=decision['expected_material_fact']
            issues.append(dict(code=decision['mismatch_reason'],field=field,entity=fact,
                claimed=claim['count'],expected=decision['expected'],aggregation=claim['aggregation'],
                scope_entity=claim['scope_entity'],scope_count=claim['scope_count']))
        return row
    if claims is not None:
        if not isinstance(claims,list) or len(claims)>32:
            issues.append(dict(code='invalid_quantity_claim',field='quantity_claims'))
        else:
            for claim in claims:
                if (not isinstance(claim,dict) or set(claim)!=FIELDS
                        or not isinstance(claim.get('entity'),str) or claim['entity'] not in ENTITIES
                        or not _integer(claim.get('count')) or not isinstance(claim.get('aggregation'),str)
                        or claim['aggregation'] not in AGGREGATIONS
                        or claim.get('scope_entity') not in (None,'region','group','section')
                        or not (claim.get('scope_count') is None or _integer(claim['scope_count']))
                        or (claim['aggregation']=='total' and (claim['scope_entity'] is not None or claim['scope_count'] is not None))
                        or (claim['aggregation'].startswith('per_') and claim['scope_entity']!=claim['aggregation'][4:])):
                    issues.append(dict(code='invalid_quantity_claim',field='quantity_claims'))
                    continue
                declared.append(claim)
                observe(claim,'quantity_claims','structured')
    rendered=[]
    for field,text in [('basis',basis)]+[('assumptions',a) for a in assumptions]:
        replacements=[]
        for claim,span in _mentions(text,facts):
            matches=[c for c in declared if c['entity']==claim['entity'] and c['count']==claim['count']]
            signatures={(c['aggregation'],c['scope_entity'],c['scope_count']) for c in matches}
            if claim['aggregation']=='unknown' and len(signatures)==1:
                claim=dict(matches[0])
                if _decision(claim,facts)['decision']=='accepted':
                    # A validated total needs no cosmetic “共”. Adding it in
                    # front of a parent quantity can change subsequent scope.
                    marker={'total':'','per_region':'每区域','per_group':'每组',
                        'per_section':'每部分','scoped_subset':'其中'}.get(claim['aggregation'],'')
                    if marker:replacements.append((span,marker+text[span[0]:span[1]]))
            elif claim['aggregation']=='unknown':
                # Unattributed prose is an estimation assumption, not a new
                # material-fact record. Only declared claims or explicit
                # total/per/subset wording participate in copy fidelity.
                continue
            elif matches and not any(
                    c['aggregation']==claim['aggregation'] for c in matches):
                issues.append(dict(code='quantity_scope_conflict',field=field,entity=claim['entity']+'_count',
                    claimed=claim['count'],expected=None,aggregation=claim['aggregation'],
                    scope_entity=claim['scope_entity'],scope_count=claim['scope_count']))
            observe(claim,field,'visible_copy')
        for (start,end),replacement in sorted(replacements,reverse=True):
            text=text[:start]+replacement+text[end:]
        rendered.append(text)
    if len(rendered[0])>400 or any(len(a)>200 for a in rendered[1:]):
        issues.append(dict(code='quantity_render_too_long',field='basis'))
    return issues,observations,rendered[0],rendered[1:]
