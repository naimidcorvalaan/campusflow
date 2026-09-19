"""Pure TJU schema-expression compatibility, never response conversion.

The nullable-object union and four annotation-only metadata keys are adapted.
Business validators consume the unchanged formal schema and argument values.
"""
from copy import deepcopy

ANNOTATIONS=frozenset(('title','description','default','examples'))


def schema_children(schema):
    """Schema positions only; property names and enum/default data are not keywords."""
    if not isinstance(schema,dict):return
    for key in ('properties','patternProperties','$defs','definitions','dependentSchemas'):
        if isinstance(schema.get(key),dict):
            for name,value in schema[key].items():yield (key,name),value
    for key in ('items','additionalItems','additionalProperties','contains','not','if','then','else','propertyNames'):
        if isinstance(schema.get(key),dict):yield (key,),schema[key]
        elif key=='items' and isinstance(schema.get(key),list):
            for index,value in enumerate(schema[key]):yield (key,index),value
    for key in ('anyOf','oneOf','allOf','prefixItems'):
        if isinstance(schema.get(key),list):
            for index,value in enumerate(schema[key]):yield (key,index),value
    if isinstance(schema.get('dependencies'),dict):
        for name,value in schema['dependencies'].items():
            if isinstance(value,dict):yield ('dependencies',name),value


def strip_annotations(schema):
    """Four annotation-only keywords, without deleting same-named business fields."""
    result=deepcopy(schema)
    if not isinstance(result,dict):return result
    for path,child in list(schema_children(result)):
        parent=result
        for part in path[:-1]:parent=parent[part]
        parent[path[-1]]=strip_annotations(child)
    for key in ANNOTATIONS:result.pop(key,None)
    return result


def recognition_tool_schema(formal):
    """The opt-in tool export; never used to interpret or clean a response."""
    return strip_annotations(tool_schema(formal))


def tool_schema(formal):
    value=deepcopy(formal)
    if not isinstance(value,dict):return value
    for key in ('properties','patternProperties','$defs','definitions','dependentSchemas'):
        if isinstance(value.get(key),dict):
            value[key]={name:tool_schema(child) for name,child in value[key].items()}
    for key in ('items','additionalItems','additionalProperties','contains','not','if','then','else','propertyNames'):
        if isinstance(value.get(key),dict):value[key]=tool_schema(value[key])
        elif key=='items' and isinstance(value.get(key),list):value[key]=[tool_schema(x) for x in value[key]]
    for key in ('anyOf','oneOf','allOf','prefixItems'):
        if isinstance(value.get(key),list):value[key]=[tool_schema(x) for x in value[key]]
    if isinstance(value.get('dependencies'),dict):
        value['dependencies']={k:tool_schema(v) if isinstance(v,dict) else v for k,v in value['dependencies'].items()}
    types=value.get('type')
    if not (isinstance(types,list) and len(types)==2 and set(types)=={'object','null'}):return value
    value.pop('type')
    branch={'type':'object'}
    for key in ('properties','patternProperties','required','additionalProperties','propertyNames',
                'minProperties','maxProperties','dependencies','dependentRequired','dependentSchemas'):
        if key in value:branch[key]=value.pop(key)
    union=[branch,{'type':'null'}]
    # General constraints (enum/const/ref/combinators) must also constrain null.
    # Keep them outside; metadata and unknown annotations are preserved too.
    if 'anyOf' in value:value.setdefault('allOf',[]).append({'anyOf':union})
    else:value['anyOf']=union
    return value
