"""Read-only prompt projections of the formal recognition contract.

No response conversion, additional validator or transport schema lives here.
"""
import math


def nullable(node):
    types=node.get('type',[])
    return types=='null' or isinstance(types,list) and 'null' in types or any(
        nullable(branch) for branch in node.get('anyOf',[]))


def field_states(schema,path='$'):
    result=[]
    for name,node in schema.get('properties',{}).items():
        here=path+'.'+name
        result.append(dict(path=here,required=name in schema.get('required',()),nullable=nullable(node),
            object='properties' in node))
        result.extend(field_states(node,here))
    if isinstance(schema.get('items'),dict):result.extend(field_states(schema['items'],path+'[*]'))
    for branch in schema.get('anyOf',[]):result.extend(field_states(branch,path))
    return result


def enum_glossary():
    from src.material_workload import RATES,LABELS,FEATURE_SEMANTICS
    from src.material_effort import CORE
    lines=['workload.features.kind表示可识别的工作动作，含义如下；不同于effort_ledger.core.category的分钟分解类别。']
    lines.extend(k+'：'+LABELS[k]+'；'+FEATURE_SEMANTICS.get(k,'') for k in RATES)
    ledger_only=sorted(set(CORE)-set(RATES))
    lines.append('仅属于ledger、不能用作workload kind的类别：'+', '.join(ledger_only)+'。按工作动作选择上面已有类别，不创建别名。')
    return '\n'.join(lines)


def policy_guide():
    from src.material_effort_provenance import POLICIES,POLICY_TOTAL_RATIO,POLICY_TOTAL_MAX
    lines=['estimation_policy调整的单项上限=min(绝对上限, floor(core subtotal × 比例))；分钟须为正整数，上限为0时省去该调整。']
    lines.extend(k+'：category='+v['category']+'，比例='+str(v['ratio'])+'，绝对上限='+str(v['maximum_minutes'])+'分钟'
        for k,v in POLICIES.items())
    lines.append('所有policy分钟合计≤min('+str(POLICY_TOTAL_MAX)+', floor(core subtotal × '+str(POLICY_TOTAL_RATIO)+'))。')
    lines.append('超限时缩减至合法范围或删除，必要时修正建议；没有来源不得编造调整，不能改变policy。')
    return '\n'.join(lines)


def derived_guide(schema):
    from src.material_stage_projection import stage_guide
    from src.material_workload import WORKLOAD_COVERAGE_SEMANTICS
    from src.material_output_schema import ITEM_FIELD_SEMANTICS,WORKLOAD_FIELD_SEMANTICS
    item_fields=schema.get('properties',{}).get('items',{}).get('items',{}).get('properties',{})
    item_guide='\n'.join('items[*].'+name+'：'+meaning for name,meaning in ITEM_FIELD_SEMANTICS.items() if name in item_fields)
    work_fields=schema.get('properties',{}).get('workload',{}).get('items',{}).get('properties',{})
    work_guide='\n'.join('workload[*].'+name+'：'+meaning for name,meaning in WORKLOAD_FIELD_SEMANTICS.items() if name in work_fields)
    states=field_states(schema)
    optional=[r['path'] for r in states if not r['required'] and not r['nullable']]
    required_objects=[r['path'] for r in states if r['required'] and r['nullable'] and r['object']]
    required_strings=[r['path'] for r in states if r['required'] and not r['nullable'] and not r['object']
        and any(r['path'].startswith(parent+'.') for parent in required_objects)]
    return '\n'.join([stage_guide(),WORKLOAD_COVERAGE_SEMANTICS,enum_glossary(),item_guide,work_guide,
        '省略与null：required字段不可缺失；optional字段无可靠值时省略；只有类型允许null时才能输出null。'
        '可选不等于可空。',
        '可选且非nullable字段：'+', '.join(dict.fromkeys(optional))+'。',
        '必需但允许null的对象字段：'+', '.join(dict.fromkeys(required_objects))+
        '；无可靠对象时用null。若提供对象，内部required仍必须有合法值，不可用空子字段伪造对象。',
        '上述对象内必须有非null值的字段：'+', '.join(dict.fromkeys(required_strings))+
        '。缺少整个对象的事实时将该对象设为null，不创建内部字段为null的占位对象。',
        policy_guide()])


def policy_repair_context(obj):
    """Numeric observations only; no evidence text or model response retained.

    Read the same registry/formula as validate_row/check_policy_total. This
    context explains existing errors; it never accepts or edits a response.
    """
    from src.material_effort_provenance import POLICIES,POLICY_TOTAL_RATIO,POLICY_TOTAL_MAX
    out=[]
    def walk(node,path='$'):
        if isinstance(node,dict):
            ledger=node.get('effort_ledger')
            if isinstance(ledger,dict):
                core_rows=ledger.get('core',[]);rows=ledger.get('adjustments',[])
                if isinstance(core_rows,list) and all(isinstance(r,dict) and type(r.get('minutes')) is int for r in core_rows) and isinstance(rows,list):
                    core=sum(r['minutes'] for r in core_rows)
                    policies=[(i,r) for i,r in enumerate(rows) if isinstance(r,dict) and r.get('provenance_type')=='estimation_policy']
                    total=sum(r['minutes'] for _,r in policies if type(r.get('minutes')) is int)
                    for i,row in policies:
                        ref=row.get('provenance_ref');policy=POLICIES.get(ref) if isinstance(ref,str) else None
                        if policy:
                            out.append(dict(path=path+'.effort_ledger.adjustments['+str(i)+'].minutes',policy_name=ref,
                                observed_minutes=row.get('minutes') if type(row.get('minutes')) is int else None,
                                core_subtotal=core,allowed_limit=min(policy['maximum_minutes'],math.floor(core*policy['ratio'])),
                                policy_total_observed=total,policy_total_limit=min(POLICY_TOTAL_MAX,math.floor(core*POLICY_TOTAL_RATIO))))
            for k,v in node.items():walk(v,path+'.'+k)
        elif isinstance(node,list):
            for i,v in enumerate(node):walk(v,path+'['+str(i)+']')
    walk(obj)
    return out
