"""Ground numbered work in source paragraphs and explicit user selections.

This is an estimate coverage boundary, not a planner/task identity layer.
It preserves source work when a model omits a section or a delivery step.
"""
from dataclasses import dataclass
import re
from src.material_structure import content_text, source_title

NUMBER = r'(?:\d{1,3}|[零〇一二两三四五六七八九十百]{1,5})'
_PART = re.compile(r'第\s*(' + NUMBER + r')\s*部\s*分')
_ENUM = re.compile(r'(?m)(?:^|(?<=[：:；;。]))\s*(?:[（(](' + NUMBER + r')[）)]|(' + NUMBER + r')[.．、](?!\d))\s*')
_TASK_CUE = re.compile(r'数据|采样|滤波|误差|报告|讨论|复核|检查|计算|推导|证明|填写|编写|撰写|整理|梳理|评审|交付|比较|分析|提交|上传|设计|绘制|完成|核对|说明|实验|作业|任务')


def _heading_has_work(after_marker):
    pieces=re.split(r'[。；;\n]',after_marker.lstrip(),maxsplit=1)
    title=pieces[0]
    if _TASK_CUE.search(title[:45]):return True
    # A short noun heading may carry its actual action in the next sentence.
    # Reuse workload actions; do not treat arbitrary numbered prose as work.
    if len(title)>24 or len(pieces)<2:return False
    actions='|'.join(pattern for _,pattern in _ACTIONS)
    return bool(re.match(r'\s*(?:请\s*)?(?:阅读|整理|设计|完成|'+actions+r')',pieces[1]))


def section_markers(text):
    """Recognize task headings at sentence boundaries as well as line starts.

    Bare ordinals, decimals, and prose references are not section headings.
    Matching uses source offsets so evidence and the complete bodies survive.
    """
    candidates=[]
    for m in _PART.finditer(text):
        prefix=text[:m.start()].rstrip(' \t#')
        if prefix and prefix[-1] not in '\n。；;：:':
            continue
        tail=re.split(r'[。；;\n]',text[m.end():].lstrip(),maxsplit=1)[0]
        if not _heading_has_work(text[m.end():]):
            continue
        if re.match(r'\s*(?:的|中|所述|提到|见|与|和|及|(?:至|到)\s*第?'+NUMBER+r')',tail):
            continue
        candidates.append((m.start(),m.end(),number(m[1])))
    for m in _ENUM.finditer(text):
        tail=re.split(r'[。；;\n]',text[m.end():].lstrip(),maxsplit=1)[0]
        if _heading_has_work(text[m.end():]):
            candidates.append((m.start(),m.end(),number(m[1] or m[2])))
    from src.material_reference_regions import reference_regions
    regions=reference_regions(text)
    return sorted(m for m in candidates if not any(start<=m[0]<end for start,end in regions))
_EXPRESSION = (r'第?' + NUMBER + r'(?:\s*(?:至|到|[-–—~～]|、|,|，|和|及)\s*第?' + NUMBER + r')*')


def number(value):
    if value.isdigit():
        return int(value)
    digits = dict(zip('零〇一二两三四五六七八九', (0,0,1,2,2,3,4,5,6,7,8,9)))
    total = current = 0
    for char in value:
        if char in '十百':
            total += (current or 1) * (10 if char == '十' else 100)
            current = 0
        else:
            current = digits[char]
    return total + current


def numbers(expression):
    """Bounded continuous and non-continuous selections; never infer a gap."""
    tokens = re.findall(NUMBER + r'|至|到|[-–—~～]|、|,|，|和|及', expression)
    found = set(); previous = None; range_next = False
    for token in tokens:
        if re.fullmatch(NUMBER, token):
            value = number(token)
            if not 1 <= value <= 200:
                continue
            if range_next and previous is not None and previous <= value:
                found.update(range(previous, value + 1))
            else:
                found.add(value)
            previous = value; range_next = False
        else:
            range_next = token in ('至','到','-','–','—','~','～')
    return found


_EXTRA = (
    ('提交检查', r'提交(?:前)?(?:检查|核对|复核)'),
    ('上传', r'上传'), ('提交', r'提交'),
    ('检查', r'检查|复核|核对'),
)


