"""Recognition-only function channel, using the existing shared output template.

Function arguments remain untrusted. No execution, syntax repair, retries,
content fallback or business decisions belong in this transport.
"""
NAME='material_recognition_result'


class ToolArguments(str):
    strict_material_json=True


def transport_failure(error):
    """Read safe formal causes through the existing provider exception wrapper."""
    from src.material_formal_validation import FormalValidationError
    seen=set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error,FormalValidationError) and error.feedback['code'] in (
                'missing_tool_call','multiple_tool_calls','wrong_tool_name','wrong_arguments_type'):
            return error
        error=getattr(error,'__cause__',None)
    return None


def function_definition():
    from src.material_stage_projection import recognition_stage_schema
    from src.material_tool_schema import recognition_tool_schema
    return dict(name=NAME,description='提交唯一最终材料识别结果；仅承载数据，不执行任务或发布计划。',parameters=recognition_tool_schema(recognition_stage_schema()))


def validate_parameters(value):
    from src.material_stage_projection import validate_stage_parameters
    # Both guards apply. Full historical parser remains unchanged; the stage
    # subset is stricter, not a permissive function-only parser.
    validate_stage_parameters(value)


def extract_arguments(data):
    from src.material_formal_validation import failure
    from src.material_estimate_diagnostics import record,_sink
    choices=data.get('choices') if isinstance(data,dict) else None
    choice=choices[0] if isinstance(choices,list) and len(choices)==1 and isinstance(choices[0],dict) else {}
    message=choice.get('message');message=message if isinstance(message,dict) else {}
    calls=message.get('tool_calls')
    if calls is None:calls=[]
    count=len(calls) if isinstance(calls,list) else None
    code='accepted'
    if count!=1:code='missing_tool_call' if count==0 else 'multiple_tool_calls'
    elif (not isinstance(calls[0],dict) or calls[0].get('type')!='function'
            or not isinstance(calls[0].get('function'),dict)
            or calls[0]['function'].get('name')!=NAME):code='wrong_tool_name'
    elif not isinstance(calls[0]['function'].get('arguments'),str):code='wrong_arguments_type'
    record('material_function_transport','tool_call',code,valid=code=='accepted')
    if _sink.get() is not None:_sink.get()[-1]['function_transport']=dict(tool_call_count=count,
        target_name_valid=code not in ('wrong_tool_name','missing_tool_call','multiple_tool_calls'),
        finish_reason=choice.get('finish_reason') if choice.get('finish_reason') in ('stop','tool_calls','length') else 'other',decision=code)
    if code!='accepted':failure(code,'$.message.tool_calls','exactly one target function call')
    return ToolArguments(calls[0]['function']['arguments'])


def call_recognition(system,user,message_call,timeout,max_tokens,content=None):
    from src.material_output_schema import recognition_semantic_guide
    from src.material_stage_projection import prepare_recognition_request
    system,user,content=prepare_recognition_request(system,user,content)
    # The complete material context is unchanged. Existing JSON instructions
    # describe arguments; they do not request a second copy in message.content.
    instructions=('\n结构输出通道：必须且只调用一次 '+NAME+'；把上述唯一正式JSON结果放在函数arguments中，'
        '不要在message.content重复JSON或解释，不要调用其他函数。normal和repair使用同一函数契约。')
    guide='' if 'campusflow.material-serialization.v1' in system else recognition_semantic_guide()
    return message_call([dict(role='system',content=system+instructions+'\n'+guide),
        dict(role='user',content=user if content is None else content)],
        temperature=0,max_tokens=max_tokens,timeout=timeout,recognition_function=function_definition())
