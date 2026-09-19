"""Frozen candidate, original ten recognition texts, at most one formal repair.

Gold is never sent to the model. No estimation, data persistence or publication.
"""
import hashlib,json,os,re,sys,time
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.fc_fixtures import TEXTS,ENDPOINT,NAME,Done
from src.material_inbox import MaterialInbox,update_source,extract_material,parse_extraction
from src.material_actionability_sources import source_spans,resolve,validate_repaired_action,assert_binding_only
from src.material_recognition_schema import recognition_json_schema,validate_parameters
from src.material_function_transport import function_definition
from src.material_output_schema import recognition_semantic_guide
from src.material_serialization_repair import strict_object
from src.material_formal_validation import feedback
from src.material_estimate_diagnostics import capture_estimate_diagnostics
from src.material_workload import RATES,LABELS,Feature,evidence_matches
from src.p2_tju_live_adapter import TJUP2CallAdapter
DEST=ROOT/'artifacts/evaluation/tju-20260916/function-original-ten-final'
SUBMIT=r'提交|上传|递交|交付|上交'
CHECK=r'检查|核对|复核|校验|审核|审查|校对|检验'
WRITE={'short_text','long_text'}
# Independent acceptance groups, allowing reasonable formal category alternatives.
# Absence of a required operation is a scope failure; an unsupported category is
# an enum-semantics failure. No predicted result is used to revise these sets.
SPECS=[
 ('表格检查', [{'review'},{'submit'}], {'review','submit'}, [r'缺失|缺项|缺漏',CHECK,SUBMIT]),
 ('阅读讨论', [{'reading_100_words'},WRITE,{'submit'}], {'reading_100_words','short_text','long_text','review','submit'}, [r'阅读|读完|研读',r'讨论',SUBMIT]),
 ('申请表', [{'basic_field'},{'review'},{'submit'}], {'basic_field','review','submit','attachment'}, [r'填写|填表|录入',r'姓名|日期|信息',CHECK,SUBMIT]),
 ('均值计算', [{'calculation','problem'},{'review'},{'submit'}], {'calculation','problem','review','submit'}, [r'均值|平均值',r'公式',SUBMIT]),
 ('实验图', [{'chart'},{'submit'}], {'chart','short_text','basic_field','review','submit'}, [r'图|绘制',r'坐标|轴|标注',SUBMIT]),
 ('摘要修订', [WRITE,{'review'},{'submit'}], {'short_text','long_text','reading_100_words','review','submit'}, [r'摘要',r'引用|引文|参考',SUBMIT]),
 ('实验代码', [{'code'},{'review'}], {'code','review'}, [r'运行|执行',r'保存|存储|输出',CHECK]),
 ('访谈提纲', [WRITE,{'review'},{'submit'}], {'short_text','long_text','review','submit'}, [r'提纲',r'顺序|次序|排序',SUBMIT]),
 ('滤波比较', [WRITE,{'submit'}], {'review','calculation','problem','short_text','long_text','reading_100_words','submit'}, [r'两种|两类|2种|不同',r'滤波',r'差异|区别|比较|对比',SUBMIT]),
 ('清单附件', [{'review'},{'attachment'},{'submit'}], {'review','attachment','submit'}, [r'清单',r'附件',r'补齐|补充|补全|完善',SUBMIT]),
]


def gold_rows():
    return [dict(fixture=i+1,label=label,text=text,expected_is_estimatable=True,expected_type='object',
        evidence_role='当前材料唯一原文任务指令段落，须提供支持该任务的来源ref',
        expected_feature_groups=[sorted(g) for g in groups],allowed_feature_kinds=sorted(allowed),scope_patterns=patterns,
        fixed_requirements='无明确截止时间、指定时长或字数要求；保留原文所有任务动作')
        for i,(text,(label,groups,allowed,patterns)) in enumerate(zip(TEXTS,SPECS))]


def freeze_hashes():
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'src').glob('*.py'))}


