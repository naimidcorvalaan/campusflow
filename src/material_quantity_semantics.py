"""Shared owner/metric/value semantics; legacy aggregation stays independent."""
QUALIFIERS=frozenset(('exact','range','approximately','minimum','maximum'))
SOURCE_TYPES=frozenset(('original_fact','derived_estimate','workload_feature'))
SUBJECT_OWNERS={'report_body':'deliverable_body','paper':'deliverable_paper','application':'deliverable_application',
    'discussion':'subtask_discussion','abstract':'subtask_abstract','conclusion':'subtask_conclusion',
    'explanation':'subtask_explanation','source_document':'source_document'}
MATERIAL_OWNERS={'page':'source_document',**{k:'material_'+k for k in ('region','record','group','section','table')}}
OWNER_METRICS={v:frozenset(('page_count','word_count')) for v in SUBJECT_OWNERS.values()}
OWNER_METRICS.update({owner:frozenset((entity+'_count',)) for entity,owner in MATERIAL_OWNERS.items()})
FIELDS=frozenset(('owner','metric','qualifier','minimum','maximum','source_type'))
SCOPE_FIELDS=frozenset(('aggregation','scope_entity','scope_count'))

INSTRUCTIONS=(
    ' quantity_claims使用owner/metric/qualifier/minimum/maximum/source_type六字段，aggregation和scope_entity/scope_count仅局部材料数量时附加。'
    'owner可为source_document、deliverable_body、deliverable_paper、deliverable_application、subtask_discussion、subtask_abstract、'
    'subtask_conclusion、subtask_explanation或material_region/record/group/section/table。'
    'metric使用page_count/word_count/region_count/record_count/group_count/section_count/table_count，与owner对应。'
    'qualifier只能为exact/range/approximately/minimum/maximum；range保留原始两个端点，其余minimum=maximum=原始数值。'
    'source_type只能original_fact/derived_estimate/workload_feature。原始事实来自material_facts或material_requirements。'
    '源PDF页数属于source_document；用户要写的正文长度属于deliverable_body，二者绝不可互换或互相比较。'
    '源文件exact页数、正文range页数、讨论approximately字数属于不同对象，可以同时成立。'
    '若内部按区间中值估工作量，只可作为deliverable_body的derived_estimate；不得将中值声明为源文件页数或替换原始区间要求。'
    '对外依据优先复述原始区间与限定词；约某字数可写该字数左右，不可写至少、不少于、必须达到。'
    'legacy entity/count/aggregation/scope_entity/scope_count仅兼容原材料实体，不表示交付要求。'
)


def claim_format():
    return dict(owner='明确对象，来自material_facts/material_requirements',metric='对应metric',
        qualifier='exact|range|approximately|minimum|maximum',minimum='正整数',maximum='正整数',
        source_type='original_fact；仅内部派生估算使用derived_estimate')


def semantic_value(owner,metric,qualifier,minimum,maximum,source_type='original_fact'):
    return dict(owner=owner,metric=metric,qualifier=qualifier,minimum=minimum,maximum=maximum,source_type=source_type)


def requirement_value(row):
    return semantic_value(row.get('owner',SUBJECT_OWNERS[row['subject']]),
        row.get('metric',{'pages':'page_count','words':'word_count'}[row['unit']]),
        row.get('qualifier','range' if row['minimum']!=row['maximum'] else 'exact'),
        row['minimum'],row['maximum'],row.get('source_type','original_fact'))


def valid_claim(c):
    if not isinstance(c,dict) or not FIELDS<=set(c) or set(c)-FIELDS-SCOPE_FIELDS:return False
    if not isinstance(c['owner'],str) or c['owner'] not in OWNER_METRICS:return False
    if not all(isinstance(c[k],str) for k in ('metric','qualifier','source_type')):return False
    if c['metric'] not in OWNER_METRICS[c['owner']]:return False
    if c['qualifier'] not in QUALIFIERS or c['source_type'] not in SOURCE_TYPES:return False
    if not all(type(c[k]) is int and 0<c[k]<=10000000 for k in ('minimum','maximum')):return False
    if c['minimum']>c['maximum']:return False
    if (c['qualifier']=='range') != (c['minimum']!=c['maximum']):return False
    if set(c)&SCOPE_FIELDS:
        if not SCOPE_FIELDS<=set(c):return False
        if c['owner'] not in MATERIAL_OWNERS.values():return False
        from src.material_quantity_claims import AGGREGATIONS
        if not isinstance(c['aggregation'],str) or c['aggregation'] not in AGGREGATIONS or c['scope_entity'] not in (None,'region','group','section'):return False
        if c['scope_count'] is not None and (type(c['scope_count']) is not int or c['scope_count']<=0):return False
    return True


def requirement_decision(claim,requirements):
    expected=[requirement_value(r) for r in requirements
        if requirement_value(r)['owner']==claim['owner'] and requirement_value(r)['metric']==claim['metric']]
    if claim['source_type']=='derived_estimate':
        accepted=any(r['qualifier']=='range' and r['minimum']<=claim['minimum']<=claim['maximum']<=r['maximum'] for r in expected)
        return '' if accepted else 'unsupported_derived_quantity'
    if claim['source_type']!='original_fact':return 'workload_quantity_as_fact'
    if not expected:
        same_metric=[requirement_value(r) for r in requirements if requirement_value(r)['metric']==claim['metric']]
        if any(r['minimum']<=claim['minimum']<=claim['maximum']<=r['maximum'] for r in same_metric):
            return 'requirement_scope_mismatch'
        return 'unsupported_requirement_claim'
    if not any(r['qualifier']==claim['qualifier'] for r in expected):return 'requirement_qualifier_mismatch'
    if not any(r['qualifier']==claim['qualifier'] and r['minimum']==claim['minimum'] and r['maximum']==claim['maximum'] for r in expected):
        return 'requirement_value_mismatch'
    return ''


def legacy_semantics(claim):
    entity=claim['entity']
    return dict(semantic_value(MATERIAL_OWNERS[entity],entity+'_count','exact',claim['count'],claim['count']),
        aggregation=claim['aggregation'],scope_entity=claim['scope_entity'],scope_count=claim['scope_count'])


def to_legacy(claim):
    entity=next(k for k,v in MATERIAL_OWNERS.items() if v==claim['owner'])
    return dict(entity=entity,count=claim['minimum'],aggregation=claim.get('aggregation','total'),
                scope_entity=claim.get('scope_entity'),scope_count=claim.get('scope_count'))