def selected_parts(supplement, available):
    selected = set(); completed = set(); active = []
    # A comma inside a numeric enumeration is not a clause boundary.
    clauses = re.split(r'[。；;]|(?<!\d)[，,]|[，,](?!\s*\d)', supplement)
    for clause in clauses:
        done = bool(re.search(r'已(?:经)?(?:经)?(?:完成|做完)|(?:完成|做完)了|已经做完', clause))
        found = set()
        for match in re.finditer(r'(' + _EXPRESSION + r')\s*部分', clause):
            found.update(numbers(match[1]))
        first = re.search(r'前(' + NUMBER + r')(?:个)?部分', clause)
        if first:
            found.update(range(1, min(number(first[1]), 200) + 1))
        remaining = re.search(r'(?:只剩|剩下|仅剩|只做|只估|只需要完成)\s*(' + _EXPRESSION + r')', clause)
        if remaining:
            found.update(numbers(remaining[1]))
        if done:
            completed.update(found)
        else:
            selected.update(found); active.append(clause)
    wanted = selected if selected else set(available) - completed
    extras = []; remaining = '；'.join(active)
    for label, pattern in _EXTRA:
        if re.search(pattern, remaining):
            extras.append(label)
            remaining = re.sub(pattern, '', remaining)
    return tuple(sorted(wanted)), tuple(extras), bool(selected or completed)