def audit_candidate(raw,draft,gold):
    from jsonschema import Draft7Validator
    row=dict(json_valid=False,schema_valid=False,parser_valid=False,actionability_executed=False,
        actionability_valid=False,actionability_correct=False,verified_evidence=False,formal_failure=None,
        workload_semantic_correct=False,scope_correct=False,business_pass=False,primary_failure_layer='JSON')
    try:obj=strict_object(raw);row['json_valid']=True
    except (ValueError,TypeError) as exc:row['formal_failure']=feedback(exc);return row
    row['root_estimate_present']='estimate' in obj
    row['item_estimate_count']=sum('estimate' in item for item in obj.get('items',[]) if isinstance(item,dict)) if isinstance(obj.get('items'),list) else None
    action=obj.get('actionability')
    row['actionability_type']='object' if isinstance(action,dict) else 'null' if action is None else 'string' if isinstance(action,str) else 'other'
    row['is_estimatable']=action.get('is_estimatable') if isinstance(action,dict) and type(action.get('is_estimatable')) is bool else None
    row['actionability_correct']=row['is_estimatable'] is gold['expected_is_estimatable']
    reason=action.get('reason') if isinstance(action,dict) else None
    row['reason_valid']=isinstance(reason,str) and 1<=len(reason.strip())<=400
    errors=list(Draft7Validator(recognition_json_schema()).iter_errors(obj))
    row['schema_errors']=[dict(rule=e.validator,path='$'+''.join('['+str(k)+']' if isinstance(k,int) else '.'+k for k in e.absolute_path),
        observed_type=type(e.instance).__name__) for e in errors]
    try:
        from src.material_function_transport import validate_parameters as validate_stage
        validate_stage(obj);row['schema_valid']=True
        parse_extraction(raw,draft,set());row['parser_valid']=True
    except Exception as exc:row['formal_failure']=feedback(exc)
    spans=source_spans(draft);index={s['ref']:s for s in spans}
    row['source_map_counts']={state:sum(s['support_state']==state for s in spans) for state in ('action_supporting','non_action','unknown')}
    row['evidence']=dict(supplied=len(spans),returned=0,valid=0,supporting=0,non_supporting=0,unknown_refs=0,unknown_support=0)
    if isinstance(action,dict):
        refs=action.get('evidence_refs');refs=refs if isinstance(refs,list) else []
        valid=[r for r in refs if isinstance(r,str) and r in index and index[r]['in_current_scope']]
        row['evidence'].update(returned=len(refs),valid=len(valid),unknown_refs=sum(not isinstance(r,str) or r not in index for r in refs),
            supporting=sum(index[r]['support_state']=='action_supporting' for r in valid),
            non_supporting=sum(index[r]['support_state']=='non_action' for r in valid),
            unknown_support=sum(index[r]['support_state']=='unknown' for r in valid))
        row['source_provenance']=[dict(ref=r,support_state=index[r]['support_state'],origin=index[r]['support_origin']) for r in valid]
        if row['parser_valid']:
            row['actionability_executed']=True
            source_audit=resolve(action,draft)[1];row['verified_evidence']=source_audit['verified_evidence_required']
            row['evidence_failure_reason']=source_audit['provenance_failure_reason']
            try:validate_repaired_action(raw,draft);row['actionability_valid']=True
            except Exception as exc:row['formal_failure']=feedback(exc)
    works=obj.get('workload');works=works if isinstance(works,list) else []
    features=[f for w in works if isinstance(w,dict) for f in (w.get('features') or []) if isinstance(f,dict)]
    audits=[]
    for feature in features:
        kind=feature.get('kind');legal=isinstance(kind,str) and kind in RATES
        safe_kind=kind if isinstance(kind,str) and re.fullmatch(r'[A-Za-z_0-9]{1,40}',kind) else '<non-category>'
        try:Feature(**feature);valid_model=True
        except (ValueError,TypeError):valid_model=False
        audits.append(dict(kind=safe_kind,enum_valid=legal,formal_feature_valid=valid_model,
            semantically_supported=legal and kind in gold['allowed_feature_kinds'],
            evidence_matches_source=evidence_matches(feature.get('evidence'),draft.original_text),
            units=feature.get('units') if type(feature.get('units')) is int else None))
    row['features']=audits;kind_set={f['kind'] for f in audits}
    row['missing_feature_groups']=[g for g in gold['expected_feature_groups'] if not kind_set.intersection(g)]
    from scripts.fc_action_coverage import coverage
    row['action_coverage']=coverage(obj,audits,gold)
    row['workload_semantic_correct']=bool(audits and all(r['covered'] for r in row['action_coverage']) and all(
        f['enum_valid'] and f['formal_feature_valid'] and f['semantically_supported'] and f['evidence_matches_source'] for f in audits))
    scopes=([action.get('short_scope')] if isinstance(action,dict) else [])+[w.get('scope') for w in works if isinstance(w,dict)]
    scopes += [i.get('scope') for i in obj.get('items',[]) if isinstance(i,dict)] if isinstance(obj.get('items'),list) else []
    text='；'.join(v for v in scopes if isinstance(v,str))
    row['scope_summary']=text[:300];row['scope_summary_truncated']=len(text)>300
    # Scope is not an exhaustive action list: the formal consumer estimates
    # the entire scope AND features. Count only supported/grounded features,
    # never merely quote the source as proof that an action was recognized.
    completions=[i.get('completion') for i in obj.get('items',[]) if isinstance(i,dict)] if isinstance(obj.get('items'),list) else []
    actions=[LABELS[f['kind']] for f in audits if f['formal_feature_valid'] and f['semantically_supported'] and f['evidence_matches_source']]
    # Grounded feature evidence carries details such as which form fields to
    # fill; action_coverage separately prevents quoting an instruction from
    # counting an entirely unrecognized action as covered.
    details=[f['evidence'] for f,a in zip(features,audits) if a['formal_feature_valid'] and a['semantically_supported'] and a['evidence_matches_source']]
    coverage_text='；'.join([text]+[v for v in completions if isinstance(v,str)]+actions+details)
    row['scope_coverage']=[bool(re.search(pattern,coverage_text)) for pattern in gold['scope_patterns']]
    row['scope_coverage_source']='scope + item completion + verified feature semantics'
    row['scope_correct']=bool(text) and all(row['scope_coverage']) and all(r['covered'] for r in row['action_coverage'])
    from src.material_recognition_semantics import policy_repair_context
    row['policy_observations']=policy_repair_context(obj)
    row['time_emission']=[dict(path='$.items[{}].{}'.format(i,k),object_present=isinstance(item[k],dict),
        value_is_null=item[k] is None,text_present='text' in item[k] if isinstance(item[k],dict) else False,
        text_type=type(item[k].get('text')).__name__ if isinstance(item[k],dict) else None)
        for i,item in enumerate(obj.get('items',[]) if isinstance(obj.get('items'),list) else []) if isinstance(item,dict)
        for k in ('deadline','start','end') if k in item]
    if row['parser_valid']:
        from src.material_workload_semantics import validate_workload_semantics
        try:validate_workload_semantics(obj,draft.original_text)
        except ValueError as exc:row['formal_failure']=feedback(exc)
    if not row['schema_valid'] or not row['parser_valid']:layer='schema'
    elif not row['actionability_correct'] or not row['reason_valid']:layer='actionability semantics'
    elif not row['verified_evidence']:
        layer='source support' if row['evidence']['valid'] and (row['evidence']['non_supporting'] or row['evidence']['unknown_support']) else 'evidence'
    elif not row['actionability_valid']:layer='evidence'
    elif (row.get('formal_failure') or {}).get('code')=='workload_evidence_mismatch':layer='evidence'
    elif not row['workload_semantic_correct']:layer='enum semantics'
    elif row['formal_failure']:layer='formal business validation'
    elif not row['scope_correct']:layer='其他：scope保真'
    else:layer=None
    row['primary_failure_layer']=layer;row['business_pass']=layer is None
    from scripts.fc_action_coverage import repair_feedback
    row['coverage_feedback']=repair_feedback(row)
    return row


