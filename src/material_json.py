"""Single-object JSON boundary and text-free syntax diagnostics for materials."""
import json
import re


def _scan(text):
    stack=[];quoted=False;escape=False;mismatch=False;trailing=[]
    for i,ch in enumerate(text):
        if quoted:
            if escape:escape=False
            elif ch=='\\':escape=True
            elif ch=='"':quoted=False
            continue
        if ch=='"':quoted=True
        elif ch in '{[':stack.append(ch)
        elif ch in '}]':
            if not stack or stack[-1]!=('{' if ch=='}' else '['):mismatch=True
            else:stack.pop()
        elif ch==',' and text[i+1:].lstrip().startswith(('}',']')):trailing.append(i)
    return dict(unclosed_delimiter_count=len(stack),delimiter_mismatch=mismatch,
        unterminated_string=quoted),trailing


def parse(raw):
    from src.material_formal_validation import invariant,shape
    text=raw.strip() if isinstance(raw,str) else ''
    strict=bool(getattr(raw,'strict_material_json',False))
    fenced=not strict and bool(re.fullmatch(r'```(?:json)?\s*\n[\s\S]*\n```',text,re.I))
    payload=text.split('\n',1)[1].rsplit('\n',1)[0].strip() if fenced else text
    first=payload[:1]
    state,trailing=_scan(payload)
    observed=dict(shape(raw),response_length=len(raw) if isinstance(raw,str) else 0,
        first_token_type={'{':'object_open','[':'array_open','"':'string',"'":'single_quote'}.get(first,'empty' if not first else 'other'),
        markdown_fence_exists='```' in text,canonical_single_fence=fenced,
        leading_prose=bool(first and first not in '{['),trailing_prose=False,
        opens_object=first=='{',closes_object=payload.endswith('}'),
        appears_truncated=bool(state['unclosed_delimiter_count'] or state['unterminated_string']),
        **state)
    error=None;value=None;canonical=False
    try:
        if strict:
            from src.material_serialization_repair import strict_object
            value=strict_object(payload)
        else:value=json.loads(payload)
    except (ValueError,TypeError) as exc:
        error=exc
        if trailing and not strict:
            # The project's trailing-comma compatibility, only outside strings.
            cleaned=''.join(ch for i,ch in enumerate(payload) if i not in set(trailing))
            try:value=json.loads(cleaned);error=None;canonical=True
            except json.JSONDecodeError:pass
    if error is None and isinstance(value,dict):
        if strict:
            from src.material_function_transport import validate_parameters
            validate_parameters(value)
        return value
    kind='wrong_top_level_type'
    if error:
        msg=getattr(error,'msg','')
        kind={'Extra data':'extra_data','Expecting property name enclosed in double quotes':'invalid_property_quotes_or_comma',
            "Expecting ',' delimiter":'missing_comma_or_closing_delimiter',
            "Expecting ':' delimiter":'missing_colon','Expecting value':'expected_value'}.get(msg,
            'unterminated_string' if msg.startswith('Unterminated string') else
            'control_character' if msg.startswith('Invalid control character') else 'invalid_json_syntax')
        if msg=='Extra data':
            tail=payload[error.pos:].lstrip()
            kind='multiple_json_objects' if tail.startswith(('{','[')) else 'trailing_prose'
            observed['trailing_prose']=kind=='trailing_prose'
        if first not in ('{','[') and first:kind='leading_prose_or_non_json'
        observed.update(error_offset=getattr(error,'pos',None),error_line=getattr(error,'lineno',None),
            error_column=getattr(error,'colno',None),offset_basis='unfenced_trimmed_payload')
    observed.update(parse_error_type=kind,trailing_comma_present=bool(trailing),
        single_quote_present="'" in payload,typographic_quote_present=any(c in payload for c in '“”‘’'),
        failure_reason='empty_response' if not text else 'object_boundary_missing' if '{' not in payload or '}' not in payload else 'invalid_json_syntax')
    invariant('material_response_json_object',['$.response'],
        'exactly one JSON object; optional single outer fence/trailing commas only',observed)
