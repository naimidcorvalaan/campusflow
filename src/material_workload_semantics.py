"""Narrow, source-backed category contradictions, not general NLP inference.

Grounded quotations and explicit operation boundaries are checked. Unknown
semantics are not guessed; no enum is converted or silently removed.
"""
import re

LOCAL_OUTPUT=r'(?:保存|存储|导出|写入|写出|save|export|store|write)\s*[^。；;\n]{0,24}(?:文件|结果|输出|本地|磁盘|file|result|output|local|disk)'
DELIVERY=r'提交|上传|递交|交付|上交|发给|发送给|submit|upload|hand\s*in|deliver|send\s+to'
ATTACHMENT=r'附件|证明|佐证|支撑材料|attachment|certificate|supporting\s+document'
LOOKUP=r'查找|查阅|检索|搜集|搜索|查资料|文献调研|look\s*up|search|retrieve'
COMPARISON=r'比较|对比|compare|contrast'


def validate_workload_semantics(obj,source):
    from src.material_workload import FEATURE_SEMANTICS,evidence_matches
    from src.material_formal_validation import FormalValidationError
    for wi,work in enumerate(obj.get('workload') or []):
        features=work.get('features',[])
        for fi,feature in enumerate(features):
            kind=feature.get('kind');quote=feature.get('evidence','')
            # Only Function Recognition calls this guard. Legacy workload
            # ingestion keeps its historical best-effort behavior. A function
            # result must not silently lose work through rejected quotations.
            if 'evidence' in feature and not evidence_matches(quote,source):
                exc=FormalValidationError('workload_evidence_mismatch',
                    '$.workload[{}].features[{}].evidence'.format(wi,fi),
                    'one nonempty verbatim source span; no paraphrase or stitched quote',quote)
                exc.feedback.update(invariant_id='workload_feature_source_evidence',
                    exact_match=isinstance(quote,str) and quote in source,
                    layout_only_candidate=isinstance(quote,str) and bool(quote.strip()) and re.sub(r'\s+','',quote) in re.sub(r'\s+','',source),
                    evidence_chars=len(quote) if isinstance(quote,str) else None,
                    instruction='引用支持该动作的连续原文片段，保留原任务范围；不通过改写证据或删除真实工作来通过校验。')
                raise exc
            if (kind=='research' and evidence_matches(quote,source)
                    and re.search(COMPARISON,quote,re.I) and not re.search(LOOKUP,source,re.I)):
                exc=FormalValidationError('workload_semantic_mismatch',
                    '$.workload[{}].features[{}].kind'.format(wi,fi),FEATURE_SEMANTICS[kind],kind)
                exc.feedback.update(invariant_id='comparison_is_not_source_lookup',category=kind,
                    source_support='comparison_without_lookup_instruction',
                    instruction='保留比较分析任务；现有引用只证明比较，不证明外部资料检索。按已有动作类别表达比较或说明，不增加未要求的查找资料工作。')
                raise exc
            # Local saving is not delivery for text/review work either. This
            # needs an explicit local-output instruction, not an absent verb
            # alone; no category is rewritten by the guard.
            if (kind=='submit' and evidence_matches(quote,source)
                    and re.search(LOCAL_OUTPUT,source,re.I)
                    and not re.search(DELIVERY,source,re.I)):
                exc=FormalValidationError('workload_semantic_mismatch',
                    '$.workload[{}].features[{}].kind'.format(wi,fi),FEATURE_SEMANTICS[kind],kind)
                exc.feedback.update(invariant_id='local_output_is_not_external_delivery',category=kind,
                    source_support='verified_local_operation',
                    instruction='保留阅读、文字、代码或复核工作；本地保存不等于向接收方交付。纠正无来源的提交类别，保留其他任务与来源。')
                raise exc
            if not any(f.get('kind')=='code' for f in features):continue
            if kind not in ('submit','attachment') or not evidence_matches(quote,source):continue
            from src.material_scope import _ACTIONS
            local_output=bool(re.search(LOCAL_OUTPUT,quote,re.I))
            explicit_code=bool(re.search(dict(_ACTIONS)['code'],quote,re.I))
            code_result_review=(bool(re.search(dict(_ACTIONS)['code'],source,re.I))
                and bool(re.search(dict(_ACTIONS)['review'],quote,re.I)))
            # A source-backed code operation also cannot become delivery just
            # because it produces a result. Unknown operations remain unknown.
            if not (local_output or kind=='submit' and (explicit_code or code_result_review)):continue
            if re.search(DELIVERY if kind=='submit' else ATTACHMENT,source,re.I):continue
            exc=FormalValidationError('workload_semantic_mismatch',
                '$.workload[{}].features[{}].kind'.format(wi,fi),FEATURE_SEMANTICS[kind],kind)
            exc.feedback.update(invariant_id='local_output_is_not_external_delivery' if local_output else 'code_is_not_external_delivery',category=kind,
                source_support='verified_local_operation' if local_output else 'code_without_delivery_instruction',
                instruction='保留代码与结果核对工作；不增加没有材料依据的交付或附件工作。保持其他任务事实和来源引用。')
            raise exc
