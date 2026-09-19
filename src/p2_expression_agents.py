"""P3e Qwen-heavy 多 Agent 计划表达层（Python 3.8 兼容）。

职责分工：
- 程序负责：地图 node / route distance / 时间数学 / capacity / lifecycle /
  state consistency / schema validation / call budget / sanitizer；
- Qwen Agent 只读取“已确定的计划事实”，不修改结构化计划：
  A. Plan Narrator Agent        -> opening（活泼、1~2句）
  B. Warm Companion Agent       -> change_summary / closing（温暖、2~4句）
  C. Final Copy Reviewer        -> 只审核 opening / change_summary / gap_tip / closing
  D. Plan Critic Agent          -> 计划质量审查（不改硬事实）
  E. Plan Improver Agent        -> 仅 Critic revision_needed 时调用，最多一轮
  F. Lifestyle & Gap Reviewer   -> 校园生活观感轻量审查 + gap_tip（仅真实空档）

每个 Agent 最多 1 次正常调用 + 1 次 repair；整轮共享有限调用预算
MAX_EXPRESSION_CALLS_PER_TURN，防止死循环但不阻止合理多 Agent 分工。
任何 Agent 失败都转安全 fallback，不影响主计划。
"""

import dataclasses
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from src.p2_agentic_models import P2AgenticDayResult
from src.p2_agentic_parser import extract_json_object
from src.p2_allocator import allocate_tasks_across_windows
from src.p2_companion_copy import (
    CONTEXT_FEEDBACK,
    CONTEXT_INITIAL,
    CONTEXT_REFRESH,
    CompanionCopy,
    companion_fallbacks,
    extract_plan_facts,
)
from src.p2_day_plan import summarize_day_plan
from src.p2_models import DayPlanningState, TaskState
from src.p3_map_schema import TransportMode, parse_transport_mode
from src.p3_class_prep import class_prep_display_segments, class_prep_interval

PLAN_CRITIC_SCHEMA = "p3.plan-critic.v1"
PLAN_IMPROVER_SCHEMA = "p3.plan-improver.v1"
LIFESTYLE_SCHEMA = "p3.lifestyle-review.v1"
NARRATOR_SCHEMA = "p3.plan-narrator.v1"
WARM_SCHEMA = "p3.warm-companion.v1"
COPY_REVIEW_SCHEMA = "p3.copy-review.v1"

MAX_EXPRESSION_CALLS_PER_TURN = 16

logger = logging.getLogger("campusflow.expression")
MAX_PLAN_QUALITY_CALLS = 4
MAX_FIELD_LENGTH = 200
MAX_REASON_LENGTH = 300

AgentCaller = Callable[[str, str], str]


class ExpressionBudgetExceeded(Exception):
    """整轮表达层调用预算耗尽（防死循环）。"""


class _BudgetCaller(object):
    """共享调用预算：所有表达层 Agent（含 repair）共用同一计数器。"""

    def __init__(self, caller: AgentCaller, max_calls: int):
        if not callable(caller):
            raise TypeError("caller must be callable")
        if isinstance(max_calls, bool) or not isinstance(max_calls, int):
            raise ValueError("max_calls must be an integer")
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1")
        self.caller = caller
        self.max_calls = max_calls
        self.count = 0

    def __call__(self, system: str, user: str) -> Optional[str]:
        if self.count >= self.max_calls:
            raise ExpressionBudgetExceeded("expression call budget exceeded")
        self.count += 1
        return self.caller(system, user)


# ---------------------------------------------------------------------------
# 结果模型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CriticIssue:
    issue_type: str
    severity: str
    message: str

    def __post_init__(self):
        if not isinstance(self.issue_type, str) or not self.issue_type.strip():
            raise ValueError("issue_type must be non-empty")
        if self.severity not in ("low", "medium", "high"):
            raise ValueError("severity must be low/medium/high")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be non-empty")


@dataclass(frozen=True)
class PlanCriticResult:
    approved: bool
    issues: Tuple[CriticIssue, ...]
    revision_needed: bool
    generated: bool

    def __post_init__(self):
        if not isinstance(self.approved, bool) or not isinstance(self.revision_needed, bool):
            raise ValueError("approved/revision_needed must be bool")
        for issue in self.issues:
            if not isinstance(issue, CriticIssue):
                raise TypeError("issues must contain CriticIssue instances")
        if self.revision_needed and not self.issues:
            raise ValueError("revision_needed requires at least one issue")


@dataclass(frozen=True)
class ImproverSuggestion:
    task_order_titles: Tuple[str, ...]
    include_low_attention: bool
    reason: Optional[str]
    generated: bool

    def __post_init__(self):
        for title in self.task_order_titles:
            if not isinstance(title, str) or not title.strip():
                raise ValueError("task_order_titles entries must be non-empty strings")
        if not isinstance(self.include_low_attention, bool):
            raise ValueError("include_low_attention must be a bool")
        if self.reason is not None:
            if not isinstance(self.reason, str) or len(self.reason) > MAX_REASON_LENGTH:
                raise ValueError("reason must be a short string or None")

    def applicable(self, state: DayPlanningState) -> bool:
        """是否至少有一个可执行建议（能映射出任务顺序或允许低注意力窗口）。"""
        if self.include_low_attention:
            return True
        refs = _titles_to_refs(self.task_order_titles, state.tasks)
        return len(refs) >= 2


@dataclass(frozen=True)
class LifestyleReview:
    notes: Tuple[str, ...]
    user_facing_hint: Optional[str]
    gap_tip: Optional[str] = None
    generated: bool = False

    def __post_init__(self):
        for note in self.notes:
            if not isinstance(note, str) or not note.strip():
                raise ValueError("notes entries must be non-empty strings")
        if self.user_facing_hint is not None and not isinstance(self.user_facing_hint, str):
            raise ValueError("user_facing_hint must be a str or None")
        if self.gap_tip is not None and not isinstance(self.gap_tip, str):
            raise ValueError("gap_tip must be a str or None")


@dataclass(frozen=True)
class NarratorCopy:
    opening: str
    generated: bool

    def __post_init__(self):
        if not isinstance(self.opening, str) or not self.opening.strip():
            raise ValueError("opening must be non-empty")


@dataclass(frozen=True)
class WarmCopy:
    change_summary: Optional[str]
    closing: str
    generated: bool

    def __post_init__(self):
        if not isinstance(self.closing, str) or not self.closing.strip():
            raise ValueError("closing must be non-empty")
        if self.change_summary is not None and not isinstance(self.change_summary, str):
            raise ValueError("change_summary must be a str or None")


@dataclass(frozen=True)
class CopyReviewResult:
    approved: bool
    revised: Optional[Dict[str, object]]
    reason: Optional[str]
    generated: bool

    def __post_init__(self):
        if not isinstance(self.approved, bool):
            raise ValueError("approved must be a bool")
        if self.reason is not None and not isinstance(self.reason, str):
            raise ValueError("reason must be a str or None")



# ---------------------------------------------------------------------------
# 计划事实提取（扩展 extract_plan_facts）
# ---------------------------------------------------------------------------


def _fmt_hm(value) -> str:
    if value is None:
        return "（时间待定）"
    return value.strftime("%H:%M")


def _mode_label(mode) -> str:
    value = getattr(mode, "value", mode)
    return {"walk": "步行", "bike": "骑行"}.get(value, "出行")


def _block_mode(block) -> TransportMode:
    try:
        return parse_transport_mode(getattr(block, "mode", None))
    except ValueError:
        return TransportMode.WALK

_LOCATION_EXTENSION_SUFFIXES = (
    "东门",
    "西门",
    "南门",
    "北门",
    "校区",
    "东区",
    "西区",
    "南区",
    "北区",
)


def _canonical_node_name(map_data, node_id, fallback):
    """取节点最短别名作为用户可见/文案引用名称；map_data 缺失时用 fallback。"""
    if map_data is None or not node_id:
        return fallback
    for node in map_data.nodes:
        if node.id == node_id:
            candidates = [alias for alias in node.aliases if len(alias) >= 2]
            if candidates:
                return min(candidates, key=lambda item: (len(item), item))
            return node.name or fallback
    return fallback


def _aliases_for_node(map_data, node_id):
    if map_data is None or not node_id:
        return ()
    for node in map_data.nodes:
        if node.id == node_id:
            return tuple(node.aliases)
    return ()


def _trusted_location_dicts(movement_blocks, current_location, map_data):
    """本轮计划相关受信地点：node_id + canonical 显示名 + 允许别名。"""
    locations = []
    seen = set()

    def _add(node_id, display_name, allowed_aliases):
        key = node_id or display_name
        if not key or key in seen:
            return
        seen.add(key)
        locations.append(
            {
                "node_id": node_id,
                "display_name": display_name,
                "allowed_aliases": tuple(allowed_aliases or ()),
            }
        )

    for block in movement_blocks or ():
        origin_id = getattr(block, "origin_node_id", None)
        dest_id = getattr(block, "destination_node_id", None)
        origin_name = getattr(block, "origin_name", None) or getattr(block, "origin_text", None)
        dest_name = (
            getattr(block, "destination_short_name", None)
            or getattr(block, "destination_name", None)
            or getattr(block, "destination_text", None)
        )
        _add(
            origin_id,
            _canonical_node_name(map_data, origin_id, origin_name or "起点"),
            _aliases_for_node(map_data, origin_id),
        )
        _add(
            dest_id,
            _canonical_node_name(map_data, dest_id, dest_name or "终点"),
            _aliases_for_node(map_data, dest_id),
        )
    if current_location:
        _add(None, current_location, ())
    return tuple(locations)


def _location_lexicon(map_data, trusted_locations):
    """校验用受信地点词表：优先全量地图（名称+别名），退化为计划相关地点。"""
    tokens = set()
    if map_data is not None:
        for node in map_data.nodes:
            if node.name and len(node.name) >= 2:
                tokens.add(node.name)
            for alias in node.aliases:
                if len(alias) >= 2:
                    tokens.add(alias)
    if not tokens:
        for loc in trusted_locations or ():
            if loc.get("display_name"):
                tokens.add(loc["display_name"])
            for alias in loc.get("allowed_aliases") or ():
                if alias:
                    tokens.add(alias)
    return tuple(sorted(tokens, key=len, reverse=True))


def _location_lexicon_tokens(facts):
    """受信地点 token 集：优先 facts 中的全量词表，退化为计划相关地点。"""
    lexicon = facts.get("location_lexicon") or ()
    if lexicon:
        return tuple(lexicon)
    return _location_lexicon(None, facts.get("trusted_locations") or ())