def main(fixture_ids=None,destination=None,coverage_repair=False):
    output=destination or DEST
    output.mkdir(parents=True,exist_ok=True);dest=output/'calls.json'
    if dest.exists():raise RuntimeError('No repeated fixture run')
    gold=gold_rows();assert len(gold)==len(TEXTS)==10
    if fixture_ids is not None:gold=[g for g in gold if g['fixture'] in fixture_ids]
    schema=function_definition();guide=recognition_semantic_guide();hashes=freeze_hashes()
    (output/'design.json').write_text(json.dumps(dict(gold=gold,schema=schema,semantic_guide=guide,
        source_hashes=hashes,semantic_repair_only_on_formal_error=not coverage_repair,
        action_coverage_accepts_equivalent_formal_fields=True),ensure_ascii=False,indent=2),encoding='utf-8')
    env = []
    pass  # Use the caller environment unchanged.
    os.environ['TJU_LLM_BASE_URL']=ENDPOINT[:-len('/chat/completions')]
    from src.tju_llm_client import _require_all_envs,_build_chat_url
    base,_,model=_require_all_envs();assert _build_chat_url(base)==ENDPOINT and model=='tju-llm'
    import requests
    adapter=TJUP2CallAdapter(recognition_tools=True);real=requests.sessions.Session.request;records=[];attempt=None
    def observed(session,method,url,**kwargs):
        assert url==ENDPOINT and method.upper()=='POST'
        body=kwargs['json'];assert body['tools']==[dict(type='function',function=schema)] and body['tool_choice']=='auto'
        assert guide in body['messages'][0]['content']
        wire_context=json.loads(body['messages'][1]['content'])
        wire_template=sorted(wire_context['output'])
        wire_fields=sorted(schema['parameters']['properties'])
        system_roots=sorted(body['messages'][0]['content'].split('输出根字段仅允许：')[1].split('。')[0].split(','))
        assert wire_template==wire_fields==system_roots
        attempt.update(output_template_root_fields=wire_template,tool_root_fields=wire_fields,
            system_allowed_root_fields=system_roots,stage_contract_aligned=True)
        attempt['requests']+=1;assert attempt['requests']==1
        start=time.monotonic();response=real(session,method,url,**kwargs)
        attempt.update(http_status=response.status_code,latency_ms=round((time.monotonic()-start)*1000),
            same_tool_schema=True,same_semantic_guide=True)
        if response.status_code==200:
            data=response.json();choice=data.get('choices',[{}])[0];calls=(choice.get('message') or {}).get('tool_calls') or []
            attempt.update(tool_call_count=len(calls),target_tool_calls=sum(c.get('function',{}).get('name')==NAME for c in calls),
                function_names=[c.get('function',{}).get('name') if re.fullmatch(r'[A-Za-z_0-9]{1,60}',str(c.get('function',{}).get('name'))) else '<other>' for c in calls],finish_reason=choice.get('finish_reason'))
            usage=data.get('usage') or {};attempt['usage']={k:usage.get(k) for k in ('prompt_tokens','completion_tokens','total_tokens')}
        return response
    requests.sessions.Session.request=observed
    try:
        for spec in gold:
            row=dict(fixture=spec['fixture'],label=spec['label'],attempts=[])
            draft=update_source(MaterialInbox(),spec['text'],'',datetime(2026,9,16,14),'plan','beiyangyuan').draft
            def recognize_only(system,user):
                nonlocal attempt
                previous=None;last_failure=None;binding_only=False
                for number in range(2):
                    attempt=dict(stage='normal' if number==0 else 'semantic_repair',requests=0)
                    row['attempts'].append(attempt);request_system=system;request_user=user
                    if number:
                        request_system+=(' 这是唯一一次recognition semantic repair。只修validation_feedback指出的问题。'
                            '通过同一个material_recognition_result函数返回一个完整最终对象，不在content输出JSON。'
                            '正式required、可选字段、enum、nested对象以同一函数parameters为准，不新增字段。'
                            'workload.features.kind只能选择给定枚举，不能使用effort_ledger类别。'
                            'items即使为空也必须保留。evidence_refs只能从verified_action_source_refs选择；'
                            '没有支持来源时改为is_estimatable=false，不编造ref。')
                        context=json.loads(user);context.update(previous_recognition=str(previous),validation_feedback=last_failure)
                        if last_failure.get('code')=='workload_action_coverage':
                            request_system+=' 这是开发验收的动作覆盖修复，不是新业务schema。只修缺失或错误分类，保留已有任务身份、时间地点、材料事实与source refs；若动作已等价覆盖则明确现有scope/completion，不重复生成feature。'
                        from src.material_recognition_semantics import policy_repair_context
                        context['policy_validation_context']=policy_repair_context(strict_object(previous))
                        request_user=json.dumps(context,ensure_ascii=False)
                    raw=None
                    try:
                        raw=adapter.agent_caller(request_system,request_user);attempt.update(audit_candidate(raw,draft,spec))
                        if number and binding_only:assert_binding_only(previous,raw)
                    except Exception as exc:
                        attempt.update(formal_failure=feedback(exc),business_pass=False,primary_failure_layer='transport' if raw is None else 'schema')
                    issue=attempt.get('formal_failure') or (attempt.get('coverage_feedback') if coverage_repair else None)
                    if not issue:break
                    if raw is None or not attempt.get('json_valid') or attempt.get('http_status')!=200:break
                    attempt['repair_trigger']=issue
                    previous=raw;last_failure=issue
                    binding_only=bool(attempt.get('parser_valid') and attempt.get('actionability_executed') and issue.get('code') not in ('workload_action_coverage','workload_semantic_mismatch'))
                raise Done()
            with capture_estimate_diagnostics():
                try:extract_material(draft,recognize_only)
                except Done:pass
            final=row['attempts'][-1];row.update(final_pass=final.get('business_pass',False),primary_failure_layer=final.get('primary_failure_layer'),repair_count=len(row['attempts'])-1)
            records.append(row);dest.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(dict(fixture=spec['fixture'],final_pass=row['final_pass'],layer=row['primary_failure_layer'],repair=row['repair_count'],
                attempts=[dict(schema=a.get('schema_valid'),parser=a.get('parser_valid'),action=a.get('is_estimatable'),verified=a.get('verified_evidence'),
                    features=a.get('features'),failure=a.get('formal_failure'),usage=a.get('usage')) for a in row['attempts']]),ensure_ascii=False),flush=True)
            if any(a.get('http_status')!=200 for a in row['attempts']):break
            time.sleep(2.1)
    finally:requests.sessions.Session.request=real
    assert freeze_hashes()==hashes,'Frozen product candidate changed during run'

if __name__=='__main__':main()
