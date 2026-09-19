"""Stage-owned subset of the unchanged historical formal schema.

Projection applies to schemas, never to model responses. A field returned
outside the stage subset is rejected, not silently removed.
"""
from copy import deepcopy
import json


def project_stage(schema,owners,stage):
    result=deepcopy(schema)
    if set(result['properties'])!=set(owners):
        raise ValueError('Every formal root field must have declared ownership')
    excluded={name for name,owner in owners.items() if owner!=stage}
    if excluded.intersection(result.get('required',())):
        raise ValueError('Cannot project away a required formal field')
    result['properties']={name:value for name,value in result['properties'].items() if name not in excluded}
    return result


def recognition_stage_schema():
    from src.material_output_schema import RECOGNITION_ROOT_OWNERS,RECOGNITION_ITEM_OWNERS
    from src.material_recognition_schema import recognition_json_schema
    schema=project_stage(recognition_json_schema(),RECOGNITION_ROOT_OWNERS,'recognition')
    schema['properties']['items']['items']=project_stage(schema['properties']['items']['items'],RECOGNITION_ITEM_OWNERS,'recognition')
    return schema


def stage_guide():
    from src.material_output_schema import RECOGNITION_ROOT_OWNERS,RECOGNITION_ITEM_OWNERS
    delegated=[prefix+name+' → '+stage for prefix,owners in (('',RECOGNITION_ROOT_OWNERS),('items[*].',RECOGNITION_ITEM_OWNERS)) for name,stage in owners.items() if stage!='recognition']
    return ('阶段所有权：当前函数只产生recognition负责的结果。历史完整模板中以下可选字段由其他阶段负责，'
        '本次不生成：'+'；'.join(delegated)+'。材料识别保留任务事实和workload，专门估时阶段再确定建议分钟。')


def project_prompt_template(template):
    """Project only the declared output template, never a response object."""
    allowed=recognition_stage_schema()['properties']
    from src.material_output_schema import RECOGNITION_ROOT_OWNERS
    if not isinstance(template,dict) or set(template)-set(RECOGNITION_ROOT_OWNERS):
        raise ValueError('Unrecognized formal output template')
    result={key:deepcopy(value) for key,value in template.items() if key in allowed}
    if isinstance(result.get('items'),list):
        result['items']=[project_item_template(item) for item in result['items']]
    return result


def project_item_template(template):
    from src.material_output_schema import RECOGNITION_ITEM_OWNERS
    if not isinstance(template,dict) or set(template)-set(RECOGNITION_ITEM_OWNERS):
        raise ValueError('Unrecognized formal item template')
    return {key:deepcopy(value) for key,value in template.items() if RECOGNITION_ITEM_OWNERS[key]=='recognition'}


def prepare_recognition_request(system,user,content=None):
    """Align trusted prompt/template artifacts with the stage schema.

    Match shared prompt blocks exactly; don't scan or rewrite material text.
    Multimodal attachments and repair's previous untrusted response stay intact.
    """
    from src.material_recognition_prompts import recognition_system_prompt
    from src.material_output_schema import output_instructions
    for before,after in ((recognition_system_prompt(),recognition_system_prompt('recognition')),
                         (output_instructions(),output_instructions(stage='recognition'))):
        system=system.replace(before,after)
    prepared=user
    try:context=json.loads(user)
    except (ValueError,TypeError):context=None
    if isinstance(context,dict) and 'output' in context:
        context['output']=project_prompt_template(context['output'])
        if isinstance(context.get('formal_item_format'),dict):
            context['formal_item_format']=project_item_template(context['formal_item_format'])
        prepared=json.dumps(context,ensure_ascii=False)
    if isinstance(context,dict) and context.get('request_stage')=='recognition_serialization_repair':
        # Same function parameters are the only format contract. Retain the
        # failed response and exact parser error, never resend source material.
        context['recognition_contract']=recognition_stage_schema()
        prepared=json.dumps(context,ensure_ascii=False)
    if content is None:return system,prepared,None
    projected=deepcopy(content)
    if isinstance(projected,list):
        for part in projected:
            if isinstance(part,dict) and part.get('type')=='text' and part.get('text')==user:
                part['text']=prepared
    return system,prepared,projected


def validate_stage_parameters(value):
    from src.material_recognition_schema import validate_parameters
    from src.material_output_schema import RECOGNITION_ROOT_OWNERS,RECOGNITION_ITEM_OWNERS
    from src.material_formal_validation import FormalValidationError
    try:validate_parameters(value,recognition_stage_schema())
    except FormalValidationError as exc:
        path=exc.feedback.get('field_path','')
        key=path[2:] if path.startswith('$.') else ''
        import re
        item=re.fullmatch(r'\$\.items\[\d+\]\.([a-z_]+)',path)
        owners=RECOGNITION_ITEM_OWNERS if item else RECOGNITION_ROOT_OWNERS
        if item:key=item.group(1)
        if key in owners and owners[key]!='recognition':
            exc.feedback.update(stage='recognition',field_owner=owners[key],
                expected='field belongs to another stage; omit it from recognition output')
        raise
    validate_parameters(value)  # Original full contract, unchanged.