def validate_expression_locations(text, facts) -> bool:
    """程序级地点事实校验：文案引用的地点必须来自受信地点，禁止拼接/扩写/改名。

    返回 True 表示地点事实安全；False 表示检测到不受信地点名称。
    仅当 facts 提供了受信地点词表时才启用；无地点上下文的文案不拦截。
    """
    if not isinstance(text, str) or not text.strip():
        return True
    for foreign_name in facts.get("foreign_campus_names") or ():
        if foreign_name and foreign_name in text:
            return False
    assumed_name = facts.get("assumed_current_location")
    if assumed_name:
        # An inferred origin must never be narrated as verified user state.
        # Conditional phrasing such as “先按你在图书馆来安排” remains allowed.
        asserted = re.compile(
            r"(?:正在|你现在(?:就)?在|你就(?:在)?)\s*{}".format(
                re.escape(assumed_name)
            )
        )
        if asserted.search(text):
            return False
    tokens = _location_lexicon_tokens(facts)
    if not tokens:
        return True
    known = set(tokens)
    max_len = len(text)
    for t1 in tokens:
        for t2 in tokens:
            if t1 == t2:
                continue
            combined = t1 + t2
            if len(combined) > max_len or combined in known:
                continue
            if combined in text:
                return False
    for token in tokens:
        for ext in _LOCATION_EXTENSION_SUFFIXES:
            combined = token + ext
            if len(combined) <= max_len and combined not in known and combined in text:
                return False
            combined2 = ext + token
            if len(combined2) <= max_len and combined2 not in known and combined2 in text:
                return False
    return True


def _parse_hm(value):
    """把 'HH:MM' 或 datetime 解析为 (hour, minute)；失败返回 None。"""
    if hasattr(value, "hour"):
        return (value.hour, value.minute)
    if not isinstance(value, str):
        return None
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", value)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


_PM_MARKERS = ("下午", "晚上", "傍晚")


def _normalize_claim_time(hour, minute, marker, trusted):
    """把文案中的时间说法规范化为 (hour, minute)，带有限口语推断。"""
    minute = minute if minute is not None else 0
    if marker in _PM_MARKERS:
        if hour == 12:
            hour = 0  # 晚上12点 = 0:00
        elif hour < 12:
            hour += 12
    elif marker == "中午":
        if hour < 12:
            hour += 12
    elif marker == "凌晨" and hour == 12:
        hour = 0
    base = (hour % 24, minute)
    if base in trusted:
        return base
    # 无上下午标记的裸时间：若 (h+12, m) 是受信时间，推断为下午（口语“3点”常指15点）
    if not marker and hour < 12 and ((hour + 12) % 24, minute) in trusted:
        return ((hour + 12) % 24, minute)
    return base


_TIME_CLAIM_COLON_RE = re.compile(
    r"(上午|晚上|早上|凌晨|中午|下午|傍晚)?\s*(\d{1,2})[:：](\d{1,2})"
)
_TIME_CLAIM_CN_RE = re.compile(
    r"(上午|晚上|早上|凌晨|中午|下午|傍晚)?\s*(\d{1,2})点(?:\s*(\d{1,2}))?\s*分?"
)


def _extract_time_claims(text, trusted):
    """提取文案中的明确时间并规范化为 (hour, minute) 集合。"""
    claims = set()
    for m in _TIME_CLAIM_COLON_RE.finditer(text):
        claims.add(
            _normalize_claim_time(int(m.group(2)), int(m.group(3)), m.group(1), trusted)
        )
    for m in _TIME_CLAIM_CN_RE.finditer(text):
        minute = int(m.group(3)) if m.group(3) else 0
        claims.add(
            _normalize_claim_time(int(m.group(2)), minute, m.group(1), trusted)
        )
    return claims


def _trusted_time_set(facts):
    """收集结构化计划中的明确时间（commitment / 移动 / plan span / gap）。"""
    trusted = set()

    def _add(value):
        parsed = _parse_hm(value)
        if parsed is not None:
            trusted.add(parsed)

    for key in ("plan_start", "plan_end"):
        _add(facts.get(key))
    for mv in facts.get("movement_facts") or ():
        _add(mv.get("transition_start"))
        _add(mv.get("movement_start"))
        _add(mv.get("start"))
        _add(mv.get("end"))
    for commitment in facts.get("commitment_facts") or ():
        _add(commitment.get("class_prep_start"))
        _add(commitment.get("building_arrival"))
        _add(commitment.get("classroom_arrival"))
    next_commitment = facts.get("next_commitment")
    if isinstance(next_commitment, str):
        m = re.match(r"^\s*(\d{1,2}):(\d{2})", next_commitment)
        if m:
            trusted.add((int(m.group(1)), int(m.group(2))))
    for line in (facts.get("commitments") or ()) + (facts.get("tasks") or ()):
        if isinstance(line, str):
            for m in re.finditer(r"(\d{1,2}):(\d{2})", line):
                trusted.add((int(m.group(1)), int(m.group(2))))
    for gap in facts.get("idle_gaps") or ():
        _add(gap.get("start"))
        _add(gap.get("end"))
    change_facts = facts.get("change_facts") or {}
    change_next = change_facts.get("next_commitment")
    if isinstance(change_next, str):
        m = re.match(r"^\s*(\d{1,2}):(\d{2})", change_next)
        if m:
            trusted.add((int(m.group(1)), int(m.group(2))))
    return trusted


def _validate_expression_times(text, facts) -> bool:
    """明确时间校验：文案引用的具体时间必须与结构化计划一致（可省略，不可说错）。"""
    trusted = _trusted_time_set(facts)
    if not trusted:
        return True
    claims = _extract_time_claims(text, trusted)
    for claim in claims:
        if claim not in trusted:
            return False
    return True


_WALK_MODE_PHRASES = ("步行", "走路", "走过去", "走去", "走着")
_BIKE_MODE_PHRASES = ("骑车", "骑行", "骑过去", "骑自行车", "骑着", "自行车")
_OTHER_MODE_PHRASES = ("坐车", "开车", "打车", "公交", "地铁", "班车", "坐地铁", "坐公交")


def _validate_expression_mode(text, facts) -> bool:
    """movement mode 校验：文案声称的交通方式必须与真实移动事实一致。"""
    movement_facts = facts.get("movement_facts") or ()
    if not movement_facts:
        return True  # 无真实移动事实时不拦截（避免误杀“骑行安排保持不变”类表述）
    walk = any(p in text for p in _WALK_MODE_PHRASES)
    bike = any(p in text for p in _BIKE_MODE_PHRASES)
    other = any(p in text for p in _OTHER_MODE_PHRASES)
    if other:
        return False
    has_walk = any(mv.get("mode") == "步行" for mv in movement_facts)
    has_bike = any(mv.get("mode") == "骑行" for mv in movement_facts)
    if walk and not has_walk:
        return False
    if bike and not has_bike:
        return False
    return True


_DURATION_APPROX_RE = re.compile(
    r"(约|大概|差不多|大约|将近|近)\s*(\d{1,3})\s*分钟"
)
_DURATION_RANGE_RE = re.compile(r"(\d{1,3})\s*分钟\s*(左右|上下|出头)")
_DURATION_TRAVEL_RE = re.compile(
    r"(\d{1,3})\s*分钟\s*(就到|就能到|能到|到达|赶到|即到|便到|可以到|路程)"
)

_CN_NUM_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_DURATION_CN_TRAVEL_RE = re.compile(
    r"([零一二两三四五六七八九十几]+)\s*分钟\s*(就到|就能到|能到|到达|赶到|即到|便到|可以到|路程)"
)


def _cn_duration_interval(token):
    """把中文数字时长（十/十几/二十五等）解析为 [lo, hi] 分钟区间；无法解析返回 None。"""
    if token in _CN_NUM_DIGITS:
        value = _CN_NUM_DIGITS[token]
        return (value, value)
    if token == "十":
        return (10, 10)
    if token == "十几":
        return (10, 19)
    if token.endswith("来"):  # 十来/二十来 按整十上下浮动处理
        base = _cn_duration_interval(token[:-1])
        if base is not None:
            return (base[0], base[1] + 4)
    if token.startswith("十"):
        tail = token[1:]
        if tail == "几":
            return (10, 19)
        if tail in _CN_NUM_DIGITS:
            value = 10 + _CN_NUM_DIGITS[tail]
            return (value, value)
    if len(token) == 2 and token[0] in _CN_NUM_DIGITS and token[1] == "十":
        value = _CN_NUM_DIGITS[token[0]] * 10
        return (value, value)
    if len(token) == 3 and token[0] in _CN_NUM_DIGITS and token[1] == "十":
        base = _CN_NUM_DIGITS[token[0]] * 10
        tail = token[2]
        if tail == "几":
            return (base, base + 9)
        if tail in _CN_NUM_DIGITS:
            value = base + _CN_NUM_DIGITS[tail]
            return (value, value)
    return None


def _movement_duration_minutes(facts):
    minutes = set()
    for mv in facts.get("movement_facts") or ():
        value = mv.get("estimated_minutes")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            minutes.add(value)
    return minutes


def _validate_expression_duration(text, facts) -> bool:
    """travel duration 校验：文案明说移动耗时必须与 effective 时长一致（约±1分钟）。"""
    durations = _movement_duration_minutes(facts)
    if not durations:
        return True

    def _match(n):
        return any(abs(n - d) <= 1 for d in durations)

    for m in _DURATION_APPROX_RE.finditer(text):
        if not _match(int(m.group(2))):
            return False
    for m in _DURATION_RANGE_RE.finditer(text):
        if not _match(int(m.group(1))):
            return False
    for m in _DURATION_TRAVEL_RE.finditer(text):
        if not _match(int(m.group(1))):
            return False
    for m in _DURATION_CN_TRAVEL_RE.finditer(text):
        interval = _cn_duration_interval(m.group(1))
        if interval is None:
            continue
        lo, hi = interval
        if not any(lo - 1 <= d <= hi + 1 for d in durations):
            return False
    return True


def _relative_minutes_claims(text):
    claims = []
    claims.extend(30 for _ in re.finditer(r"还有\s*半小时", text))
    claims.extend(int(m.group(1)) for m in re.finditer(r"还有\s*(\d{1,3})\s*分钟", text))
    claims.extend(30 for _ in re.finditer(r"半小时\s*后", text))
    claims.extend(int(m.group(1)) for m in re.finditer(r"(\d{1,3})\s*分钟\s*后", text))
    return claims


def _validate_relative_times(text, facts) -> bool:
    """相对时间校验：只有事实明确支持时才允许“还有X分钟/半小时”类说法。"""
    minutes_to_next = facts.get("minutes_to_next_commitment")
    if minutes_to_next is None:
        return True
    gap_minutes = {
        int(gap.get("minutes") or 0) for gap in facts.get("idle_gaps") or ()
    }
    for claim in _relative_minutes_claims(text):
        if abs(claim - minutes_to_next) <= 3:
            continue
        if any(abs(claim - gap) <= 3 for gap in gap_minutes):
            continue
        return False
    return True


