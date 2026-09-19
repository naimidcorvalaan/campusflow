"""JSON Schema export of the existing material contract, not an LLM schema.

Required sets/enums are shared with the formal parser; Feature field defaults
come from its dataclass. Rich cross-field/source validation remains formal code.
"""
from src.material_output_schema import (recognition_template,formal_item_template,
    MATERIAL_REQUIRED,ESTIMATE_REQUIRED,ITEM_REQUIRED,TIME_REQUIRED,ITEM_KINDS,CAMPUS_IDS,COMMITMENT_KINDS)

def _schema(template,key=''):
    if isinstance(template,dict):
        props={k:_schema(v,k) for k,v in template.items()}
        required=list(template)
        if key=='estimate':required=sorted(ESTIMATE_REQUIRED)
        return dict(type='object',properties=props,required=required,additionalProperties=False)
    if isinstance(template,list):
        item=_schema(template[0]) if template else dict(type='string')
        if key=='items':item=_schema(formal_item_template(),'formal_item');item['required'].remove('estimate')
        return dict(type='array',items=item)
    if template is None:return dict(type=['integer','null'])  # ledger included_in
    if template=='boolean':return dict(type='boolean')
    description=str(template);nullable=description.endswith('|null');plain=description.removesuffix('|null') if hasattr(description,'removesuffix') else description[:-5] if nullable else description
    if plain=='integer' or key in ('minutes','units','words') or '正整数' in plain or plain=='1-7':
        result=dict(type=['integer','null'] if nullable else 'integer')
        if plain=='1-7':result.update(minimum=1,maximum=7)
    elif '|' in plain:
        result=dict(type=['string','null'] if nullable else 'string',enum=plain.split('|')+([None] if nullable else []))
    elif key in ('schema_version','completeness'):
        result=dict(type='string',enum=[plain])
    else:result=dict(type=['string','null'] if nullable else 'string')
    result['description']=description
    return result


def _template_schema():
    schema=_schema(recognition_template())
    schema['required']=sorted(MATERIAL_REQUIRED)
    return schema


def feature_schema():
    from dataclasses import fields,MISSING
    from src.material_workload import Feature,RATES,LABELS
    props={f.name:dict(type={str:'string',int:'integer'}[f.type]) for f in fields(Feature)}
    required=[f.name for f in fields(Feature) if f.default is MISSING and f.default_factory is MISSING]
    for f in fields(Feature):
        if f.default is not MISSING:props[f.name]['default']=f.default
    props['kind'].update(enum=list(RATES),description='工作量特征类型，不是effort_ledger.category。'+
        '；'.join(k+'='+LABELS[k] for k in RATES))
    return dict(type='object',properties=props,required=required,additionalProperties=False)


def quantity_schema():
    # Historical optional claim representations use their existing formal
    # field sets/enums; the new prompt never asks the model to copy input facts.
    from src.material_quantity_semantics import FIELDS,SCOPE_FIELDS,OWNER_METRICS,QUALIFIERS,SOURCE_TYPES
    from src.material_quantity_claims import FIELDS as OLD,ENTITIES,AGGREGATIONS
    scopes=dict(aggregation=dict(type='string',enum=sorted(AGGREGATIONS)),
        scope_entity=dict(type=['string','null']),scope_count=dict(type=['integer','null']))
    props=dict(owner=dict(type='string',enum=sorted(OWNER_METRICS)),
        metric=dict(type='string',enum=sorted(set().union(*OWNER_METRICS.values()))),
        qualifier=dict(type='string',enum=sorted(QUALIFIERS)),minimum=dict(type='integer'),maximum=dict(type='integer'),
        source_type=dict(type='string',enum=sorted(SOURCE_TYPES)),**scopes)
    old=dict(entity=dict(type='string',enum=sorted(ENTITIES)),count=dict(type='integer'),**scopes)
    return dict(type='array',description='仅历史兼容；材料事实由上游持有，不要复述。',items=dict(anyOf=[
        dict(type='object',properties=props,required=sorted(FIELDS),additionalProperties=False),
        dict(type='object',properties=old,required=sorted(OLD),additionalProperties=False)]))


