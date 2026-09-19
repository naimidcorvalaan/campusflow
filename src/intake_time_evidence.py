"""Lexical clock evidence for already extracted commitment endpoints.

This validates representation equivalence, never creates an event or guesses
its missing time. Event binding remains the intake model's responsibility.
"""
import re
from dataclasses import dataclass

_NUM = r"[0-9零〇一二两三四五六七八九十]{1,3}"
_CLOCK = re.compile(
    r"(?P<period>凌晨|早上|上午|中午|下午|傍晚|晚上)?\s*"
    r"(?P<hour>" + _NUM + r")(?:[:：](?P<colon>[0-5]?\d)|"
    r"[点时](?:(?P<half>半)|(?P<quarter>[一三])刻|(?P<minute>" + _NUM + r")分?)?)"
)
_RANGE = re.compile(r"^\s*(?:到|至|[-—–~～])\s*$")
_END = re.compile(r"^\s*(?:下课|结束|散会|结束课程)")


def _number(value):
    if value.isdecimal():
        return int(value)
    digits = dict(zip('零〇一二两三四五六七八九', (0,0,1,2,2,3,4,5,6,7,8,9)))
    if '十' in value:
        parts = value.split('十')
        if len(parts) != 2 or any(len(x)>1 for x in parts):
            return None
        left, right = parts
        return digits.get(left, 1)*10 + digits.get(right, 0)
    if any(c not in digits for c in value):
        return None
    return int(''.join(str(digits[c]) for c in value))


@dataclass(frozen=True)
class ClockEvidence:
    start: int
    end: int
    minutes: int
    period: str


def clocks(text):
    result = []
    for match in _CLOCK.finditer(text):
        hour = _number(match['hour'])
        minute = (30 if match['half'] else 15 if match['quarter']=='一' else
                  45 if match['quarter']=='三' else _number(match['minute']) if match['minute'] else
                  int(match['colon']) if match['colon'] else 0)
        if hour is None or minute is None or hour > 23 or minute > 59:
            continue
        period = match['period'] or ''
        # Only a connected range inherits AM/PM, not an unrelated later event.
        if not period and result and _RANGE.fullmatch(text[result[-1].end:match.start()]):
            period = result[-1].period
        if period in ('下午','晚上','傍晚') and hour < 12:
            hour += 12
        elif period in ('凌晨','早上','上午') and hour == 12:
            hour = 0
        elif period == '中午' and hour < 11:
            hour += 12
        result.append(ClockEvidence(match.start(), match.end(), hour*60+minute, period))
    return tuple(result)


def supports_commitment_end(starts_at, ends_at, text):
    """Check a linked range or an explicit end label, not an unrelated clock."""
    expected = int(ends_at.split(':')[0])*60 + int(ends_at.split(':')[1])
    start = int(starts_at.split(':')[0])*60 + int(starts_at.split(':')[1]) if starts_at else None
    values = clocks(text)
    for index, value in enumerate(values):
        if value.minutes != expected:
            continue
        if _END.match(text[value.end:]):
            return True
        if re.search(r'(?:下课|结束|散会)(?:时间)?(?:是|在|为|到)?\s*$', text[:value.start]):
            return True
        if index and values[index-1].minutes == start and _RANGE.fullmatch(text[values[index-1].end:value.start]):
            return True
    return False
