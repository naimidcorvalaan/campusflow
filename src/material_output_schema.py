"""Shared output field definitions for recognition and workload estimates.

Input facts belong to the application. Models estimate work; they do not
create a second material-fact store. Historical nested claims are validated
compatibility data, never requested in new output templates.
"""
MATERIAL_REQUIRED=frozenset(('schema_version','reference_date','reference_evidence','items'))
MATERIAL_OPTIONAL=frozenset(('actionability','estimate','coverage','workload'))
# The full historical recognition model includes an early estimate shortcut.
# Ownership controls only the opt-in function stage's generatable subset.
RECOGNITION_ROOT_OWNERS = dict(schema_version='recognition',reference_date='recognition',
    reference_evidence='recognition',items='recognition',actionability='recognition',
    workload='recognition',coverage='recognition',estimate='estimate')
WORKLOAD_REQUIRED=frozenset(('schema_version','estimates'))
ESTIMATE_EXAMPLE=dict(min_focus_minutes='integer|null',max_focus_minutes='integer|null',
    recommended_minutes='integer|null',basis='简短工作量依据|null',assumptions=['必要的估时假设'],
    clarification_question='string|null')
ESTIMATE_REQUIRED=frozenset(ESTIMATE_EXAMPLE)
ESTIMATE_FIELDS=ESTIMATE_REQUIRED|{'effort_ledger'}
WORKLOAD_ESTIMATE_REQUIRED=(ESTIMATE_REQUIRED-{'clarification_question'})|{'work_id'}
WORKLOAD_ESTIMATE_FIELDS=WORKLOAD_ESTIMATE_REQUIRED|{'effort_ledger'}
LEGACY_ESTIMATE_OPTIONAL=frozenset(('quantity_claims','effort_ledger'))
CONTEXT_ECHOES=frozenset(('quantity_claims','material_facts','material_requirements','source_title','required_scope'))
ITEM_REQUIRED=frozenset(('kind','title','scope','completion','evidence','deadline','start','end',
    'location_text','campus_id','commitment_kind','minutes','duration_evidence','uncertainties','possible_task_ref'))
RECOGNITION_ITEM_OWNERS={**dict.fromkeys(ITEM_REQUIRED,'recognition'),'estimate':'estimate'}
ITEM_FIELD_SEMANTICS={
    'evidence':'逐字引用材料中的非空片段，不改写或拼接。',
    'deadline':'仅表达该任务的截止事实；材料没有截止要求时整个对象为null，不构造空时间对象。',
    'start':'仅表达该事项明确要求的开始时间；背景活动时间未知不代表本任务必须有开始时间，没有来源事实时整个对象为null。',
    'end':'仅表达固定安排的结束事实；没有来源事实时整个对象为null，不用估计时间补齐。',
    'uncertainties':'只列真正缺失或不确定的信息；evidence仍引用相关任务的原文片段，不能用占位符表示缺失；没有不确定项用空数组。',
}
WORKLOAD_FIELD_SEMANTICS={
    'scope':'范围摘要与features共同覆盖明确工作动作；交给接收方也属于交付，不能因不在摘要主动作中而遗漏。',
    'features':'每项evidence逐字引用同一个实际原文片段，不拼接改写。units只使用可核实数量；同一动作不重复计数。识别时对照每条原文指令的主动作及后续动作；已在scope/completion等价覆盖则不重复拆项，否则保留有实质工作量的后续步骤。',
}
TIME_REQUIRED=frozenset(('text','date','clock','offset_days','week_offset','weekday'))
ITEM_KINDS=('task','fixed_commitment')
CAMPUS_IDS=(None,'beiyangyuan','weijinlu')
COMMITMENT_KINDS=(None,'class','meeting','other')

