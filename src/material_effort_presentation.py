"""Copy-only projection: one numeric source, no estimate or ledger mutation.

The model's rationale still explains the work. With a validated ledger, minute
annotations in that explanation are redundant: the card's metrics and ledger
own them. We remove these annotations instead of guessing which category a
free-text number belongs to. Material quantities (pages, words, records) stay.
"""
import re

NUMBER=r'(?:\d+(?:\.\d+)?|[零一二三四五六七八九十百千万两半]+)'
AMOUNT=NUMBER+r'(?:\s*[–—~～至到\-]\s*'+NUMBER+r')?'
TIME=r'(?:约|大约|大概|至少|最多|不超过|上限|下限)?\s*'+AMOUNT+r'\s*(?:分钟|小时|minutes?|mins?|hours?)'
_TIME=re.compile(TIME,re.I)
_ANNOTATION=re.compile(r'[（(]\s*'+TIME+r'\s*[）)]',re.I)
# Only explicit estimate totals, not a general prose fact parser. Their values
# are diagnostic only; rendering never trusts these claims, even when equal.
_CLAIMS=[
    ('core',re.compile(r'(?:基础)?核心(?:工作量|工作|用时|时间)?\s*(?:合计|小计|总计|共计|共|总共|约|为|=|＝)\s*('+AMOUNT+r')\s*分钟')),
    ('adjustments',re.compile(r'(?:额外)?(?:调整|缓冲|余量)(?:用时|时间)?\s*(?:合计|小计|总计|共计|共|总共|为|=|＝)\s*('+AMOUNT+r')\s*分钟')),
    ('suggested',re.compile(r'(?:建议|推荐)(?:总)?(?:专注)?(?:用时|时间|预留)?\s*(?:约|为|共|=|＝)?\s*('+AMOUNT+r')\s*分钟')),
    ('range',re.compile(r'(?:预计|估计|用时|时长)(?:区间|范围)?\s*(?:约|为|=|＝)?\s*('+NUMBER+r'\s*[–—~～至到\-]\s*'+NUMBER+r')\s*分钟')),
]


def numeric_copy_issues(value,ledger):
    """Safe field/numeric diagnostics; not another arithmetic validator."""
    expected=dict(core=(sum(r['minutes'] for r in ledger['core']),),
        adjustments=(sum(r['minutes'] for r in ledger['adjustments'] if r['included_in'] is None),),
        suggested=(value['recommended_minutes'],),
        range=(value['focused_minutes_min'],value['focused_minutes_max']))
    issues=[]
    for field,text in [('rationale',value['rationale'])]+[
            ('assumptions[{}]'.format(i),t) for i,t in enumerate(value['assumptions'])]:
        for kind,pattern in _CLAIMS:
            for match in pattern.finditer(text):
                numbers=tuple(float(n) for n in re.findall(r'\d+(?:\.\d+)?',match.group(1)))
                if numbers and numbers!=expected[kind]:
                    issues.append(dict(code='effort_numeric_copy_mismatch',field=field,
                        metric=kind,observed=list(numbers),expected=list(expected[kind])))
    return issues


def explanation(text):
    """Remove duplicated effort numbers, retaining qualitative/material facts."""
    if not _TIME.search(text):return text
    for _,pattern in _CLAIMS:text=pattern.sub('',text)
    text=_ANNOTATION.sub('',text)
    text=_TIME.sub('',text)
    text=re.sub(r'[（(]\s*[）)]','',text)
    text=re.sub(r'([，,；;。])\s*([，,；;。])',r'\2',text)
    return text.strip(' ，,；;。\n')


def present_effort(value,ledger):
    """Fresh projection on every render, shared by normal/repair/recovery cards."""
    if not ledger:return value,''
    from src.material_effort import render_ledger
    from src.material_estimate_diagnostics import record,_sink
    # Existing formal ledger renderer checks its contract. No minute changes.
    ledger_note=render_ledger(ledger)
    issues=numeric_copy_issues(value,ledger)
    if issues:
        record('material_presentation','effort_numeric_copy','copy_projected_from_ledger',valid=True)
        if _sink.get() is not None:_sink.get()[-1]['copy_mismatches']=issues
    # The structure has already rendered all adjustment minutes. Its free-text
    # reasons must not create a second set of minute annotations either.
    for row in ledger['adjustments']:
        ledger_note=ledger_note.replace('（'+row['reason'], '（'+explanation(row['reason']))
    projected=dict(value,rationale=explanation(value['rationale']),
        assumptions=[explanation(t) for t in value['assumptions']])
    return projected,ledger_note
