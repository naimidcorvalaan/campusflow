"""Keep verified source requirements immutable at estimate boundaries.

This uses the existing small requirement vocabulary, not a new quantity parser.
Only a unique, validated owner/metric binding may replace a model copy.
"""
import json
import re
from copy import deepcopy
from src.material_quantity_semantics import requirement_value,valid_claim
from src.material_formal_validation import invariant,shape
from src.material_estimate_diagnostics import record


def verified(requirements):
    result={}
    for index,row in enumerate(requirements):
        try:c=requirement_value(row)
        except (KeyError,TypeError,ValueError):c=None
        if (not isinstance(row,dict) or row.get('source')!='explicit_source_requirement'
                or not valid_claim(c) or c['source_type']!='original_fact'):
            invariant('upstream_requirement_valid',['$.material_requirements[{}]'.format(index)],
                'verified original typed requirement with valid owner/metric/value semantics',shape(row))
        key=(c['owner'],c['metric'])
        if key in result and result[key]!=c:
            invariant('upstream_requirement_unique',['$.material_requirements'],
                'one authoritative requirement per owner and metric',dict(requirement_count=len(requirements)))
        result[key]=c
    return result


def bind_copy(basis,assumptions,claims,requirements):
    from src.material_requirements import length_claims,_LENGTH,_QUALIFIER
    source=verified(requirements)
    def text_copy(text):
        if not isinstance(text,str):return text
        edits=[]
        for claim,unit_span in length_claims(text):
            c=source.get((claim['owner'],claim['metric']))
            if not c or claim['source_type']=='derived_estimate':continue
            if all(claim[k]==c[k] for k in ('qualifier','minimum','maximum')):continue
            match=next((m for m in _LENGTH.finditer(text) if m.span(3)==unit_span),None)
            if match is None:continue
            start,end=match.span()
            prefix=re.search('(?:'+_QUALIFIER+r')\s*$',text[:start])
            if prefix:start=prefix.start()
            suffix=re.match(r'\s*(?:左右|上下|以内|以上)',text[end:])
            if suffix:end+=suffix.end()
            q=c['qualifier'];lo,hi=c['minimum'],c['maximum']
            value='{}～{}'.format(lo,hi) if q=='range' else str(lo)
            label={'exact':'','range':'','approximately':'约','minimum':'至少','maximum':'不超过'}[q]
            edits.append((start,end,label+value+match[3]))
        for start,end,value in reversed(edits):text=text[:start]+value+text[end:]
        if edits:record('material_copy','authoritative_requirement','copy_rebound',valid=True,count=len(edits))
        return text
    bound=[] if claims is None else deepcopy(claims)
    if isinstance(bound,list):
        for index,c in enumerate(bound):
            if (isinstance(c,dict) and isinstance(c.get('owner'),str) and isinstance(c.get('metric'),str)
                    and c.get('source_type')!='derived_estimate'):
                original=source.get((c.get('owner'),c.get('metric')))
                if original:bound[index]=deepcopy(original)
    rendered=type(assumptions)(text_copy(a) for a in assumptions) if isinstance(assumptions,(list,tuple)) else assumptions
    return text_copy(basis),rendered,bound


def bind_estimate(value,requirements,basis_key='basis'):
    value=deepcopy(value)
    basis,assumptions,claims=bind_copy(value.get(basis_key),value.get('assumptions'),value.get('quantity_claims'),requirements)
    if basis_key in value:value[basis_key]=basis
    if 'assumptions' in value:value['assumptions']=assumptions
    if 'quantity_claims' in value:value['quantity_claims']=claims
    return value


def bind_response(raw,requirements):
    from src.material_inbox import extract_json_object
    try:obj=extract_json_object(raw)
    except (ValueError,TypeError):return raw  # original parse failure remains diagnostic
    if not isinstance(obj,dict):return raw
    containers=[obj]+[r for r in obj.get('items',[]) if isinstance(r,dict)] if isinstance(obj.get('items',[]),list) else [obj]
    changed=False
    for row in containers:
        if isinstance(row.get('estimate'),dict):
            corrected=bind_estimate(row['estimate'],requirements)
            changed=changed or corrected!=row['estimate'];row['estimate']=corrected
    if not changed:return raw
    encoded=json.dumps(obj,ensure_ascii=False)
    return type(raw)(encoded) if getattr(raw,'strict_material_json',False) else encoded