# Shared recognition field declarations: the template/type view and semantic
# guide are projections of these same entries, not independent prompt schemas.
CONFIRMATION_SEMANTICS=(
    '只列材料明确提到、尚待确认且影响采用的关键事实。未提截止或地点本身不等于存在未解决的约束，'
    '不因不能排除任意可能性而增加确认项；明确写着待定、冲突或需用户选择的事实仍须保留。'
    '同一作业的多个步骤不等于多个独立事项。'
)
ACTIONABILITY_FIELDS={
    'is_estimatable':('boolean',
        '材料是否已提供足够信息，对用户要完成的任务作有意义的工作量估计。明确的完成、填写、计算、撰写、检查或提交任务，'
        '不因可选细节未定而判为不可估；确实无法辨认用户要做什么时才判为不可估，没有可辨认行为时actionability可为空。'),
    'task_name':('string|null',None),
    'short_scope':('string|null',None),
    'reason':('非空工作量解释，最多400字符','对可估与否的简短判断原因；不是来源证据，也不能代替来源引用。'),
    'evidence':('可选展示文字|null',None),
    'evidence_refs':(['actionability_source_spans中的ref，可多个；无文字span时为空数组'],
        '证明判断来自材料的来源引用。逐字选用本次输入实际提供且支持该任务的source refs，可以引用多个片段；不编造ref，不能用reason替代。'),
    'confirmation_required':(['deadline|location|fixed_arrangement|multiple_actions|existing_task|identity'],CONFIRMATION_SEMANTICS),
    'waiting_note':('不计入专注时间的第三方等待说明|null',None),
}


def actionability_template():
    from copy import deepcopy
    return {name:deepcopy(spec[0]) for name,spec in ACTIONABILITY_FIELDS.items()}


def recognition_semantic_guide(schema=None):
    if schema is None:
        from src.material_stage_projection import recognition_stage_schema
        schema=recognition_stage_schema()
    fields=schema.get('properties',{}).get('actionability',{}).get('properties',{})
    from src.material_recognition_semantics import derived_guide
    return '\n'.join('actionability.'+name+'：'+meaning
        for name,(_,meaning) in ACTIONABILITY_FIELDS.items() if name in fields and meaning)+'\n'+derived_guide(schema)

FACT_INPUT_INSTRUCTIONS=(
    ' normal/repair/recovery只修估时输出，不重建requirements。已验证owner/metric/value/qualifier不可变；'
    '需要引用要求时使用verified_evidence_refs中的正式material_fact ref，不输出要求副本。'
    ' material_facts、material_requirements和上游typed claims仅为只读输入事实，不属于模型输出。'
    '程序会把这些事实原样附着到最终估时对象，模型不需要重新生成或复述quantity_claims。'
    '估时只负责分钟区间、建议、依据和假设；assumptions不是新材料事实。'
    '材料源文件页数与交付物页数属于不同对象，正文区间和局部讨论约数必须保持。'
    '可按已有要求的上界估算工作量，但明确这是估时假设，不得改写原始要求。'
    '例如可写“按正文6～8页的较高工作量估计”；讨论约400字不能改成至少400字。'
    '已验证材料事实和动作计数严格区分：workload_action_counts是操作次数，不是记录、区域、页数或表格数量。'
)

def estimate_example(workload=False):
    value=dict(ESTIMATE_EXAMPLE,assumptions=list(ESTIMATE_EXAMPLE['assumptions']))
    if workload:
        value.pop('clarification_question')
        value['work_id']='来自workloads，逐字复制'
        value.update(min_focus_minutes='正整数',max_focus_minutes='正整数',
            recommended_minutes='区间内整数',basis='非空工作量依据；最多400字')
    from src.material_effort import example
    value['effort_ledger']=example()
    return value


def formal_item_template():
    time=dict(text='原文中的时间短引用',date='YYYY-MM-DD|null',clock='HH:MM|null',
        offset_days='integer|null',week_offset='integer|null',weekday='1-7|null')
    return dict(kind='|'.join(ITEM_KINDS),title='string',scope='string',completion='string',
        evidence='原文短引用',deadline=dict(time),start=dict(time),end=dict(time),
        location_text='string|null',campus_id='|'.join(v for v in CAMPUS_IDS if v)+'|null',
        commitment_kind='|'.join(v for v in COMMITMENT_KINDS if v)+'|null',minutes='integer|null',duration_evidence='原文|null',
        uncertainties=[dict(field='identity|scope|completion|deadline|start|end|location|minutes|general',
            status='uncertain|missing',message='简短说明',evidence='原文短引用')],
        possible_task_ref='已给出的ref|null',estimate=estimate_example())


def recognition_template():
    from src.material_workload import workload_format
    return dict(schema_version='campusflow.material-text.v2',workload=workload_format(),
        actionability=actionability_template(),
        estimate=estimate_example(),coverage=dict(level='whole|partial|unknown',reason='覆盖范围判断依据',
            uncovered_content='未读取或尚未明确的工作内容；无则空字符串'),
        reference_date='YYYY-MM-DD|null',reference_evidence='原文|null',items=[])