@dataclass(frozen=True)
class NumberedScope:
    title: str
    sections: tuple  # (number, exact source text)
    extras: tuple
    explicit: bool

    @property
    def scope(self):
        labels = []
        for index, text in self.sections:
            first = re.split(r'[。]', text)[0].replace('\n','')
            # Keep the numbered identity even when its explanation is long.
            labels.append(first[:min(55, (480-len('；'.join(self.extras)))//len(self.sections)-1)])
        labels.extend(self.extras)
        return '；'.join(labels)

    def context(self):
        return dict(section_numbers=[n for n, _ in self.sections], additional_steps=list(self.extras),
            authoritative_scope=self.scope, user_selected=self.explicit)


def numbered_scope(text, supplement=''):
    text=content_text(text)
    matches = section_markers(text)
    if not matches or not re.search(r'完成|作业|任务|要求|填写|编写|计算|证明', text + supplement):
        return None
    sections = {}
    from src.material_reference_regions import reference_regions
    references=reference_regions(text)
    for i, match in enumerate(matches):
        start,end,index = match
        stop=matches[i+1][0] if i+1<len(matches) else len(text)
        stop=min([stop]+[left for left,_ in references if start<left<stop])
        body = text[start:stop].lstrip('# ').strip()
        # Restore only a broken numbered marker, not arbitrary PDF whitespace.
        body = _PART.sub(lambda m:'第'+m[1]+'部分',body,count=1)
        # Repeated reference headings cannot overwrite a fuller section.
        if len(body) > len(sections.get(index, '')):
            sections[index] = body
    wanted, extras, explicit = selected_parts(supplement, sections)
    if not wanted or set(wanted) - set(sections) or len(wanted) > 12:
        # Preserve existing limits; never silently accept a subset as the whole.
        if explicit:
            from src.material_inbox import MaterialError
            raise MaterialError('请核对要完成的部分编号，材料中尚未找到完整对应范围。')
        return None
    retained='\n'.join(sections[n] for n in wanted)
    # Preserve delivery/completion obligations in the displayed scope too,
    # including when they occur after (rather than in) a numbered heading.
    completion=[]
    for label, pattern in (('整理报告', r'报告按|整理.{0,10}报告|整合.{0,10}报告'),
                           ('运行脚本复核', r'重新运行.{0,6}脚本|运行.{0,6}脚本'),
                           ('提交检查', r'提交.{0,6}(?:检查|核对|复核)'),
                           ('上传', r'上传')):
        if re.search(pattern,retained) and label not in extras:
            completion.append(label)
    extras=extras+tuple(completion)
    title = source_title(text)
    result = NumberedScope(title, tuple((n,sections[n]) for n in wanted), extras, explicit)
    if len(result.scope) > 500:
        return None
    return result


# One observed action clause is a unit, not a page, character or document.
_ACTIONS = (
    ('calculation', r'计算|构造|进行.{0,10}检验|比较.{0,12}(?:均值|平均)|求解|推导'),
    ('proof', r'证明(?!材料|文件|附件)'),
    ('chart', r'绘制|制作.{0,12}(?:图|表)|画出|建立.{0,25}(?:图|表)'),
    ('long_text', r'撰写|写一段|写.{0,8}(?:描述|讨论)|报告按'),
    ('short_text', r'写出|解释|说明|讨论|修订|修改.{0,8}(?:摘要|文本|短文|文字|报告)'),
    ('code', r'编写|编程|调试|运行.{0,8}(?:脚本|代码|程序)|脚本.{0,8}运行'),
    ('review', r'检查|核对|复核|确认|校对'),
    ('attachment', r'整理.{0,12}(?:附件|证明|材料)|准备.{0,12}(?:附件|证明|材料)'),
    ('submit', r'上传|提交|交付|递交|交给'),
)


def scope_workload(boundary, model_work=()):
    """Keep source-backed actions even if a model feature/item is rejected."""
    from src.material_workload import Feature, Workload, MAX_FEATURES, evidence_matches
    features = []; seen = set()
    source = '\n'.join(t for _, t in boundary.sections)
    for _, body in boundary.sections:
        before = len(features)
        for clause in re.split(r'[。；;\n]', body):
            clause = clause.lstrip('# ').strip()
            if not clause or re.match(r'不(?:需|要求|必|要)|禁止|不能|无需|若|如果|允许', clause):
                continue
            for kind, pattern in _ACTIONS:
                match = re.search(pattern, clause)
                if not match:
                    continue
                if kind=='short_text' and re.search(dict(_ACTIONS)['long_text'],clause):
                    continue
                # Negated/optional subordinate instructions are not new work.
                prefix = clause[max(0, match.start()-7):match.start()]
                if re.search(r'不要求|不需要|不要|不能|无需|不再|不(?:是|能仅)|(?:若|如果|可以)', prefix):
                    continue
                evidence = clause[:600]
                key = (kind,evidence)
                if key not in seen:
                    words=max((number(m[1]) for m in re.finditer('('+NUMBER+r')\s*字',evidence)),default=0) if kind=='long_text' else 0
                    features.append(Feature(kind,1,evidence,words));seen.add(key)
        if len(features) == before:
            # A numbered requested work unit is retained, even if its action
            # vocabulary is unfamiliar. This does not assert a specific skill.
            features.append(Feature('problem',1,body[:600]))
    for extra in boundary.extras:
        kind = ('review' if extra in ('检查','提交检查') else 'code' if extra=='运行脚本复核'
            else 'long_text' if extra=='整理报告' else 'submit')
        # Explicit additions already described in the selected section are
        # coverage labels, not duplicate work units.
        if not any(f.kind==kind and (extra in f.evidence or extra in ('检查','整理报告','运行脚本复核')
                or extra=='提交检查' and re.search(r'提交.{0,6}(?:检查|复核|核对)',f.evidence)) for f in features):
            features.append(Feature(kind,1,extra))
    # Models may add valid, selected-scope detail, but cannot replace the source
    # action inventory with a smaller set or reintroduce completed sections.
    for work in model_work:
        for feature in work.features:
            if not evidence_matches(feature.evidence,source):
                continue
            normalized=lambda value:re.sub(r'\s+',' ',value).strip()
            matching=[i for i,f in enumerate(features) if f.kind==feature.kind
                and (normalized(feature.evidence) in normalized(f.evidence)
                     or normalized(f.evidence) in normalized(feature.evidence))]
            if not matching:
                features.append(feature)
            else:
                # A source clause establishes an action; a validated model
                # count can establish several units within that same action.
                # Preserve that richer count without counting the clause twice.
                count=sum(features[i].units for i in matching)
                i=matching[0];old=features[i]
                features[i]=Feature(old.kind,old.units+max(0,feature.units-count),old.evidence,
                    max(old.words,feature.words))
    # Aggregate equivalent kinds without dropping counts at MAX_FEATURES.
    # Full selected paragraphs remain in the first recognition request.
    grouped = {}
    for feature in features:
        key = (feature.kind, feature.words)
        old = grouped.get(key)
        if old:
            grouped[key] = Feature(feature.kind,min(200,old.units+feature.units),old.evidence,feature.words)
        else:
            grouped[key] = feature
    return Workload(boundary.title,boundary.scope,tuple(grouped.values())[:MAX_FEATURES],origin='source_scope')