def recognition_json_schema():
    schema=_template_schema();props=schema['properties']
    work=props['workload']['items'];work['properties']['features']['items']=feature_schema()
    work['properties']['waiting_note']=dict(type='string',default='')
    # Workload.origin is program provenance, not part of model output.
    from dataclasses import fields,MISSING
    from src.material_workload import Workload
    work['required']=[f.name for f in fields(Workload) if f.default is MISSING and f.default_factory is MISSING]
    action=props['actionability'];action['type']=['object','null']
    from src.material_actionability_sources import ACTION_REQUIRED
    action['required']=sorted(ACTION_REQUIRED)
    action['properties']['reason']=dict(type='string',minLength=1,maxLength=400)
    action['properties']['confirmation_required']['default']=[]
    action['properties']['evidence_refs']['description']='可省略以兼容唯一原文引用；提供时逐字复制正式source refs。'
    item=props['items']['items'];item['required']=sorted(ITEM_REQUIRED)
    for name,values in [('kind',ITEM_KINDS),('campus_id',CAMPUS_IDS),('commitment_kind',COMMITMENT_KINDS)]:
        item['properties'][name]['enum']=list(values)
    for name in ('deadline','start','end'):
        time=item['properties'][name];time['required']=sorted(TIME_REQUIRED);time['type']=['object','null']
    for estimate in (props['estimate'],item['properties']['estimate']):
        estimate['type']=['object','null'];estimate['required']=sorted(ESTIMATE_REQUIRED)
        estimate['properties']['quantity_claims']=quantity_schema()
        # Ledger remains optional and non-null if present; included_in remains
        # required but nullable, exactly as the formal ledger validator.
    return schema


def validate_parameters(value,schema=None,path='$'):
    """Validate the finite JSON Schema subset emitted from our own template.

    The formal parser and all cross-field/source guards still run afterwards.
    """
    from src.material_formal_validation import fields,failure
    schema=schema or recognition_json_schema()
    if 'anyOf' in schema:
        from src.material_formal_validation import FormalValidationError
        for option in schema['anyOf']:
            try:validate_parameters(value,option,path);return
            except FormalValidationError:pass
        failure('invalid_quantity_claim',path,'declared historical quantity representation',value)
    actual='null' if value is None else 'boolean' if type(value) is bool else 'integer' if type(value) is int else 'number' if type(value) is float else 'string' if isinstance(value,str) else 'array' if isinstance(value,list) else 'object' if isinstance(value,dict) else 'other'
    allowed=schema['type'];allowed=[allowed] if isinstance(allowed,str) else allowed
    if actual not in allowed:failure('wrong_type',path,' or '.join(allowed),value)
    if 'enum' in schema and value not in schema['enum']:
        from src.material_formal_validation import FormalValidationError
        error=FormalValidationError('invalid_enum',path,'declared enum',value)
        # Values originate in the formal declaration, never in model text.
        error.feedback['allowed_values']=list(schema['enum'])
        raise error
    if actual=='string' and not schema.get('minLength',0)<=len(value)<=schema.get('maxLength',len(value)):
        failure('invalid_constraint',path,'declared string length',value)
    if actual=='integer' and not schema.get('minimum',value)<=value<=schema.get('maximum',value):
        failure('invalid_constraint',path,'declared numeric bounds',value)
    if actual=='object':
        props=schema['properties'];required=schema['required']
        fields(value,required,path,set(props)-set(required))
        for k,v in value.items():validate_parameters(v,props[k],path+'.'+k)
    elif actual=='array':
        for i,v in enumerate(value):validate_parameters(v,schema['items'],path+'[{}]'.format(i))