def validate_expression_facts(text, facts) -> bool:
    """统一表达层事实校验：地点 / 明确时间 / movement mode / travel duration / 相对时间。

    只校验“文案主动声称的硬事实”，不要求文案必须提到这些事实。
    """
    if not isinstance(text, str) or not text.strip():
        return True
    if not validate_expression_locations(text, facts):
        logger.debug("[CampusFlow][expression] validation_failed: location text=%r", text[:80])
        return False
    if not _validate_expression_times(text, facts):
        logger.debug("[CampusFlow][expression] validation_failed: time_claim text=%r", text[:80])
        return False
    if not _validate_event_time_binding(text, facts):
        logger.debug("[CampusFlow][expression] validation_failed: event_time_binding text=%r", text[:80])
        return False
    if not _validate_current_future_semantics(text, facts):
        logger.debug("[CampusFlow][expression] validation_failed: current_future text=%r", text[:80])
        return False
    if not _validate_expression_mode(text, facts):
        logger.debug("[CampusFlow][expression] validation_failed: mode_claim text=%r", text[:80])
        return False
    if not _validate_expression_duration(text, facts):
        logger.debug("[CampusFlow][expression] validation_failed: duration_claim text=%r", text[:80])
        return False
    if not _validate_relative_times(text, facts):
        logger.debug("[CampusFlow][expression] validation_failed: relative_time text=%r", text[:80])
        return False
    return True


def _location_whitelist_text(facts) -> str:
    """给 Qwen 的受信地点白名单（本轮计划相关 canonical 名称）。"""
    names = []
    seen = set()
    for loc in facts.get("trusted_locations") or ():
        name = loc.get("display_name")
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return "、".join(names)


def _warm_facts_ok(warm, facts) -> bool:
    return (
        validate_expression_facts(warm.change_summary, facts)
        and validate_expression_facts(warm.closing, facts)
    )


def _revised_facts_ok(revised, facts) -> bool:
    if not isinstance(revised, dict):
        return True
    for name in _COPY_FIELD_NAMES:
        if not validate_expression_facts(revised.get(name), facts):
            return False
    return True




def _schedule_intervals(state, allocation_plan, movement_blocks):
    """已确定的时间段集合（移动块 + 分配片段 + 固定安排），用于 plan span。"""
    now = state.now
    window_by_ref = {window.window_ref: window for window in state.windows}
    intervals = []
    for block in movement_blocks or ():
        start = getattr(block, "window_start", None)
        end = getattr(block, "end_time", None)
        if start is not None and end is not None and end > now:
            intervals.append((start, end))
    for allocation in allocation_plan.allocations:
        if allocation.starts_at is not None:
            end = allocation.starts_at + timedelta(minutes=allocation.planned_minutes)
            if end > now:
                intervals.append((allocation.starts_at, end))
            continue
        window = window_by_ref.get(allocation.window_ref)
        if window is None:
            continue
        start = window.starts_at
        for block in movement_blocks or ():
            if getattr(block, "window_ref", None) == allocation.window_ref:
                block_end = getattr(block, "end_time", None)
                if block_end is not None and block_end > start:
                    start = block_end
        end = start + timedelta(minutes=allocation.planned_minutes)
        if end > now:
            intervals.append((start, end))
    for commitment in state.commitments:
        if (
            commitment.starts_at is not None
            and commitment.ends_at is not None
            and commitment.ends_at > now
        ):
            intervals.append((commitment.starts_at, commitment.ends_at))
    return intervals