def output_instructions(workload=False,stage=None):
    from src.material_effort import INSTRUCTIONS,contract
    from src.material_actionability_sources import INSTRUCTIONS as SOURCE_INSTRUCTIONS
    import json
    if stage=='recognition':
        from src.material_stage_projection import recognition_stage_schema
        root=recognition_stage_schema()['properties']
        return (' normal和repair使用完全相同的阶段字段契约，不得添加未知或非本阶段字段。'
            +SOURCE_INSTRUCTIONS+' 输出根字段仅允许：'+','.join(sorted(root))+'。'
            '通过函数返回唯一完整对象；output是本阶段结果模板，不用output字段包装响应。'
            '输入材料事实和requirements是只读来源，不能重建副本或改变限定语义。')
    root=WORKLOAD_REQUIRED if workload else MATERIAL_REQUIRED|MATERIAL_OPTIONAL
    nested=WORKLOAD_ESTIMATE_FIELDS if workload else ESTIMATE_FIELDS
    boundary=(' JSON对象闭合后立即结束；不要第二份修正版、重复JSON或追加总结。' if not workload else '')
    return (boundary+' 只能返回一个完整JSON object；不要Markdown代码块、解释、前言或后记。'
        '属性名和字符串使用JSON双引号，正确转义字符串内引号/换行；检查所有括号闭合。'
        'normal和repair使用完全相同的正式字段契约，不得添加未知字段。'
        +('' if workload else SOURCE_INSTRUCTIONS)+
        ' 输出根字段仅允许：'+','.join(sorted(root))+'。'
        '估时对象字段仅使用：'+','.join(sorted(nested))+'。'
        '不要输出material_facts/material_requirements/quantity_claims或其他输入上下文字段；'
        '不要用output字段包装响应。输入中的output是完整输出模板。'+FACT_INPUT_INSTRUCTIONS+INSTRUCTIONS+
        ' 嵌套ledger的唯一字段约束（不是输出字段）：'+json.dumps(contract(),ensure_ascii=False))

def authoritative_claims(facts,requirements,existing=()):
    from src.material_quantity_semantics import MATERIAL_OWNERS,semantic_value,requirement_value,legacy_semantics
    result=list(existing or ())
    def attach(claim):
        for old in result:
            normalized=legacy_semantics(old) if isinstance(old,dict) and 'entity' in old else old
            if isinstance(normalized,dict) and all(normalized.get(k)==v for k,v in claim.items()) and normalized.get('aggregation','total')=='total':
                return
        result.append(claim)
    for key,fact in facts.items():
        entity=key[:-6] if key.endswith('_count') else ''
        if entity in MATERIAL_OWNERS:
            attach(semantic_value(MATERIAL_OWNERS[entity],key,'exact',fact['value'],fact['value']))
    for row in requirements:attach(requirement_value(row))
    return result


def render_source_facts(facts,requirements):
    """Render original typed facts only, separately from model assumptions."""
    labels={'source_document':'源文件','deliverable_body':'正文','deliverable_paper':'论文',
        'deliverable_application':'申请书','subtask_discussion':'讨论','subtask_abstract':'摘要',
        'subtask_conclusion':'结论','subtask_explanation':'说明','material_region':'材料区域',
        'material_record':'材料记录','material_group':'材料分组','material_section':'材料部分',
        'material_table':'材料表格'}
    units={'page_count':'页','word_count':'字','region_count':'个','record_count':'条',
        'group_count':'组','section_count':'部分','table_count':'个'}
    parts=[]
    for c in authoritative_claims(facts,requirements):
        owner=c['owner'];metric=c['metric'];q=c['qualifier']
        if owner not in labels or metric not in units:continue
        lo,hi=c['minimum'],c['maximum']
        quantity='{}～{}'.format(lo,hi) if q=='range' else str(lo)
        prefix={'exact':'','approximately':'约','minimum':'至少','maximum':'不超过','range':''}[q]
        if owner=='source_document' and q=='exact':prefix='共'
        parts.append(labels[owner]+prefix+quantity+units[metric])
    return '；'.join(parts)
