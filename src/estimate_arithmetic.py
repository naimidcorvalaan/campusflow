"""Check explicit focus-minute breakdowns, never infer minutes from prose.

The estimate schemas contain a recommendation, a range and explanatory text,
not a mandatory additive ledger. Only recognizable labelled minute items or
explicit core subtotals establish such a ledger. This is a rejection guard;
it never changes the model's recommendation or the user's adopted minutes.
"""
from dataclasses import dataclass
import re

from src.material_estimate_diagnostics import record

ARITHMETIC_INSTRUCTIONS = (
    '建议分钟、区间、basis和assumptions应相互支持。若给分钟分项，用“步骤名(10分钟)”或'
    '“步骤名(10-15分钟)”清楚列出；不强求分项求和等于建议值，但明显差额须明确交代。'
    '另加准备、查资料、复核、返工或缓冲时写出额外分钟；个体速度修正写明适用范围及倍率或额外分钟。'
    '已计入的步骤不要重复加。没有分钟分项时可用纯文字依据，不把字数、数量、观察时长当作工作分钟。'
)
ARITHMETIC_REPAIR_INSTRUCTIONS = (
    '当前分项分钟与建议分钟存在未解释差额，请补充遗漏工作量或修正建议时间。'
    '保留原任务范围和已有分钟分项，不得删除分钟分项来回避校验。'
    '额外时间或速度调整必须在basis/assumptions中以自然中文和明确数量说明；'
    '可以保留近似区间及合理舍入，不需要机械凑成等式。只输出原协议的JSON，不显示内部校验术语。'
    'validation_feedback列明当前建议、可解释分钟上下界与未解释差额；只补真实遗漏工作或修正建议，不为通过校验虚构缓冲。'
)

_N = r'\d+(?:\.\d+)?'
_TIME = re.compile(r'(?<![\d.])(?P<low>'+_N+r')\s*(?:(?:[-–—~～至到])\s*'
    r'(?P<high>'+_N+r'))?\s*(?P<unit>分钟|mins?\b|minutes?\b|分(?!钟|之|数))',re.I)
_TOTAL = re.compile(r'合计|总计|总共|总用时|总时长|建议|整体|总耗时|推荐')
_CORE = re.compile(r'核心(?:作业|工作|步骤|部分|时间|用时)|分项合计|基础工作')
_EXTRA = re.compile(r'额外|另(?:外)?(?:预留|留|加|需|计)|再(?:预留|留|加)|缓冲|返工|机动|准备|查(?:找)?(?:资料|笔记)|复核|检查|提交|setup|review|buffer|lookup',re.I)
_NOT_ADDITIVE = re.compile(r'已(?:经)?(?:包含|计入)|不(?:再|另)(?:计|加)|其中|每(?:题|道|次|项|分钟)|/题|per\b',re.I)
_SOURCE_TIME = re.compile(r'观测时长|观察(?:值|时长|时间)|记录时长|样本(?:时长|均值)|分数|得分|成绩|课程时长|课长')


@dataclass(frozen=True)
class ArithmeticCheck:
    applicable: bool
    valid: bool
    parts: tuple = ()
    extras: tuple = ()
    explained_min: float = 0
    explained_max: float = 0
    factor: float = 1
    tolerance: float = 0
    code: str = 'no_explicit_breakdown'
    adjusted_part_count: int = 0

    def feedback(self,recommended=None,minimum=None,maximum=None):
        # Only derived counts/numbers, never source labels or model text.
        result=dict(code=self.code,part_count=len(self.parts),extra_count=len(self.extras),
            explained_min=self.explained_min,explained_max=self.explained_max,
            speed_factor=self.factor,adjusted_part_count=self.adjusted_part_count,
            tolerance_minutes=self.tolerance)
        if recommended is not None:
            result.update(recommended_minutes=recommended,duration_min=minimum,duration_max=maximum,
                unexplained_gap_minutes=(recommended-self.explained_max if recommended>self.explained_max
                    else recommended-self.explained_min if recommended<self.explained_min else 0)
                    if self.applicable else None)
        return result


