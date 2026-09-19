"""Development fixture acceptance, not a production NLP/hard validator.

Required category groups are independent, source-supported fixture gold.
Small positive action markers accept equivalent scope/completion statements;
quoted source evidence alone is deliberately not recognized action coverage.
"""
import re

ACTION_MARKERS={
    'basic_field':r'填写|填表|录入',
    'review':r'检查|核对|复核|校验|校对',
    'submit':r'提交|上传|交付|递交|上交|\bupload\b|\bhand[ -]in\b',
    'short_text':r'撰写|写出|写一段|修订|修改.{0,12}(?:摘要|文本|短文|文字|报告)|整理.{0,12}(?:提纲|文字|文本)',
    'long_text':r'撰写|写出|写一段|修订|修改.{0,12}(?:摘要|文本|短文|文字|报告)|整理.{0,12}(?:提纲|文字|文本)',
    'calculation':r'计算|推导', 'problem':r'解题|做题',
    'code':r'运行.{0,12}(?:代码|程序)|实现.{0,12}(?:代码|程序)|编程',
    'chart':r'绘制|制图', 'attachment':r'补齐附件|补充附件|准备.{0,8}(?:附件|证明)',
    'reading_100_words':r'阅读|研读|读完',
}


def coverage(obj,features,gold):
    statements=[]
    action=obj.get('actionability')
    if isinstance(action,dict):statements.append(('actionability.short_scope',action.get('short_scope')))
    for group in ('items','workload'):
        rows=obj.get(group,[])
        if isinstance(rows,list):
            for i,row in enumerate(rows):
                if isinstance(row,dict):
                    for field in ('scope','completion'):
                        statements.append(('{}[{}].{}'.format(group,i,field),row.get(field)))
    safe=[(path,text) for path,text in statements if isinstance(text,str)]
    recognized={f['kind'] for f in features if f['formal_feature_valid'] and f['semantically_supported'] and f['evidence_matches_source']}
    result=[]
    for group in gold['expected_feature_groups']:
        feature_hits=sorted(recognized.intersection(group))
        paths=sorted({path for path,text in safe for kind in group if kind in ACTION_MARKERS and re.search(ACTION_MARKERS[kind],text,re.I)})
        result.append(dict(allowed_categories=group,feature_categories=feature_hits,equivalent_field_paths=paths,covered=bool(feature_hits or paths)))
    return result


def repair_feedback(row):
    missing=[r['allowed_categories'] for r in row.get('action_coverage',[]) if not r['covered']]
    unsupported=[f['kind'] for f in row.get('features',[]) if f['enum_valid'] and not f['semantically_supported']]
    if not missing and not unsupported:return None
    from src.material_workload import LABELS,FEATURE_SEMANTICS
    return dict(code='workload_action_coverage',development_acceptance=True,
        missing_action_categories=missing,unsupported_feature_categories=unsupported,
        category_semantics={k:dict(label=LABELS[k],meaning=FEATURE_SEMANTICS[k]) for k in set(unsupported).union(*(set(g) for g in missing))},
        supporting_source_refs=[r['ref'] for r in row.get('source_provenance',[]) if r['support_state']=='action_supporting'],
        current_feature_categories=[f['kind'] for f in row.get('features',[])],
        instruction='仅补齐明确的缺失工作动作，或在现有scope/completion中明确其等价覆盖；纠正不受来源支持的类别。保留任务身份、原文事实、时间地点和evidence refs，不重复计量、不生成新事实。')
