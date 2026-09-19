"""Recognition-only, schema-gated extraction of an unambiguous first object."""
import json
import re
from src.material_estimate_diagnostics import record_recognition_boundary,record_formal_failure


def token(text):
    c=text.lstrip()[:1]
    return {'{':'object_open','[':'array_open','}':'object_close',']':'array_close',
        '"':'string_quote',"'":'single_quote',',':'comma',':':'colon','`':'fence'}.get(c,
        'empty' if not c else 'number' if c.isdigit() else 'text')


def delimiters(text):
    stack=[];quoted=False;escape=False;close=0;objects=0;structures=0
    for c in text:
        if quoted:
            if escape:escape=False
            elif c=='\\':escape=True
            elif c=='"':quoted=False
            continue
        if c=='"':quoted=True
        elif c in '{[':stack.append(c)
        elif c in '}]':
            if stack and stack[-1]==('{' if c=='}' else '['):
                stack.pop()
                if not stack:
                    structures+=1
                    if c=='}':objects+=1
            elif c=='}':close+=1
    return dict(unmatched_open_braces=stack.count('{'),unmatched_close_braces=close,
        top_level_object_count_candidate=objects,candidate_structure_count=structures)


def canonical_recognition(raw,validate,observation=None):
    """Never repair syntax or choose between structures. validate is the real parser.

    Rejected responses are retained intact for the existing bounded error path.
    Only an exact standard-JSON prefix, formally valid, may lose inert suffixes.
    """
    if not isinstance(raw,str):return raw
    text=raw.lstrip();start=len(raw)-len(text)
    info=dict(response_length=len(raw),first_complete_object_end_offset=None,
        parser_error_offset=None,trailing_content_length=0,trailing_content_first_token_type='empty',
        trailing_content_contains_json_opener=False,trailing_content_discarded=False,
        **delimiters(text))
    def emit():
        if observation is not None:
            observation.clear();observation.update(info)
        record_recognition_boundary(info)
    if not text.startswith('{'):
        info.update(response_shape='other',decision='defer_to_formal_parser')
        emit();return raw
    try:obj,end=json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError as exc:
        info.update(parser_error_offset=start+exc.pos,response_shape='internal_syntax_error',decision='rejected')
        emit();return raw
    tail=text[end:];stripped=tail.strip()
    info.update(first_complete_object_end_offset=start+end,trailing_content_length=len(tail),
        trailing_content_first_token_type=token(tail),
        trailing_content_contains_json_opener=any(c in tail for c in '{['))
    if not stripped:
        info.update(response_shape='single_object',decision='defer_to_formal_parser')
        emit();return raw
    info['parser_error_offset']=start+end+(len(tail)-len(tail.lstrip()))
    extra_closers=bool(re.fullmatch(r'[}\s]+',tail))
    try:json.JSONDecoder().raw_decode(tail.lstrip());structured_tail=True
    except json.JSONDecodeError:structured_tail=False
    # Ambiguous fragments, revised answers and data-bearing suffixes are not prose.
    inert_prose=(not structured_tail and not any(c in tail for c in '{}[]"\'`:') and not re.search(r'\d',tail)
        and not re.search(r'修正|更正|修订|改为|应为|改成|更新|纠正|修改|correct|revis|instead',tail,re.I)
        and token(tail)=='text')
    info['response_shape']='extra_closing_braces' if extra_closers else 'trailing_prose' if inert_prose else 'structured_or_ambiguous_suffix'
    if not (extra_closers or inert_prose):
        info['second_structure_type']=('object' if token(tail)=='object_open' else 'array'
            if token(tail)=='array_open' else 'structured_fragment')
        info.update(decision='rejected',error_code='ambiguous_multiple_structures')
        emit();return raw
    if getattr(raw,'strict_material_json',False):
        info.update(decision='rejected',error_code='invalid_function_arguments')
        emit();return raw  # Function arguments never use content boundary salvage.
    candidate=text[:end]
    try:validate(candidate)
    except (ValueError,TypeError,KeyError) as exc:
        record_formal_failure(exc,'material_recognition','boundary_candidate')
        info.update(decision='schema_rejected')
        emit();return raw
    info.update(decision='canonicalized',trailing_content_discarded=True)
    emit()
    return candidate