class EstimateArithmeticError(ValueError):
    def __init__(self, check):
        super().__init__('estimate_arithmetic_inconsistent')
        self.check = check


def _items(text, assumption=False):
    items=[];core=[]
    for clause in re.split(r'[；;。\n]',text or ''):
        for match in _TIME.finditer(clause):
            low=float(match['low']);high=float(match['high'] or match['low'])
            if not 0<=low<=high<=10080:
                continue
            prefix=clause[:match.start()].strip()
            # A new item may follow a comma; keep a quantity/label pair local.
            prefix=re.split(r'[，,、]',prefix)[-1].strip()
            suffix=clause[match.end():]
            label=prefix.rstrip('(:：（ （').strip()
            parenthesized=bool(re.search(r'[（(]\s*(?:约|大约)?\s*$',prefix))
            labelled=parenthesized or bool(re.search(r'[:：]\s*(?:约|大约)?$',prefix))
            numbered=bool(re.search(r'第[\d一二三四五六七八九十]+(?:部分|项|步)',prefix))
            action=bool(re.match(r'^.{0,10}?(?:做题|计算|证明|写作|撰写|阅读|整理|核对|复核|检查|绘制|绘图|编写|编程|调试|上传|提交|查资料|查笔记|估计|检验|讨论|分析|填写|订正)',label))
            extra=bool(_EXTRA.search(prefix))
            is_core=bool(_CORE.search(prefix))
            # Explicit included substeps are not added twice, including the
            # equivalent “复核(10分钟，已计入计算)” notation.
            included_suffix=re.match(r'\s*[，,]?\s*已(?:经)?(?:包含|计入)',suffix)
            if (_NOT_ADDITIVE.search(prefix) or included_suffix or _SOURCE_TIME.search(prefix)
                    or _TOTAL.search(prefix) and not is_core):
                continue
            # Bare “分” can mean a score. Require the conventional time-item
            # notation (as used by the product's estimate cards).
            if match['unit']=='分' and not (labelled or numbered or is_core):
                continue
            if not (labelled or numbered or action or is_core or extra):
                continue
            if assumption and not (extra or is_core):
                continue
            if not label or re.fullmatch(r'[\d.\s+*×-]+',label):
                continue
            item=dict(label=label,min=low,max=high)
            if is_core:
                core.append(item)
            else:
                items.append((item,assumption or bool(re.search(r'额外|另|再加|缓冲|返工|机动',prefix))))
    return items,core


def _factor(text):
    """Return a quantified speed adjustment and its scope-bearing clause."""
    for clause in re.split(r'[。；;\n]',text):
        if not re.search(r'速度|较慢|更慢|熟练|较快|更快|耗时|用时|时间|工作量',clause):
            continue
        factor=re.search(r'(?:按|乘以?|提高到|调整为)\s*('+_N+r')\s*倍?',clause)
        if factor and ('倍' in factor.group() or re.search(r'乘',factor.group())):
            value=float(factor[1])
            if .1<=value<=10:return value,clause
        percent=re.search(r'(增加|多留|多预留|减少|缩短)\s*('+_N+r')\s*[%％]',clause)
        if percent and 0<float(percent[2])<=300:
            value=1+(-1 if percent[1] in ('减少','缩短') else 1)*float(percent[2])/100
            if value>0:return value,clause
    return 1,''


