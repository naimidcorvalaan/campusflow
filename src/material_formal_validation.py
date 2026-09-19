"""Safe, field-level failures from the production material parser."""
import re



def shape(value):
    if value is None:return dict(type='null')
    name='boolean' if type(value) is bool else 'integer' if type(value) is int else 'number' if type(value) is float else 'string' if isinstance(value,str) else 'object' if isinstance(value,dict) else 'array' if isinstance(value,(list,tuple)) else 'other'
    result=dict(type=name)
    if isinstance(value,(str,dict,list,tuple)):result['length']=len(value)
    return result


class FormalValidationError(ValueError):
    def __init__(self,code,path,expected,value=None):
        super().__init__(code)
        self.feedback=dict(code=code,field_path=path,expected=expected,observed=shape(value))
        if code=='invalid_constraint':
            self.feedback.update(invariant_id='field_constraint',involved_field_paths=[path],expected_relation=expected)


def invariant(rule,paths,expected,observed):
    error=FormalValidationError('invalid_constraint',paths[0],expected)
    error.feedback.update(invariant_id=rule,involved_field_paths=paths,
        expected_relation=expected,observed=observed)
    raise error


def extract_material_object(raw):
    from src.material_json import parse
    return parse(raw)


def fields(value,required,path,optional=()):
    if not isinstance(value,dict):raise FormalValidationError('wrong_type',path,'object',value)
    missing=set(required)-set(value)
    if missing:
        key=sorted(missing)[0]
        raise FormalValidationError('missing_field',path+'.'+key,'required field')
    extra=set(value)-set(required)-set(optional)
    if extra:
        key=sorted(extra)[0]
        error=FormalValidationError('unknown_field',path+'.'+key,'declared fields only',value[key])
        error.feedback.update(field_name=key,parent_path=path)
        raise error


def checked(path,expected,value,operation):
    try:return operation()
    except FormalValidationError:raise
    except (ValueError,TypeError,KeyError):
        raise FormalValidationError('invalid_constraint',path,expected,value) from None


def failure(code,path,expected,value=None):
    raise FormalValidationError(code,path,expected,value)


def feedback(error):
    if isinstance(error,FormalValidationError):return error.feedback
    # Code locations are safe; exception messages and local values are not.
    trace=getattr(error,'__traceback__',None);rule='unclassified_validation'
    while trace:
        module=trace.tb_frame.f_globals.get('__name__','')
        if module.startswith('src.'):
            rule=module+'.'+trace.tb_frame.f_code.co_name+':'+str(trace.tb_lineno)
        trace=trace.tb_next
    return dict(code='invalid_constraint',field_path='$.validation',expected='formal material schema',
        invariant_id=rule,involved_field_paths=['$.validation'],expected_relation='validator must accept candidate',
        observed=dict(type=type(error).__name__))


def canonical_envelope(obj):
    """Lossless protocol cleanup only; never invent formal task/time facts.

    Context echoes are not task fields. Optional absent notification metadata
    becomes null; supplied values and every formal row still pass the parser.
    Unknown fields and malformed items remain failures, not user questions.
    """
    if not isinstance(obj,dict):return obj
    from src.material_output_schema import CONTEXT_ECHOES
    allowed_echoes=CONTEXT_ECHOES
    value={k:v for k,v in obj.items() if k not in allowed_echoes}
    value.setdefault('reference_date',None)
    value.setdefault('reference_evidence',None)
    return value