def _idle_gaps(state, allocation_plan, movement_blocks):
    """Derive idle only as the complement of the final rendered timeline.

    Window tails alone are insufficient after P4 inserts a movement block or
    splits a class preparation interval.  Every user-facing claim about free
    time must see the exact same task/movement/prep/commitment segments that
    presentation renders.
    """
    occupied = []
    for segment in _expression_segments(state, allocation_plan, movement_blocks):
        start, end = segment.get("start"), segment.get("end")
        if start is not None and end is not None and end > state.now and end > start:
            occupied.append((max(start, state.now), end))
    occupied.sort(key=lambda item: (item[0], item[1]))
    cursor = state.now
    gaps = []
    for start, end in occupied:
        if start > cursor:
            gaps.append({
                "start": cursor,
                "end": start,
                "minutes": int((start - cursor).total_seconds() // 60),
            })
        if end > cursor:
            cursor = end
    if cursor < state.day_end:
        gaps.append({
            "start": cursor,
            "end": state.day_end,
            "minutes": int((state.day_end - cursor).total_seconds() // 60),
        })
    return tuple(item for item in gaps if item["minutes"] >= 1)


def _remaining_usable_minutes(state) -> int:
    now = state.now
    total = 0
    for window in state.windows:
        if window.ends_at <= now:
            continue
        usable_start = max(window.starts_at, now)
        minutes = int((window.ends_at - usable_start).total_seconds() // 60)
        if minutes > 0:
            total += minutes
    return total


def _evening_free(state) -> bool:
    for window in state.windows:
        if window.ends_at <= state.now:
            continue
        if window.starts_at.hour >= 18 or window.ends_at.hour > 18:
            return True
    return False


def _expression_segments(state, allocation_plan, movement_blocks):
    """Return user-facing schedule segments with stable kinds for copy facts."""
    segments = []
    task_by_ref = {task.task_ref: task for task in state.tasks}
    windows = {window.window_ref: window for window in state.windows}
    for block in movement_blocks or ():
        transition_start = getattr(block, "transition_start", None)
        movement_start = getattr(block, "window_start", None)
        end = getattr(block, "end_time", None)
        if transition_start is not None and movement_start is not None and transition_start < movement_start:
            segments.append({"kind": "transition", "title": "收拾东西", "start": transition_start, "end": movement_start})
        if movement_start is not None and end is not None:
            segments.append({"kind": "movement", "title": "{}前往{}".format(_mode_label(_block_mode(block)), getattr(block, "destination_short_name", None) or getattr(block, "destination_name", None) or getattr(block, "destination_text", "目的地")), "start": movement_start, "end": end})
    for commitment in state.commitments:
        for prep_start, prep_end, prep_title in class_prep_display_segments(commitment):
            segments.append({"kind": "class_prep", "title": prep_title, "start": prep_start, "end": prep_end})
        if commitment.starts_at is not None:
            # A missing end is not free time. Keep the plan's user-facing
            # commitment open-ended while closing expression audit at day_end.
            segments.append({
                "kind": "commitment",
                "title": commitment.title,
                "start": commitment.starts_at,
                "end": commitment.ends_at or state.day_end,
            })
    for allocation in allocation_plan.allocations:
        window = windows.get(allocation.window_ref)
        task = task_by_ref.get(allocation.task_ref)
        if task is not None and allocation.starts_at is not None:
            segments.append({"kind": "background" if task.attention_mode == "background" else "task",
                "title": task.title, "start": allocation.starts_at,
                "end": allocation.starts_at + timedelta(minutes=allocation.planned_minutes)})
            continue
        if window is None or task is None:
            continue
        start = window.starts_at
        for block in movement_blocks or ():
            if getattr(block, "window_ref", None) == allocation.window_ref:
                block_start = getattr(block, "window_start", None)
                block_end = getattr(block, "end_time", None)
                # A P4 sequence block keeps the ref of the free interval it
                # split, but can sit immediately *after* that interval.  Such
                # a block must not shift the earlier task past its departure
                # and make expression facts claim it continues elsewhere.
                if (
                    block_start is not None
                    and block_end is not None
                    and window.starts_at <= block_start < window.ends_at
                    and block_end <= window.ends_at
                    and block_end > start
                ):
                    start = block_end
        for prior in sorted((a for a in allocation_plan.allocations if a.window_ref == allocation.window_ref), key=lambda a: (a.sequence_index, a.allocation_ref)):
            if prior.allocation_ref == allocation.allocation_ref:
                break
            start += timedelta(minutes=prior.planned_minutes)
        start = allocation.starts_at or start
        segments.append({"kind": "background" if task.attention_mode == "background" else "task", "title": task.title, "start": start, "end": start + timedelta(minutes=allocation.planned_minutes)})
    return tuple(sorted(segments, key=lambda item: (item["start"], item["kind"])))


def _public_segment(segment):
    if segment is None:
        return None
    return {"kind": segment["kind"], "title": segment["title"], "start": _fmt_hm(segment["start"]), "end": _fmt_hm(segment["end"])}


def _current_expression_segment(state, allocation_plan, movement_blocks):
    now = state.now
    for segment in _expression_segments(state, allocation_plan, movement_blocks):
        end = segment["end"]
        if segment["kind"] != "background" and segment["start"] <= now and (end is None or now < end):
            return _public_segment(segment)
    return None


def _next_expression_segment(state, allocation_plan, movement_blocks):
    now = state.now
    for segment in _expression_segments(state, allocation_plan, movement_blocks):
        if segment["start"] > now:
            return _public_segment(segment)
    return None


def extract_expression_facts(
    state,
    allocation_plan,
    movement_blocks=(),
    current_location=None,
    assumed_current_location=None,
    change_facts=None,
    map_data=None,
):
    """扩展的确定性计划事实（供所有表达层 Agent 读取，不含内部 ref/schema）。"""
    base = extract_plan_facts(state, allocation_plan, tuple(movement_blocks or ()))
    now = state.now
    intervals = _schedule_intervals(state, allocation_plan, movement_blocks)
    plan_start = None
    plan_end = None
    if intervals:
        plan_start = min(start for start, _ in intervals)
        plan_end = max(end for _, end in intervals)
    if plan_start is None:
        plan_start = now
    if plan_end is None:
        plan_end = state.day_end
    span_minutes = max(int((plan_end - plan_start).total_seconds() // 60), 0)
    only_afternoon = 12 <= plan_start.hour and plan_end.hour <= 19
    only_evening = plan_start.hour >= 18

    gaps = _idle_gaps(state, allocation_plan, movement_blocks)
    idle_minutes = sum(gap["minutes"] for gap in gaps)
    scheduled_minutes = sum(a.planned_minutes for a in allocation_plan.allocations) + sum(
        int(getattr(b, "estimated_minutes", 0) or 0) for b in (movement_blocks or ())
    )

    future_commitments = sorted(
        (c for c in state.commitments if c.starts_at is not None and c.starts_at > now),
        key=lambda c: c.starts_at,
    )
    next_commitment = None
    minutes_to_next = None
    if future_commitments:
        head = future_commitments[0]
        next_commitment = "{} {}".format(_fmt_hm(head.starts_at), head.title)
        minutes_to_next = int((head.starts_at - now).total_seconds() // 60)

    task_by_ref = {task.task_ref: task for task in state.tasks}
    unallocated_refs = set(allocation_plan.unallocated_task_refs)
    unallocated_splittable = [
        task.title
        for ref in sorted(unallocated_refs)
        for task in [task_by_ref.get(ref)]
        if task is not None
        and task.state == TaskState.ACTIVE
        and task.is_splittable is True
    ]
    tasks_detail = []
    for task in state.tasks:
        if task.state != TaskState.ACTIVE:
            continue
        remaining = task.remaining_minutes
        splittable_label = (
            "可分段"
            if task.is_splittable is True
            else ("不可分段" if task.is_splittable is False else "未确认")
        )
        tasks_detail.append(
            {
                "title": task.title,
                "remaining_minutes": remaining,
                "splittable": splittable_label,
                "minimum_slice_minutes": task.minimum_slice_minutes,
                "completed_minutes": task.completed_minutes,
            }
        )

    movement_facts = []
    location_change_count = 0
    peak_movement_count = 0
    transition_buffer_count = 0
    for block in movement_blocks or ():
        origin = _canonical_node_name(
            map_data,
            getattr(block, "origin_node_id", None),
            getattr(block, "origin_name", None) or getattr(block, "origin_text", "起点"),
        )
        dest = _canonical_node_name(
            map_data,
            getattr(block, "destination_node_id", None),
            getattr(block, "destination_short_name", None)
            or getattr(block, "destination_name", None)
            or getattr(block, "destination_text", "终点"),
        )
        start = getattr(block, "window_start", None)
        end = getattr(block, "end_time", None)
        transition = int(getattr(block, "transition_minutes", 0) or 0)
        transition_start = getattr(block, "transition_start", None)
        peak = bool(getattr(block, "peak_bike", False))
        location_change = origin != dest
        if location_change:
            location_change_count += 1
        if peak:
            peak_movement_count += 1
        if transition > 0:
            transition_buffer_count += 1
        movement_facts.append(
            {
                "origin": origin,
                "destination": dest,
                "mode": _mode_label(_block_mode(block)),
                "distance_m": int(getattr(block, "distance_m", 0) or 0),
                "estimated_minutes": int(getattr(block, "estimated_minutes", 0) or 0),
                "peak_bike": peak,
                "buffer_minutes": transition,
                "transition_start": _fmt_hm(transition_start) if transition_start else None,
                "movement_start": _fmt_hm(start),
                "start": _fmt_hm(start),
                "end": _fmt_hm(end),
                "location_change": location_change,
            }
        )

    commitment_facts = []
    for commitment in state.commitments:
        if commitment.starts_at is not None:
            prep = class_prep_interval(commitment)
            prep_segments = class_prep_display_segments(commitment)
            commitment_facts.append(
                {"title": commitment.title, "starts_at": _fmt_hm(commitment.starts_at),
                 "class_prep_start": _fmt_hm(prep[0]) if prep is not None else None,
                 "building_arrival": _fmt_hm(prep[0]) if prep is not None else None,
                 "classroom_arrival": _fmt_hm(prep_segments[-1][0]) if prep_segments else None}
            )

    current_segment = _current_expression_segment(state, allocation_plan, movement_blocks)
    next_segment = _next_expression_segment(state, allocation_plan, movement_blocks)

    result = dict(base)
    trusted_locations = _trusted_location_dicts(
        movement_blocks, current_location, map_data
    )
    result.update(
        {
            "reference_datetime": now.strftime("%Y-%m-%d %H:%M"),
            "plan_start": _fmt_hm(plan_start),
            "plan_end": _fmt_hm(plan_end),
            "plan_span_minutes": span_minutes,
            "only_afternoon": only_afternoon,
            "only_evening": only_evening,
            "idle_gaps": gaps,
            "idle_minutes": idle_minutes,
            "scheduled_minutes": scheduled_minutes,
            "next_commitment": next_commitment,
            "minutes_to_next_commitment": minutes_to_next,
            "evening_free": _evening_free(state),
            "remaining_minutes": _remaining_usable_minutes(state),
            "tasks_detail": tuple(tasks_detail),
            "unallocated_splittable_titles": tuple(unallocated_splittable),
            "movement_facts": tuple(movement_facts),
            "commitment_facts": tuple(commitment_facts),
            "current_segment": current_segment,
            "next_segment": next_segment,
            "movement_count": len(movement_facts),
            "peak_movement_count": peak_movement_count,
            "transition_buffer_count": transition_buffer_count,
            "location_change_count": location_change_count,
            "current_location": current_location,
            "assumed_current_location": assumed_current_location,
            "trusted_locations": trusted_locations,
            "location_lexicon": _location_lexicon(map_data, trusted_locations),
            "foreign_campus_names": _foreign_campus_names(map_data),
        }
    )
    if change_facts is not None:
        result["change_facts"] = change_facts
    return result


def _foreign_campus_names(map_data):
    """Reject campus-qualified names from the other local map in copy."""
    campus_id = getattr(map_data, "campus_id", None)
    if campus_id == "weijinlu":
        return ("北洋园校区", "天津大学北洋园校区")
    if campus_id == "beiyangyuan":
        return ("卫津路校区", "天津大学卫津路校区")
    return ()


def _titles_to_refs(titles, tasks) -> Optional[Tuple[str, ...]]:
    """按标题映射任务顺序；标题不存在/不唯一时跳过，全部失败返回 None。"""
    if not titles:
        return None
    refs = []
    for title in titles:
        matches = [task.task_ref for task in tasks if task.title == title]
        if len(matches) == 1:
            refs.append(matches[0])
    if not refs:
        return None
    seen = set()
    unique = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            unique.append(ref)
    return tuple(unique)


def apply_improver_result(
    result: P2AgenticDayResult, improver: ImproverSuggestion
) -> Optional[P2AgenticDayResult]:
    """按 Improver 建议重新走确定性 allocator，返回新结果；无法安全应用返回 None。"""
    state = result.updated_state
    task_order = _titles_to_refs(improver.task_order_titles, state.tasks)
    new_plan = allocate_tasks_across_windows(
        state,
        task_order=task_order,
        include_low_attention=improver.include_low_attention,
    )
    new_summary = summarize_day_plan(new_plan, state)
    try:
        return dataclasses.replace(
            result,
            allocation_plan=new_plan,
            day_summary=new_summary,
            revision_used=True,
        )
    except (TypeError, ValueError):
        return None



# ---------------------------------------------------------------------------
# 通用 prompt 构建工具
# ---------------------------------------------------------------------------


def _facts_lines(facts) -> List[str]:
    lines = [
        "当前时间：{}".format(facts.get("reference_datetime", facts.get("now", ""))),
        "计划覆盖：{} - {}（约{}分钟）".format(
            facts.get("plan_start", ""),
            facts.get("plan_end", ""),
            facts.get("plan_span_minutes", 0),
        ),
    ]
    if facts.get("current_location"):
        lines.append("当前地点：{}".format(facts["current_location"]))
    current_segment = facts.get("current_segment")
    if current_segment:
        lines.append("当前正在进行：{}（{} - {}）".format(
            current_segment["title"], current_segment["start"], current_segment["end"]
        ))
    next_segment = facts.get("next_segment")
    if next_segment:
        lines.append("下一段安排：{}（{}开始）".format(next_segment["title"], next_segment["start"]))
    whitelist = _location_whitelist_text(facts)
    if whitelist:
        lines.append(
            "受信地点名称（引用地点时只能原样使用，禁止拼接/扩写/改名）：{}".format(whitelist)
        )
    if facts.get("next_commitment"):
        detail = "下一固定安排：{}".format(facts["next_commitment"])
        if facts.get("minutes_to_next_commitment") is not None:
            detail += "（约{}分钟后）".format(facts["minutes_to_next_commitment"])
        lines.append(detail)
    if facts.get("tasks_detail"):
        lines.append("待办任务：")
        for task in facts["tasks_detail"]:
            remaining = task.get("remaining_minutes")
            remaining_text = "约{}分钟".format(remaining) if remaining is not None else "时长未知"
            slice_text = ""
            if task.get("minimum_slice_minutes") is not None:
                slice_text = "，最小片段{}分钟".format(task["minimum_slice_minutes"])
            lines.append(
                "- {}（剩余{}，{}）{}".format(
                    task["title"], remaining_text, task.get("splittable", "未确认"), slice_text
                )
            )
    if facts.get("tasks"):
        lines.append("已安排任务：")
        lines.extend("- " + line for line in facts["tasks"])
    if facts.get("commitments"):
        lines.append("固定安排：")
        lines.extend("- " + line for line in facts["commitments"])
    if facts.get("movement_facts"):
        lines.append("移动安排：")
        for mv in facts["movement_facts"]:
            peak_text = "（高峰期骑行）" if mv.get("peak_bike") else ""
            buffer_text = "，出发前{}分钟收拾".format(mv["buffer_minutes"]) if mv.get("buffer_minutes") else ""
            lines.append(
                "- {} {} - {}：{}前往{}{}，约{}分钟{}{}".format(
                    mv["origin"],
                    mv["start"],
                    mv["end"],
                    mv["mode"],
                    mv["destination"],
                    peak_text,
                    mv["estimated_minutes"],
                    buffer_text,
                    "" if mv.get("location_change") else "（同地点）",
                )
            )
    if facts.get("unallocated"):
        lines.append("今天暂未安排但之后可继续：")
        lines.extend("- " + title for title in facts["unallocated"])
    if facts.get("unallocated_splittable_titles"):
        lines.append(
            "未安排的可分段任务：{}".format("、".join(facts["unallocated_splittable_titles"]))
        )
    gaps = facts.get("idle_gaps") or ()
    if gaps:
        lines.append("存在空闲时段：")
        for gap in gaps:
            lines.append(
                "- {} - {}（{}分钟）".format(
                    gap["start"].strftime("%H:%M"), gap["end"].strftime("%H:%M"), gap["minutes"]
                )
            )
    lines.append("晚间可用时间：{}".format("有" if facts.get("evening_free") else "无"))
    lines.append("当前剩余可安排时间：约{}分钟".format(facts.get("remaining_minutes", 0)))
    return lines


def _change_facts_lines(change_facts) -> List[str]:
    lines = []
    if change_facts is None:
        return lines
    if change_facts.get("lines"):
        lines.append("本轮已确认变化：")
        lines.extend("- " + line for line in change_facts["lines"])
    if change_facts.get("next_commitment"):
        detail = "下一固定安排：{}".format(change_facts["next_commitment"])
        if change_facts.get("minutes_to_next_commitment") is not None:
            detail += "（约{}分钟后）".format(change_facts["minutes_to_next_commitment"])
        lines.append(detail)
    lines.append(
        "晚间可用时间：{}".format("有" if change_facts.get("evening_free") else "无")
    )
    lines.append(
        "当前剩余可安排时间：约{}分钟".format(change_facts.get("remaining_minutes", 0))
    )
    return lines


def _facts_text(facts, change_facts=None) -> str:
    lines = _facts_lines(facts)
    if change_facts is not None:
        lines.extend(_change_facts_lines(change_facts))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# A. Plan Narrator Agent
# ---------------------------------------------------------------------------


def build_narrator_prompt(
    facts, context_type=CONTEXT_INITIAL, change_facts=None
) -> Tuple[str, str]:
    context_block = {
        CONTEXT_INITIAL: (
            "文案场景：initial_plan（首次生成全天计划）。\n"
            "opening：2~3句、约40~100个中文字符，活泼自然，像校园生活助理。先自然接住用户，"
            "再告诉用户计划已经整理好了，并轻轻带出收拾和通勤已算进去。不要逐项复述下面的时间表。"
        ),
        CONTEXT_FEEDBACK: (
            "文案场景：feedback_update（用户刚提交反馈，计划已按真实状态重新生成）。\n"
            "opening：1~2句，像一直陪在旁边的助手接话：先自然表达“好嘞/收到/没问题，"
            "已经帮你重新排好啦”，再针对本轮真实变化说一句温暖有帮助的话；"
            "不要重新说“同学你好”，不要机械复读同一句“已为您更新计划”。"
        ),
        CONTEXT_REFRESH: (
            "文案场景：refresh（只是刷新方案，用户没有刚提交反馈）。\n"
            "opening：1~2句普通概览式开场，不假装用户刚刚修改了计划。"
        ),
    }
    system = (
        "你是 CampusFlow 的 Plan Narrator：根据已确定的计划事实生成自然开场白，"
        "只描述和回应真实计划，不新增/修改任务、时间、地点、路线或耗时。\n\n"
        "opening：" + context_block[context_type] + "\n"
        "语气：活泼、亲切、像校园生活助手。允许适量使用“呀/啦/～/好啦/走吧/交给我”，"
        "但不要每句都“～”，不要像低龄儿童，不要像客服，不要像营销文案。\n"
        "禁止：\"尊敬的同学您好\"、\"感谢您使用CampusFlow\"、\"以下为您生成高效智能计划\"、"
        "\"开启充实的一天吧！\"、\"优秀的你一定可以！\"。\n\n"
        "禁止编造：天气、用户情绪/健康/疲劳、不存在的任务/地点/空闲时间、"
        "“晚上还有时间”（除非事实明确有晚间可用）、快迟到、压力大。\n"
        "所有地点名称只能原样使用“受信地点名称”中的名称，禁止自行拼接、扩写或按常识改名（例如不得把“31教”写成“齐园31教学楼”等地图中不存在的名称）。\n"
        "引用明确时间/交通方式/移动耗时必须与真实计划事实一致，不要自己推算时间或改写事实。\n"
        "时间必须和事件绑定：收拾东西只能引用 transition_start，骑行/步行/出发只能引用 movement_start，上课只能引用 commitment_start。\n"
        "严格按“当前正在进行”和“下一段安排”说话；当前段不是收拾或移动时，禁止说“现在准备出发 / 现在去某地 / 这就去上课”，可说先完成当前段、到点再出发。\n"
        "引用明确时间/交通方式/移动耗时必须与真实计划事实一致，不要自己推算时间或改写事实。\n"
        "只输出 JSON，不解释。\n\n"
        "schema：" + NARRATOR_SCHEMA + "\n"
        "输出对象：{\"schema_version\": \"" + NARRATOR_SCHEMA + "\", \"opening\": string}"
    )
    user_lines = ["当前时间：{}".format(facts.get("now", facts.get("reference_datetime", "")))]
    user_lines.append("以下是当前计划事实：")
    user_lines.append(_facts_text(facts, change_facts))
    user_lines.append("只输出一个符合 schema 的 JSON 对象。")
    return system, "\n".join(user_lines)


def parse_narrator_copy(text: Optional[str]) -> Optional[NarratorCopy]:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        payload = extract_json_object(text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    opening = payload.get("opening")
    if not isinstance(opening, str):
        return None
    opening = opening.strip()
    if not opening:
        return None
    if len(opening) > MAX_FIELD_LENGTH:
        return None
    return NarratorCopy(opening=opening, generated=True)


def _location_repair_prompt(facts, schema, output_desc) -> Tuple[str, str]:
    """地点事实修复 prompt：只准原样使用受信地点名称重写文案。"""
    whitelist = _location_whitelist_text(facts) or "（无）"
    system = (
        "你是 CampusFlow 文案修复助手。你之前的文案引用了不受信的地点名称，必须重写。\n"
        "地点名称只能原样使用以下受信名称：{}。\n"
        "禁止自行拼接、扩写或按常识改名（例如不得把“31教”写成“齐园31教学楼”等地图中不存在的名称），"
        "不得新增/修改任务、时间、路线、地点、耗时。\n"
        "只输出符合 {} 的 JSON 对象。"
    ).format(whitelist, schema)
    user = "输出对象：{}。只输出 JSON。".format(output_desc)
    return system, user


def _narrator_location_repair_prompt(facts) -> Tuple[str, str]:
    return _location_repair_prompt(
        facts,
        NARRATOR_SCHEMA,
        "{\"schema_version\": \"" + NARRATOR_SCHEMA + "\", \"opening\": string}",
    )


def _warm_location_repair_prompt(facts) -> Tuple[str, str]:
    return _location_repair_prompt(
        facts,
        WARM_SCHEMA,
        "{\"schema_version\": \"" + WARM_SCHEMA + "\", "
        "\"change_summary\": string|null, \"closing\": string}",
    )


def _narrator_repair_prompt() -> Tuple[str, str]:
    system = (
        "你是格式修复助手。只修复 JSON 格式/结构问题，不重新解释计划，"
        "不添加新事实，只输出符合 " + NARRATOR_SCHEMA + " 的 JSON 对象。"
    )
    user = (
        "输出对象：{\"schema_version\": \"" + NARRATOR_SCHEMA + "\", "
        "\"opening\": string}\n只输出 JSON。"
    )
    return system, user



# ---------------------------------------------------------------------------
# C. Warm Companion Agent
# ---------------------------------------------------------------------------


def build_warm_prompt(
    facts, context_type=CONTEXT_INITIAL, change_facts=None
) -> Tuple[str, str]:
    system = (
        "你是 CampusFlow 的 Warm Companion：根据已确定的计划事实生成温暖、自然、有生活感的收尾，"
        "像一个陪着大学生过校园生活的助手。"
        "只描述和回应计划，不修改计划，不新增任务，不编造用户状态、天气、健康、情绪。\n\n"
        "change_summary：仅 feedback_update 且本轮确有变化时生成，必须完全来自“本轮已确认变化”"
        "事实；一行、简洁、偏事实（例如“上课改到15:30，通勤改为步行”）；其他场景必须是 null。\n"
        "closing：2~4句话，约50~120个中文字符，比 opening 更舒展、更有温度。"
        "可以自然表达：任务没全部做完也没关系/之后有合适的时间再继续/到点记得收拾/"
        "通勤不用赶/路上注意安全/记得喝水吃饭/中间有空就稍微休息/安排满就慢一点/"
        "安排宽松就不用赶/祝上课顺利/祝实验顺利/祝一天过得顺利开心。"
        "根据真实计划调整语气：临近固定安排（约30分钟内）可以说“快到了，先收拾好东西就出发吧”；"
        "计划满可以说“不用为了赶计划把通勤弄得急匆匆”；计划宽松可以说“不用把每一分钟都塞满”；"
        "有高峰骑行可以说“这段时间骑车人会比较多，路上别赶”；"
        "有任务今天先放下可以说“先放一放也没关系，计划是帮你生活的，不是追着你跑的”。\n"
        "禁止：过度鸡汤、银行客服语气、百日誓师语气、\"宝宝/宝贝/亲亲\"、\"你一定可以！\"、"
        "\"今天也要闪闪发光！\"、\"未来可期！\"、心理咨询腔。\n"
        "closing 不要与 opening 重复表达同一件事；不要机械复制固定句式。\n\n"
        "事实红线：只有“晚间可用时间：有”才允许说晚上还能继续；"
        "严格按“当前正在进行”和“下一段安排”说话：当前段不是收拾或移动时，不得把未来出发、去某地或上课说成“现在”。收拾、骑行和上课引用时间时必须分别对应 transition_start、movement_start、commitment_start。"
        "只有下一固定安排确实很近（约30分钟内）才允许说“快到了/快迟到”；"
        "不要机械复制固定句式，不要每次机械说“今日行程繁忙”；"
        "禁止编造迟到、压力大、你累了、天气、健康状态。\n"
        "所有地点名称只能原样使用“受信地点名称”中的名称，禁止自行拼接、扩写或按常识改名（例如不得把“31教”写成“齐园31教学楼”等地图中不存在的名称）。\n"
        "只输出 JSON，不解释。\n\n"
        "schema：" + WARM_SCHEMA + "\n"
        "输出对象：{\"schema_version\": \"" + WARM_SCHEMA + "\", "
        "\"change_summary\": string|null, \"closing\": string}"
    )
    user_lines = ["当前时间：{}".format(facts.get("now", facts.get("reference_datetime", "")))]
    user_lines.append("以下是当前计划事实：")
    user_lines.append(_facts_text(facts, change_facts))
    if context_type == CONTEXT_FEEDBACK and change_facts is not None:
        user_lines.append("文案场景：feedback_update（本轮确有变化）。")
    else:
        user_lines.append("文案场景：{}（本轮无用户变化或非反馈）。".format(context_type))
    user_lines.append("只输出一个符合 schema 的 JSON 对象。")
    return system, "\n".join(user_lines)


def parse_warm_copy(text: Optional[str]) -> Optional[WarmCopy]:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        payload = extract_json_object(text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    closing = payload.get("closing")
    if not isinstance(closing, str) or not closing.strip():
        return None
    closing = closing.strip()
    if len(closing) > MAX_FIELD_LENGTH:
        return None
    change_summary = payload.get("change_summary")
    if change_summary is not None:
        if not isinstance(change_summary, str):
            return None
        change_summary = change_summary.strip()
        if len(change_summary) > MAX_FIELD_LENGTH:
            return None
        if not change_summary:
            change_summary = None
    return WarmCopy(change_summary=change_summary, closing=closing, generated=True)


def _warm_repair_prompt() -> Tuple[str, str]:
    system = (
        "你是格式修复助手。只修复 JSON 格式/结构问题，不重新解释计划，"
        "只输出符合 " + WARM_SCHEMA + " 的 JSON 对象。"
    )
    user = (
        "输出对象：{\"schema_version\": \"" + WARM_SCHEMA + "\", "
        "\"change_summary\": string|null, \"closing\": string}\n只输出 JSON。"
    )
    return system, user



# ---------------------------------------------------------------------------
# D. Final Copy Reviewer Agent
# ---------------------------------------------------------------------------


def build_copy_review_prompt(fields: Dict[str, object], facts) -> Tuple[str, str]:
    system = (
        "你是 CampusFlow 的 Final Copy Reviewer：只审查/润色自然语言文案，"
        "绝不修改任何结构化计划事实（时间、地点、路线、耗时、任务）。\n\n"
        "只审核以下字段：opening、change_summary、gap_tip、closing。\n"
        "检查：1) 是否自然；2) 是否重复（尤其 opening 与 closing、change_summary 与 opening）；"
        "3) 是否像银行客服/景区客服/鸡汤；4) opening 是否太长（应保持 1~2 句短而活泼）；"
        "5) closing 是否太冷/太短——closing 允许 2~4 句、约50~120字，"
        "不要因为“精简”把它强行压成一句；6) 是否出现内部工程词；"
        "7) 是否声称不存在的时间/地点/任务；8) 语气是否过于冷淡或过于客服。\n"
        "可以对文案做轻量精简和润色。approved 表示原文案可直接使用；"
        "revised 必须包含完整字段（值为 null 的字段保留 null），仅当确实需要修改时提供。\n"
        "reason 只用于内部日志，不展示给用户。\n"
        "不得改写任何受信实体文本：地点名、任务名、固定安排名、已确定时间。例如输入“步行前往31教”只能调整周围语气，不得改成“步行前往齐园31教学楼”等地图中不存在的名称。\n"
        "引用明确时间/交通方式/移动耗时必须与真实计划事实一致。\n"
        "只输出 JSON，不解释。\n\n"
        "schema：" + COPY_REVIEW_SCHEMA + "\n"
        "输出对象：{\"schema_version\": \"" + COPY_REVIEW_SCHEMA + "\", "
        "\"approved\": bool, \"revised\": {opening, change_summary, gap_tip, closing}|null, "
        "\"reason\": string|null}"
    )
    user_lines = ["以下是需要审查的文案字段："]
    for key, value in fields.items():
        user_lines.append("{}：{}".format(key, value if value is not None else "null"))
    user_lines.append("")
    user_lines.append("以下是真实计划事实（用于核对，不得新增/修改）：")
    user_lines.append(_facts_text(facts))
    user_lines.append("只输出一个符合 schema 的 JSON 对象。")
    return system, "\n".join(user_lines)


def parse_copy_review(text: Optional[str]) -> Optional[CopyReviewResult]:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        payload = extract_json_object(text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    approved = payload.get("approved")
    if not isinstance(approved, bool):
        return None
    revised = payload.get("revised")
    if revised is not None and not isinstance(revised, dict):
        return None
    reason = payload.get("reason")
    if reason is not None and not isinstance(reason, str):
        return None
    return CopyReviewResult(
        approved=approved, revised=revised, reason=reason, generated=True
    )


def _copy_review_repair_prompt() -> Tuple[str, str]:
    system = (
        "你是格式修复助手。只修复 JSON 格式/结构问题，不修改文案内容，"
        "只输出符合 " + COPY_REVIEW_SCHEMA + " 的 JSON 对象。"
    )
    user = (
        "输出对象：{\"schema_version\": \"" + COPY_REVIEW_SCHEMA + "\", "
        "\"approved\": bool, \"revised\": object|null, \"reason\": string|null}\n只输出 JSON。"
    )
    return system, user


# ---------------------------------------------------------------------------
# E. Plan Critic Agent
# ---------------------------------------------------------------------------


def build_critic_prompt(facts) -> Tuple[str, str]:
    system = (
        "你是 CampusFlow 的 Plan Critic：独立审查最终确定性计划的质量，只审查、不重新规划。\n\n"
        "重点检查：\n"
        "- 是否有明显可利用碎片被浪费；\n"
        "- 是否有大任务因错误“不可拆”判断被完全放弃；\n"
        "- 是否过度紧张通勤或任务切换很别扭；\n"
        "- 是否明明有短任务却留空；\n"
        "- 是否安排顺序明显不符合用户当前需求；\n"
        "- 是否某个任务被安排得过于碎片化；\n"
        "- 是否低优先任务挤压高优先固定安排；\n"
        "- 是否存在“不像大学生日常会执行”的结果。\n\n"
        "不得质疑程序硬事实：地图距离、地点节点、路线、固定安排时间、任务 lifecycle。\n"
        "issues：每个 issue 包含 type、severity（low/medium/high）、message（人话）。\n"
        "revision_needed：只有存在 high severity 或确实需要调整时才 true；"
        "轻微瑕疵用 low/medium 记录但不要求修订。\n"
        "只输出 JSON，不解释。\n\n"
        "schema：" + PLAN_CRITIC_SCHEMA + "\n"
        "输出对象：{\"schema_version\": \"" + PLAN_CRITIC_SCHEMA + "\", "
        "\"approved\": bool, \"issues\": [{\"type\": string, \"severity\": "
        "\"low|medium|high\", \"message\": string}], \"revision_needed\": bool}"
    )
    user = "以下是当前真实计划事实：\n" + _facts_text(facts) + "\n只输出一个符合 schema 的 JSON 对象。"
    return system, user


def parse_plan_critic(text: Optional[str]) -> Optional[PlanCriticResult]:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        payload = extract_json_object(text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    approved = payload.get("approved")
    revision_needed = payload.get("revision_needed")
    if not isinstance(approved, bool) or not isinstance(revision_needed, bool):
        return None
    raw_issues = payload.get("issues")
    if not isinstance(raw_issues, list):
        return None
    issues = []
    for item in raw_issues:
        if not isinstance(item, dict):
            return None
        issue_type = item.get("type")
        severity = item.get("severity")
        message = item.get("message")
        if (
            not isinstance(issue_type, str)
            or not issue_type.strip()
            or severity not in ("low", "medium", "high")
            or not isinstance(message, str)
            or not message.strip()
        ):
            return None
        issues.append(
            CriticIssue(issue_type=issue_type.strip(), severity=severity, message=message.strip())
        )
    if revision_needed and not issues:
        return None
    return PlanCriticResult(
        approved=approved, issues=tuple(issues), revision_needed=revision_needed, generated=True
    )


def _critic_repair_prompt() -> Tuple[str, str]:
    system = (
        "你是格式修复助手。只修复 JSON 格式/结构问题，不重新审查计划，"
        "只输出符合 " + PLAN_CRITIC_SCHEMA + " 的 JSON 对象。"
    )
    user = (
        "输出对象：{\"schema_version\": \"" + PLAN_CRITIC_SCHEMA + "\", "
        "\"approved\": bool, \"issues\": [{\"type\": string, \"severity\": "
        "\"low|medium|high\", \"message\": string}], \"revision_needed\": bool}\n只输出 JSON。"
    )
    return system, user



# ---------------------------------------------------------------------------
# F. Plan Improver Agent
# ---------------------------------------------------------------------------


def build_improver_prompt(critic: PlanCriticResult, facts) -> Tuple[str, str]:
    issue_lines = []
    for issue in critic.issues:
        issue_lines.append(
            "- [{}] {}：{}".format(issue.severity, issue.issue_type, issue.message)
        )
    system = (
        "你是 CampusFlow 的 Plan Improver：仅根据 Plan Critic 的问题对确定性分配提出修订建议，"
        "不重新规划、不修改程序硬约束。\n\n"
        "只能建议：\n"
        "- task_order_titles：任务顺序（使用任务真实标题，按建议顺序排列，可只列需要调整的任务）；\n"
        "- include_low_attention：是否允许使用低注意力窗口；\n"
        "不能：改地图、改路线距离、改固定时间事实、改用户明确的任务 lifecycle。\n"
        "只输出 JSON，不解释。\n\n"
        "schema：" + PLAN_IMPROVER_SCHEMA + "\n"
        "输出对象：{\"schema_version\": \"" + PLAN_IMPROVER_SCHEMA + "\", "
        "\"task_order_titles\": [string], \"include_low_attention\": bool, "
        "\"reason\": string|null}"
    )
    user = (
        "Plan Critic 的问题：\n"
        + "\n".join(issue_lines)
        + "\n\n当前真实计划事实：\n"
        + _facts_text(facts)
        + "\n只输出一个符合 schema 的 JSON 对象。"
    )
    return system, user


def parse_plan_improver(text: Optional[str]) -> Optional[ImproverSuggestion]:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        payload = extract_json_object(text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    titles = payload.get("task_order_titles")
    if not isinstance(titles, list):
        return None
    clean_titles = []
    for title in titles:
        if not isinstance(title, str) or not title.strip():
            return None
        clean_titles.append(title.strip())
    include_low = payload.get("include_low_attention")
    if not isinstance(include_low, bool):
        return None
    reason = payload.get("reason")
    if reason is not None and not isinstance(reason, str):
        return None
    return ImproverSuggestion(
        task_order_titles=tuple(clean_titles),
        include_low_attention=include_low,
        reason=reason,
        generated=True,
    )


def _improver_repair_prompt() -> Tuple[str, str]:
    system = (
        "你是格式修复助手。只修复 JSON 格式/结构问题，不重新提出建议，"
        "只输出符合 " + PLAN_IMPROVER_SCHEMA + " 的 JSON 对象。"
    )
    user = (
        "输出对象：{\"schema_version\": \"" + PLAN_IMPROVER_SCHEMA + "\", "
        "\"task_order_titles\": [string], \"include_low_attention\": bool, "
        "\"reason\": string|null}\n只输出 JSON。"
    )
    return system, user


# ---------------------------------------------------------------------------
# G. Lifestyle Reviewer Agent
# ---------------------------------------------------------------------------


def build_lifestyle_prompt(facts) -> Tuple[str, str]:
    system = (
        "你是 CampusFlow 的 Lifestyle & Gap Reviewer：以“是否在辅助校园生活”的视角轻量描述"
        "计划特征，不做健康/心理/压力诊断，不强行修改计划。\n\n"
        "notes：可识别“连续学习时间较长”“地点切换较多”“午间存在较长空档”"
        "“多次高峰移动”“大量碎片时间”等；没有有价值信息时为空数组。\n"
        "user_facing_hint：一句话的人话提示（例如“今天地点切换较多，通勤记得留余量”），"
        "无价值信息时为 null。\n"
        "gap_tip：只有 facts 明确存在“空闲时段”时才生成，且只用一行轻提示。"
        "空档不超过30分钟才可称“小空档”；31~90分钟称“一段空闲时间”；超过90分钟时返回 null，"
        "不要输出几百分钟这类机器式数字。小空档可说“这几分钟不用硬塞任务，喝口水、歇一下也很好～”。"
        "如果窗口已安排给任务就不算空档；"
        "禁止凭空新增买咖啡/跑步/复习英语/给家里打电话等用户没有的任务；"
        "无真实空闲时段时必须是 null。\n"
        "地点名称只能原样使用“受信地点名称”中的名称，禁止自行拼接、扩写或按常识改名。\n"
        "引用明确时间/交通方式/移动耗时必须与真实计划事实一致。\n"
        "只输出 JSON，不解释。\n\n"
        "schema：" + LIFESTYLE_SCHEMA + "\n"
        "输出对象：{\"schema_version\": \"" + LIFESTYLE_SCHEMA + "\", "
        "\"notes\": [string], \"user_facing_hint\": string|null, \"gap_tip\": string|null}"
    )
    user = "以下是当前真实计划事实：\n" + _facts_text(facts) + "\n只输出一个符合 schema 的 JSON 对象。"
    return system, user


def parse_lifestyle_review(text: Optional[str]) -> Optional[LifestyleReview]:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        payload = extract_json_object(text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    notes = payload.get("notes")
    if not isinstance(notes, list):
        return None
    clean_notes = []
    for note in notes:
        if not isinstance(note, str) or not note.strip():
            return None
        clean_notes.append(note.strip())
    hint = payload.get("user_facing_hint")
    if hint is not None:
        if not isinstance(hint, str):
            return None
        hint = hint.strip()
        if not hint:
            hint = None
    gap_tip = payload.get("gap_tip")
    if gap_tip is not None:
        if not isinstance(gap_tip, str):
            return None
        gap_tip = gap_tip.strip()
        if not gap_tip:
            gap_tip = None
        elif len(gap_tip) > MAX_FIELD_LENGTH:
            return None
    return LifestyleReview(
        notes=tuple(clean_notes), user_facing_hint=hint, gap_tip=gap_tip, generated=True
    )


def _lifestyle_repair_prompt() -> Tuple[str, str]:
    system = (
        "你是格式修复助手。只修复 JSON 格式/结构问题，不重新审查，"
        "只输出符合 " + LIFESTYLE_SCHEMA + " 的 JSON 对象。"
    )
    user = (
        "输出对象：{\"schema_version\": \"" + LIFESTYLE_SCHEMA + "\", "
        "\"notes\": [string], \"user_facing_hint\": string|null, \"gap_tip\": string|null}"
        "\n只输出 JSON。"
    )
    return system, user


# ---------------------------------------------------------------------------
# 通用 runner（1 次调用 + 最多 1 次 repair）
# ---------------------------------------------------------------------------


def _call_once(
    caller: AgentCaller, budget: Optional[_BudgetCaller], system: str, user: str
) -> Optional[str]:
    try:
        if budget is None:
            return caller(system, user)
        return budget(system, user)
    except Exception:
        return None


def _repair_once(
    caller: AgentCaller,
    budget: Optional[_BudgetCaller],
    repair_prompt: Tuple[str, str],
    parse_fn,
):
    repair_system, repair_user = repair_prompt
    return parse_fn(_call_once(caller, budget, repair_system, repair_user))


def run_plan_critic(
    facts, caller: AgentCaller, repair_caller=None, budget=None
) -> PlanCriticResult:
    repair = repair_caller if repair_caller is not None else caller
    system, user = build_critic_prompt(facts)
    parsed = parse_plan_critic(_call_once(caller, budget, system, user))
    if parsed is not None:
        return parsed
    repaired = _repair_once(repair, budget, _critic_repair_prompt(), parse_plan_critic)
    if repaired is not None:
        return repaired
    return PlanCriticResult(approved=True, issues=(), revision_needed=False, generated=False)


def run_plan_improver(
    critic: PlanCriticResult, facts, caller: AgentCaller, repair_caller=None, budget=None
) -> Optional[ImproverSuggestion]:
    repair = repair_caller if repair_caller is not None else caller
    system, user = build_improver_prompt(critic, facts)
    parsed = parse_plan_improver(_call_once(caller, budget, system, user))
    if parsed is not None:
        return parsed
    return _repair_once(repair, budget, _improver_repair_prompt(), parse_plan_improver)


def run_lifestyle_review(
    facts, caller: AgentCaller, repair_caller=None, budget=None
) -> LifestyleReview:
    repair = repair_caller if repair_caller is not None else caller
    system, user = build_lifestyle_prompt(facts)
    parsed = parse_lifestyle_review(_call_once(caller, budget, system, user))
    if parsed is not None:
        return parsed
    repaired = _repair_once(repair, budget, _lifestyle_repair_prompt(), parse_lifestyle_review)
    if repaired is not None:
        return repaired
    return LifestyleReview(notes=(), user_facing_hint=None, gap_tip=None, generated=False)


def run_narrator(
    facts, caller: AgentCaller, repair_caller=None, budget=None, context_type=CONTEXT_INITIAL,
    change_facts=None,
) -> NarratorCopy:
    repair = repair_caller if repair_caller is not None else caller
    system, user = build_narrator_prompt(facts, context_type=context_type, change_facts=change_facts)
    parsed = parse_narrator_copy(_call_once(caller, budget, system, user))
    if parsed is not None:
        if validate_expression_facts(parsed.opening, facts):
            return parsed
        logger.debug("[CampusFlow][expression][Narrator] validation_failed -> repair")
        repaired = _repair_once(
            repair, budget, _narrator_location_repair_prompt(facts), parse_narrator_copy
        )
        if repaired is not None and validate_expression_facts(repaired.opening, facts):
            return repaired
        logger.warning("[CampusFlow][expression][Narrator] repair_failed -> opening fallback")
        fallback_opening, _ = companion_fallbacks(context_type)
        return NarratorCopy(opening=fallback_opening, generated=False)
    logger.debug("[CampusFlow][expression][Narrator] parse_failed -> repair")
    repaired = _repair_once(repair, budget, _narrator_repair_prompt(), parse_narrator_copy)
    if repaired is not None and validate_expression_facts(repaired.opening, facts):
        return repaired
    logger.warning("[CampusFlow][expression][Narrator] repair_failed -> opening fallback")
    fallback_opening, _ = companion_fallbacks(context_type)
    return NarratorCopy(opening=fallback_opening, generated=False)


def run_warm_companion(
    facts, caller: AgentCaller, repair_caller=None, budget=None, context_type=CONTEXT_INITIAL,
    change_facts=None,
) -> WarmCopy:
    repair = repair_caller if repair_caller is not None else caller
    system, user = build_warm_prompt(facts, context_type=context_type, change_facts=change_facts)
    parsed = parse_warm_copy(_call_once(caller, budget, system, user))
    if parsed is not None:
        if _warm_facts_ok(parsed, facts):
            return parsed
        logger.debug("[CampusFlow][expression][WarmCompanion] validation_failed -> repair")
        repaired = _repair_once(
            repair, budget, _warm_location_repair_prompt(facts), parse_warm_copy
        )
        if repaired is not None and _warm_facts_ok(repaired, facts):
            return repaired
        logger.warning("[CampusFlow][expression][WarmCompanion] repair_failed -> field fallback")
        # repair 失败或仍非法：保留合法字段，非法字段用安全 fallback
        _, fallback_closing = companion_fallbacks(context_type)
        return WarmCopy(
            change_summary=(
                parsed.change_summary
                if validate_expression_facts(parsed.change_summary, facts)
                else None
            ),
            closing=(
                parsed.closing
                if validate_expression_facts(parsed.closing, facts)
                else fallback_closing
            ),
            generated=parsed.generated,
        )
    repaired = _repair_once(repair, budget, _warm_repair_prompt(), parse_warm_copy)
    if repaired is not None and _warm_facts_ok(repaired, facts):
        return repaired
    _, fallback_closing = companion_fallbacks(context_type)
    return WarmCopy(change_summary=None, closing=fallback_closing, generated=False)


def run_copy_review(
    fields: Dict[str, object],
    facts,
    caller: AgentCaller,
    repair_caller=None,
    budget=None,
) -> Optional[CopyReviewResult]:
    repair = repair_caller if repair_caller is not None else caller
    system, user = build_copy_review_prompt(fields, facts)
    parsed = parse_copy_review(_call_once(caller, budget, system, user))
    if parsed is not None:
        return parsed
    return _repair_once(repair, budget, _copy_review_repair_prompt(), parse_copy_review)


# ---------------------------------------------------------------------------
# 表达层 bundle：Narrator -> Warm -> Final Copy Review（gap_tip 由 Lifestyle & Gap Reviewer 提供）
# ---------------------------------------------------------------------------

_COPY_FIELD_NAMES = (
    "opening",
    "change_summary",
    "gap_tip",
    "closing",
)

_INVALID = object()


def _clean_revised_field(value, max_len=MAX_FIELD_LENGTH):
    if value is None:
        return None
    if not isinstance(value, str):
        return _INVALID
    value = value.strip()
    if not value:
        return None
    if len(value) > max_len:
        return _INVALID
    return value


def _apply_revised_fields(copy: CompanionCopy, revised: Optional[Dict[str, object]]):
    if not isinstance(revised, dict):
        return copy
    kwargs = {}
    for name in _COPY_FIELD_NAMES:
        value = revised.get(name)
        cleaned = _clean_revised_field(value, max_len=MAX_FIELD_LENGTH)
        if cleaned is _INVALID:
            return copy
        kwargs[name] = cleaned
    try:
        return dataclasses.replace(
            copy,
            opening=kwargs["opening"] or copy.opening,
            change_summary=(
                kwargs["change_summary"]
                if kwargs["change_summary"] is not None
                else copy.change_summary
            ),
            gap_tip=kwargs["gap_tip"] if kwargs["gap_tip"] is not None else copy.gap_tip,
            closing=kwargs["closing"] or copy.closing,
            copy_reviewed=True,
        )
    except (TypeError, ValueError):
        return copy



def _final_facts_guard(copy, facts, fallback_opening, fallback_closing) -> CompanionCopy:
    """最终防线：任何仍含不受信地点/时间/方式/耗时的文案字段整段替换为安全 fallback。"""
    opening = copy.opening
    closing = copy.closing
    change_summary = copy.change_summary
    gap_tip = copy.gap_tip
    if not validate_expression_facts(opening, facts):
        logger.warning("[CampusFlow][expression] final_guard opening -> fallback")
        opening = fallback_opening
    if not validate_expression_facts(closing, facts):
        logger.warning("[CampusFlow][expression] final_guard closing -> fallback")
        closing = fallback_closing
    if not validate_expression_facts(change_summary, facts) or not _change_summary_matches_diff(
        change_summary, facts.get("change_facts")
    ):
        logger.warning("[CampusFlow][expression] final_guard change_summary -> None")
        change_summary = None
    if not validate_expression_facts(gap_tip, facts):
        logger.warning("[CampusFlow][expression] final_guard gap_tip -> None")
        gap_tip = None
    if (opening, closing, change_summary, gap_tip) == (
        copy.opening, copy.closing, copy.change_summary, copy.gap_tip
    ):
        return copy
    return dataclasses.replace(
        copy,
        opening=opening,
        closing=closing,
        change_summary=change_summary,
        gap_tip=gap_tip,
    )


def _change_summary_matches_diff(summary, change_facts) -> bool:
    """Keep wording such as “改到15:00” tied to an actual start-time diff."""
    if summary is None or not isinstance(change_facts, dict):
        return True
    commitment_changes = tuple(change_facts.get("commitment_changes", ()) or ())
    if not commitment_changes:
        return True
    only_end_supplements = all(
        "已补充结束时间" in line for line in commitment_changes
    )
    if only_end_supplements and ("改到" in summary or "改为" in summary):
        return False
    return True


def _event_time_facts(facts):
    """Keep a time attached to the event it starts, rather than treating all
    plan times as interchangeable.  This deliberately remains a small guard
    for the explicit transition/movement/commitment phrases used in copy."""
    events = []
    for movement in facts.get("movement_facts") or ():
        transition_start = movement.get("transition_start")
        if transition_start:
            events.append(("transition", _parse_hm(transition_start)))
        movement_start = movement.get("movement_start") or movement.get("start")
        if movement_start:
            events.append(("movement", _parse_hm(movement_start)))
    for commitment in facts.get("commitment_facts") or ():
        prep_start = commitment.get("class_prep_start")
        if prep_start:
            events.append(("class_prep", _parse_hm(prep_start)))
        building_arrival = commitment.get("building_arrival") or prep_start
        if building_arrival:
            events.append(("building_arrival", _parse_hm(building_arrival)))
        # Older fact fixtures only carried one prep timestamp.  Treat it as a
        # compatibility fallback; newly produced facts always carry T-5.
        classroom_arrival = commitment.get("classroom_arrival") or prep_start
        if classroom_arrival:
            events.append(("classroom_arrival", _parse_hm(classroom_arrival)))
        starts_at = commitment.get("starts_at")
        if starts_at:
            events.append(("commitment", _parse_hm(starts_at)))
    next_commitment = facts.get("next_commitment")
    if isinstance(next_commitment, str):
        match = re.match(r"\s*(\d{1,2}:\d{2})", next_commitment)
        if match:
            events.append(("commitment", _parse_hm(match.group(1))))
    return tuple((kind, value) for kind, value in events if value is not None)


_EVENT_CLAIM_PATTERNS = (
    ("transition", re.compile(r"(收拾|整理).{0,8}(东西|书包)?")),
    ("movement", re.compile(r"(骑行|骑车|步行|走路|出发|前往)")),
    ("building_arrival", re.compile(r"到.{0,6}教学楼")),
    ("class_prep", re.compile(r"(进楼|找教室|到楼后)")),
    ("classroom_arrival", re.compile(r"(到教室|教室.{0,8}(签到|准备)|签到.{0,8}教室)")),
    # A class end time in a range (15:20-16:50上课) is valid copy.  Bind a
    # commitment only when copy explicitly claims its *start*.
    ("commitment", re.compile(r"(开始|开课|起).{0,8}(上课|课程|实验课)|(上课|课程|实验课).{0,8}(开始|开课|起)")),
)


def _validate_event_time_binding(text, facts) -> bool:
    event_times = _event_time_facts(facts)
    if not event_times:
        return True
    trusted = _trusted_time_set(facts)
    for match in _TIME_CLAIM_COLON_RE.finditer(text):
        claimed = _normalize_claim_time(int(match.group(2)), int(match.group(3)), match.group(1), trusted)
        nearby = _event_nearby_text(text, match)
        for kind, pattern in _EVENT_CLAIM_PATTERNS:
            # In “15:20上课，步行前往31教”, the time qualifies the class,
            # not the later travel verb.
            if kind == "movement" and re.search(r"(上课|课程|实验课)", nearby[: nearby.find("步") if "步" in nearby else len(nearby)]):
                continue
            if pattern.search(nearby) and (kind, claimed) not in event_times:
                return False
    for match in _TIME_CLAIM_CN_RE.finditer(text):
        minute = int(match.group(3)) if match.group(3) else 0
        claimed = _normalize_claim_time(int(match.group(2)), minute, match.group(1), trusted)
        nearby = _event_nearby_text(text, match)
        for kind, pattern in _EVENT_CLAIM_PATTERNS:
            if kind == "movement" and re.search(r"(上课|课程|实验课)", nearby[: nearby.find("步") if "步" in nearby else len(nearby)]):
                continue
            if pattern.search(nearby) and (kind, claimed) not in event_times:
                return False
    return True


def _event_nearby_text(text, match):
    """Do not bind a following timestamp's event phrase to this timestamp."""
    # Event wording belongs to the timestamp that precedes it.  Looking back
    # would make a later “15:00上课” inherit “14:55到教室”的语义。
    start = match.start()
    end = min(len(text), match.end() + 12)
    following = _TIME_CLAIM_COLON_RE.search(text, match.end())
    if following is not None:
        end = min(end, following.start())
    return text[start:end]


_NOW_WORD_RE = re.compile(r"(现在|这就|立刻|马上)")
_FUTURE_DEPARTURE_RE = re.compile(r"(准备)?(出发|去.{0,12}(教|上课)|前往|上课)")


def _validate_current_future_semantics(text, facts) -> bool:
    """A present-tense departure is only true while the current segment is a
    transition or movement.  We do not attempt broad NLP here: this catches
    the short imperative forms the narrator is instructed to avoid."""
    current = facts.get("current_segment") or {}
    if current.get("kind") in ("transition", "movement"):
        return True
    for now_match in _NOW_WORD_RE.finditer(text):
        nearby = text[now_match.start(): now_match.end() + 18]
        if _FUTURE_DEPARTURE_RE.search(nearby):
            return False
    return True


def generate_expression_copy(
    state,
    result: P2AgenticDayResult,
    movement_blocks=(),
    context_type=CONTEXT_INITIAL,
    change_facts=None,
    current_location=None,
    assumed_current_location=None,
    map_data=None,
    caller: AgentCaller = None,
    repair_caller: AgentCaller = None,
    allow_plan_revision=True,
) -> Tuple[CompanionCopy, Optional[P2AgenticDayResult]]:
    """完整表达层入口：Critic ->（可选）Improver 重分配 -> Lifestyle -> 文案 bundle。

    返回 (CompanionCopy, revised_result)；revised_result 仅在 Critic 要求修订、
    ``allow_plan_revision`` 为 True 且 Improver 建议被安全应用时非 None。
    P4 execution plan 已完成 movement/bounds formal allocation 后必须传 False：
    表达 Agent 可以评论和润色，但不能成为隐藏的第二个 allocator。
    任何失败都转安全 fallback，不影响主计划。
    """
    if caller is None:
        raise TypeError("caller must be callable")
    repair = repair_caller if repair_caller is not None else caller
    facts = extract_expression_facts(
        state,
        result.allocation_plan,
        tuple(movement_blocks or ()),
        current_location=current_location,
        assumed_current_location=assumed_current_location,
        change_facts=change_facts,
        map_data=map_data,
    )
    budget = _BudgetCaller(caller, MAX_EXPRESSION_CALLS_PER_TURN)
    critic = run_plan_critic(facts, budget, budget)
    revised = None
    improved = False
    if critic.revision_needed and allow_plan_revision:
        improver = run_plan_improver(critic, facts, budget, budget)
        if improver is not None and improver.applicable(state):
            revised = apply_improver_result(result, improver)
            if revised is not None:
                improved = True
                result = revised
                facts = extract_expression_facts(
                    state,
                    result.allocation_plan,
                    tuple(movement_blocks or ()),
                    current_location=current_location,
                    assumed_current_location=assumed_current_location,
                    change_facts=change_facts,
                    map_data=map_data,
                )
    lifestyle = run_lifestyle_review(facts, budget, budget)
    copy = generate_expression_bundle(
        facts,
        budget,
        repair,
        context_type=context_type,
        change_facts=change_facts,
        gap_tip=lifestyle.gap_tip,
        budget=budget,
    )
    copy = dataclasses.replace(
        copy,
        critic_approved=(critic.approved and not critic.revision_needed),
        improved=improved,
        lifestyle_hint=lifestyle.user_facing_hint,
    )
    return copy, revised


def generate_expression_bundle(
    facts,
    caller: AgentCaller,
    repair_caller=None,
    context_type=CONTEXT_INITIAL,
    change_facts=None,
    gap_tip=None,
    budget=None,
) -> CompanionCopy:
    """表达层文案包：Narrator（opening）+ Warm Companion（change_summary/closing）。

    gap_tip 由 Lifestyle & Gap Reviewer 提供；程序事实红线：无真实空闲时段时强制 null。
    任何失败转安全 fallback。已删除 plan_title / mainline / reasoning_note / rhythm_note。
    """
    if context_type not in (CONTEXT_INITIAL, CONTEXT_FEEDBACK, CONTEXT_REFRESH):
        raise ValueError("context_type must be a valid context")
    repair = repair_caller if repair_caller is not None else caller
    narrator = run_narrator(
        facts, caller, repair, budget=budget, context_type=context_type, change_facts=change_facts
    )
    warm = run_warm_companion(
        facts, caller, repair, budget=budget, context_type=context_type, change_facts=change_facts
    )

    # 程序决定空档语义，LLM 只负责轻量表达：长时间自由段不应被
    # 渲染成“295 分钟小空档”这类机械且失真的提示。
    gap_tip_kind = None
    gap_minutes = min(
        (int(gap.get("minutes", 0) or 0) for gap in (facts.get("idle_gaps") or ())
         if int(gap.get("minutes", 0) or 0) > 0),
        default=0,
    )


    if gap_minutes <= 0:
        gap_tip = None
    elif gap_minutes <= 30:
        gap_tip_kind = "short"
    elif gap_minutes <= 90:
        gap_tip_kind = "free"
    elif isinstance(gap_tip, str) and len(gap_tip) > MAX_FIELD_LENGTH:
        gap_tip = None
    else:
        gap_tip = None
    if isinstance(gap_tip, str) and gap_tip_kind == "short":
        if any(int(value) > 30 for value in re.findall(r"(\d{1,4})\s*分钟", gap_tip)):
            gap_tip = None

    change_summary = warm.change_summary
    if context_type != CONTEXT_FEEDBACK:
        change_summary = None
    elif change_facts is None or not change_facts.get("changed"):
        change_summary = None
    elif not _change_summary_matches_diff(change_summary, change_facts):
        change_summary = None

    fallback_opening, fallback_closing = companion_fallbacks(context_type)
    copy = CompanionCopy(
        opening=narrator.opening or fallback_opening,
        closing=warm.closing or fallback_closing,
        generated=(narrator.generated or warm.generated),
        context_type=context_type,
        change_summary=change_summary,
        gap_tip=gap_tip,
        gap_tip_kind=gap_tip_kind,
    )

    # Final Copy Reviewer：只润色文案；失败/不可用时保留现有文案
    fields = {
        "opening": copy.opening,
        "change_summary": copy.change_summary,
        "gap_tip": copy.gap_tip,
        "closing": copy.closing,
    }
    review = run_copy_review(fields, facts, caller, repair, budget=budget)
    final_copy = copy
    if review is not None and review.approved:
        final_copy = dataclasses.replace(copy, copy_reviewed=True)
    elif review is not None and review.revised is not None:
        if _revised_facts_ok(review.revised, facts):
            final_copy = _apply_revised_fields(copy, review.revised)
        # 否则：Reviewer 改坏了受信地点 -> 修订结果不被采用，保留原文案
    return _final_facts_guard(
        final_copy, facts, fallback_opening, fallback_closing
    )