def check_estimate_arithmetic(recommended, minimum, maximum, basis, assumptions=()):
    # The model's own range is a hard boundary, even with no minute ledger.
    # Approximation tolerance must never rescue a recommendation outside it.
    if (not all(type(n) is int and 1<=n<=10080 for n in (recommended,minimum,maximum))
            or not minimum<=recommended<=maximum):
        return ArithmeticCheck(False,False,code='invalid_duration_range')
    items,core=_items(basis)
    extras=[]
    for assumption in assumptions:
        more,subtotals=_items(assumption,True)
        items.extend(more);core.extend(subtotals)
    parts=[];seen=set()
    for item,extra in items:
        identity=(item['label'],item['min'],item['max'])
        if identity in seen:continue
        seen.add(identity)
        (extras if extra else parts).append(item)
    # An explicit core subtotal can establish a ledger on its own. If detailed
    # items are also present, the subtotal must not be added again.
    if not parts and core:parts=[core[0]]
    if not parts and not extras:
        return ArithmeticCheck(False,True,tuple(parts),tuple(extras))
    combined='；'.join([basis or '']+list(assumptions))
    factor,adjustment=_factor(combined)
    # A named step's slowdown must not multiply unrelated work. An explicit
    # whole/core adjustment covers all core steps; extras remain separate.
    scoped=[p['label'] for p in parts if re.search(re.escape(p['label'])+
        r'(?:的)?(?:耗时|用时|时间)',adjustment)]
    if re.search(r'核心|全部|整体|所有',adjustment):scoped=[]
    adjusted=[p for p in parts if factor!=1 and (not scoped or p['label'] in scoped)]
    low=sum(p['min']*(factor if p in adjusted else 1) for p in parts)+sum(p['min'] for p in extras)
    high=sum(p['max']*(factor if p in adjusted else 1) for p in parts)+sum(p['max'] for p in extras)
    # Reject material contradictions, not ordinary prediction uncertainty.
    # Compare against the nearest end of a breakdown range, allowing fifteen
    # minutes or 20% of the recommendation. A wide overall range does not
    # independently inflate this allowance.
    tolerance=max(15,recommended*.20)
    nearest=min(high,max(low,recommended))
    small_scale=min(recommended,nearest)
    if small_scale<30:
        # The absolute floor must not turn a two-minute job into seventeen.
        # For short jobs cap uncertainty at half the smaller time scale, with
        # two minutes for small integer rounding in either direction.
        tolerance=min(tolerance,max(2,small_scale*.5))
    valid=low-tolerance<=recommended<=high+tolerance
    return ArithmeticCheck(True,valid,tuple(parts),tuple(extras),low,high,factor,tolerance,
        'arithmetic_consistent' if valid else 'unexplained_time_gap',len(adjusted))


def ensure_estimate_arithmetic(recommended, minimum, maximum, basis, assumptions=(),stage='material_estimate'):
    result=check_estimate_arithmetic(recommended,minimum,maximum,basis,assumptions)
    record(stage,'estimate_arithmetic',result.code,field='basis',parsed=True,valid=result.valid,
        count=len(result.parts)+len(result.extras))
    if not result.valid:
        raise EstimateArithmeticError(result)
    return result


def response_arithmetic_feedback(raw):
    """Find invalid model estimates for one precise, bounded repair prompt."""
    from src.p2_agentic_parser import extract_json_object,AgenticParseError
    try:
        obj=extract_json_object(raw)
    except (ValueError,TypeError,AgenticParseError):
        return []
    if not isinstance(obj,dict):return []
    values=[obj.get('estimate'),obj]
    if isinstance(obj.get('items'),list):
        values.extend(row.get('estimate') for row in obj['items'] if isinstance(row,dict))
    feedback=[]
    for value in values:
        if not isinstance(value,dict):continue
        numbers=[value.get(k) for k in ('recommended_minutes','min_focus_minutes','max_focus_minutes')]
        if not all(type(n) is int and n>0 for n in numbers):continue
        basis=value.get('basis');assumptions=value.get('assumptions',[])
        if not isinstance(basis,str) or not isinstance(assumptions,list) or not all(isinstance(a,str) for a in assumptions):continue
        if isinstance(value.get('adjustment_basis'),str):
            assumptions=assumptions+[value['adjustment_basis']]
        from src.material_effort import check_effort,repair_feedback
        from src.material_effort_provenance import ACTIVE
        from src.material_formal_validation import FormalValidationError
        try:check=check_effort(*numbers,basis,assumptions,ledger=value.get('effort_ledger'),sources=ACTIVE.get())
        except FormalValidationError as exc:
            feedback.append(exc.feedback);continue
        if not check.valid:feedback.append(repair_feedback(check,*numbers,ledger=value.get('effort_ledger')))
    return feedback
