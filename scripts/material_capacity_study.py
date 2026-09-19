"""Isolated capacity experiment or explicit current-default acceptance run.

Only experimental runs temporarily patch file text/page admission constants. The complete
formal pipeline, model payloads, guards, repairs and adoption remain unchanged.
Generated corpus and responses stay in ignored evaluation storage.
"""
import argparse
from contextlib import contextmanager, nullcontext
import json
import os
import re
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.reliability_live import official_hashes,write


def context_error_metadata(text):
    """Only recognized limit numbers, never provider body, prompts or headers."""
    result=dict(context_limit_signal=bool(re.search(r'context.{0,60}(?:exceed|limit|length)|maximum.{0,30}tokens|too many tokens',text,re.I)),error_response_chars=len(text))
    patterns={
        'maximum_context_tokens':r'maximum context length(?:\s+is|\s*[:=])?\s*(\d+)\s*tokens',
        'requested_context_tokens':r'(?:requested|resulted in)\s*(\d+)\s*tokens',
        'message_context_tokens':r'(\d+)\s+(?:tokens\s+)?in (?:the )?messages',
    }
    for name,pattern in patterns.items():
        match=re.search(pattern,text,re.I)
        if match:result[name]=int(match[1])
    return result


@contextmanager
def experimental_capacity(chars,pages):
    from src import file_material
    if type(chars) is not int or not 12000<=chars<=110000:raise ValueError('invalid study bound')
    if type(pages) is not int or not 20<=pages<=100:raise ValueError('invalid study pages')
    with patch.object(file_material,'MAX_TEXT_CHARS',chars),patch.object(file_material,'MAX_PDF_PAGES',pages):
        yield


def run(corpus,destination,ids,limit,pages):
    from scripts import reliability_material_core as runner
    class CapacityMeter(runner.Meter):
        def request(self,session,method,url,**kwargs):
            response=super().request(session,method,url,**kwargs)
            row=self.records[-1];body=kwargs.get('json',{})
            row['function_channel']=bool(body.get('tools'))
            if response.status_code==200 and body.get('tools'):
                from src.material_function_transport import NAME,validate_parameters
                from src.material_serialization_repair import strict_object
                from src.material_formal_validation import feedback
                choice=response.json()['choices'][0];calls=choice['message'].get('tool_calls') or []
                row.update(tool_call_count=len(calls),target_call_count=sum(c.get('function',{}).get('name')==NAME for c in calls),arguments_json_valid=False)
                # Missing auto tool calls can be distinguished without saving
                # model prose or source text. Do not salvage content here.
                content=choice['message'].get('content')
                row['content_shape']=dict(type=type(content).__name__,length=len(content) if isinstance(content,str) else None)
                if isinstance(content,str):
                    row['content_shape']['starts_object']=content.lstrip().startswith('{')
                    row['content_shape']['markdown_fence']='```' in content
                    row['content_shape']['mentions_target_function']=NAME in content
                    row['content_shape']['starts_target_function']=content.lstrip().startswith(NAME)
                    row['content_shape']['tool_call_markup']=any(s in content for s in ('<tool_call>','[TOOL_CALLS]','functions.'))
                    try:
                        value=strict_object(content)
                        row['content_shape']['single_json_object']=True
                        row['content_shape']['recognition_version']=value.get('schema_version')=='campusflow.material-text.v2'
                    except Exception:
                        row['content_shape']['single_json_object']=False
                if len(calls)==1:
                    try:
                        obj=strict_object(calls[0].get('function',{}).get('arguments'));row['arguments_json_valid']=True
                        validate_parameters(obj);row['function_schema_valid']=True
                    except Exception as exc:row['function_failure']=feedback(exc)
            elif response.status_code!=200:
                # Classify service limits without saving exception/body/headers.
                value=response.text
                row.update(context_error_metadata(value))
            for message in body.get('messages',[])[1:]:
                try:context=json.loads(message.get('content',''))
                except (ValueError,TypeError):continue
                if isinstance(context,dict) and isinstance(context.get('request_stage'),str):
                    row['request_stage']=context['request_stage']
                    if 'repair' in context['request_stage']:row['repair']=True
            runner.save(self.path,self.records);return response
    destination=destination.resolve();destination.mkdir(parents=True,exist_ok=False)
    files=destination/'materials';files.mkdir()
    pass  # No private network snapshot in public checkout.
    manifest=[m for m in json.loads((corpus/'materials.json').read_text(encoding='utf-8')) if m['material_id'] in ids]
    assert len(manifest)==len(set(ids))
    for m in manifest:
        source=corpus/m['file'];shutil.copyfile(source,files/source.name)
        if m['type']=='docx':shutil.copyfile(source.with_suffix('.pdf'),files/source.with_suffix('.pdf').name)
    write(destination/'materials.json',manifest)
    from src import file_material
    write(destination/'capacity-config.json',dict(experimental=limit is not None,text_limit=limit or file_material.MAX_TEXT_CHARS,page_limit=pages if limit is not None else file_material.MAX_PDF_PAGES,
        production_default_text=file_material.MAX_TEXT_CHARS,production_default_pages=file_material.MAX_PDF_PAGES,full_pipeline=True,body_truncated=False))
    before=official_hashes();old_argv=sys.argv[:];old_env=dict(os.environ)
    old_meter,old_out=runner.Meter,runner.OUT
    runner.Meter=CapacityMeter;runner.OUT=destination
    try:
        with experimental_capacity(limit,pages) if limit is not None else nullcontext():
            for m in manifest:
                sys.argv=[runner.__file__,m['material_id'],'--run'];runner.main()
    finally:
        sys.argv=old_argv
        runner.Meter,runner.OUT=old_meter,old_out
        os.environ.clear();os.environ.update(old_env)
        after=official_hashes();write(destination/'safety.json',dict(official_data_unchanged=before==after,protected_file_count=len(after)))
        assert before==after


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--corpus',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--ids',nargs='+',required=True)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--experimental-limit',type=int)
    mode.add_argument('--production-default',action='store_true')
    p.add_argument('--experimental-pages',type=int,default=20)
    a=p.parse_args();run(a.corpus,a.output,a.ids,a.experimental_limit,a.experimental_pages)
