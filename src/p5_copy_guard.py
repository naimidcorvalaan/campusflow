"""Grounded narration and a separate model-backed fact checker."""

import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Optional
from types import SimpleNamespace

from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p5_agent_context import AgentDecisionContext
from src.p5_agent_runtime import call_structured_stage
from src.p5_plan_judge import CandidateSummary
from src.p5_copy_semantics import (
    COPY_SEMANTICS_RULES, copy_semantic_facts, has_invalid_progress_or_completion,
    opening_states_known_progress,
)

NARRATOR_SCHEMA_VERSION = "p5.grounded-narrator.v1"
COPY_CHECK_SCHEMA_VERSION = "p5.copy-fact-check.v1"

COPY_FACT_AUTHORITY = (
    "current_facts 是已应用本轮反馈并通过校验的正式状态，所有地点、时间、身份和取消状态以它为准。"
    "intent_history 仅帮助理解用户意图：initial_request是历史初始请求，latest_update是后续更新；"
    "不能拿初始请求中已被更新的事实否定current_facts，更不能在改文案时把旧事实恢复。"
    "计划未满足的意图可以如实说明，但不能通过文案改动正式状态。"
)


def _copy_context(context):
    """Separate historical language from the current authoritative snapshot."""
    current = context.to_payload()
    initial = current.pop("latest_user_text", None)
    update = current.pop("latest_feedback_text", None)
    return json.dumps(dict(current_facts=current,
        intent_history=dict(initial_request=initial, latest_update=update)), ensure_ascii=False)


@dataclass(frozen=True)
class GroundedNarrative:
    opening: str
    why_this_plan: Optional[str]
    closing: str
    proactive_suggestion: Optional[str] = None
    risk_note: Optional[str] = None
    generated: bool = True

    def __post_init__(self):
        for name in ("opening", "closing"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 240:
                raise ValueError("{} invalid".format(name))
        for name in ("why_this_plan", "proactive_suggestion", "risk_note"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 300):
                raise ValueError("{} invalid".format(name))


@dataclass(frozen=True)
class CopyFactCheck:
    safe: bool
    corrected_copy: Optional[GroundedNarrative] = None
    reason: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.safe, bool):
            raise ValueError("safe must be bool")
        if self.safe and self.corrected_copy is not None:
            raise ValueError("safe copy cannot include correction")


def generate_grounded_narrative(
    context, candidate, latest_user_text, caller, repair_caller=None, trace=None,
    approved_suggestion=None,
    actual_tasks=(),
):
    if not isinstance(context, AgentDecisionContext) or not isinstance(candidate, CandidateSummary):
        raise TypeError("context/candidate invalid")
    system, user = build_grounded_narrator_prompt(
        context, candidate, latest_user_text, approved_suggestion, actual_tasks
    )
    parser = parse_grounded_narrative
    narrative, updated = call_structured_stage(
        caller, repair_caller, system, user, parser,
        "grounded_narrator", "explain only the selected final timeline", trace,
        repair_system_prompt="只输出符合 {} 的完整 JSON，不增加任何计划事实。".format(NARRATOR_SCHEMA_VERSION),
    )
    narrative = narrative or grounded_copy_fallback(context, candidate)
    # Proactive advice has its own Qwen pass and deterministic feasibility
    # gate.  Narrator may phrase plan facts, but it may not invent a second
    # unchecked suggestion.  The approved text is inserted before the copy
    # checker so the entire visible copy is reviewed together.
    narrative = replace(
        narrative,
        proactive_suggestion=(approved_suggestion or None),
    )
    check_system, check_user = build_copy_check_prompt(context, candidate, narrative, actual_tasks)
    checker = lambda text: parse_copy_fact_check(text)
    check, updated = call_structured_stage(
        caller, repair_caller, check_system, check_user, checker,
        "copy_fact_checker", "verify action-location-time grounding", updated,
        repair_system_prompt="只输出符合 {} 的 JSON。".format(COPY_CHECK_SCHEMA_VERSION),
    )
    def finish(copy):
        from src.p5_clock_claims import bind_clock_claims
        guarded = _deterministic_copy_guard(context, candidate, copy, actual_tasks)
        return bind_clock_claims(context, guarded, grounded_copy_fallback(context, candidate),
                                caller, updated, actual_tasks)
    if check is None:
        return finish(narrative)
    if check.safe:
        return finish(narrative)
    if check.corrected_copy is not None:
        # The checker may correct wording, but it cannot introduce, remove or
        # replace the one suggestion that passed the independent deterministic
        # feasibility gate.
        checked_copy = replace(
            check.corrected_copy,
            proactive_suggestion=(approved_suggestion or None),
        )
        return finish(checked_copy)
    return grounded_copy_fallback(context, candidate), updated


def build_grounded_narrator_prompt(
    context, candidate, latest_user_text, approved_suggestion=None, actual_tasks=()
):
    system = (
        "你是 CampusFlow Grounded Narrator。只能根据最终通过硬校验的 timeline 和 remaining facts 写文案。"
        "不要把movement目的地与后续任务串错；只计划一部分时不得说‘做完/写完’，planned 不等于 completed。"
        "不要编造地点、时长、路线、并行授权。proactive_suggestion 只能逐字使用输入中的"
        "approved_suggestion；为 null 时必须输出 null。当前位置为 assumed 时，不得在文案中描述用户“正在/就在”该地点，"
        "因为页面会单独、且只会一次说明该假设。所有‘几点前完成/到达’必须逐项核对输入中的最终时间线："
        "到教学楼时间不等于到教室时间；不得把已安排完成的 meal 说成时间紧或饭后缺少缓冲。"
        "描述事件前后关系时必须沿最终 timeline 的时间顺序，不能把较晚事件写在‘随后/然后’之前。"
        "opening 只说明本次实际调整或最终时间线中可见的首要安排；总空闲较多不等于连续任务之间已经休息，"
        "若最终安排没有满足用户明确表达的顺序或偏好，opening须简短说明实际取舍及正式时间/路线依据；"
        "不能默默倒置顺序，也不能声称仍满足原顺序。若依据不足，只坦诚说明未满足，不编造冲突理由。"
        "risk_note用于用户执行前必须知道的未满足要求或取舍；有这种差异时用一句话说清原要求与实际安排，"
        "不要只放在why_this_plan中。没有真实差异或风险则null。"
        "没有程序提供的偏好达成事实时，不得笼统宣称已经充分满足‘余量/缓冲/休息’偏好。"
        "why_this_plan 只解释定性原因或取舍，不重复分钟、数量和钟点；这些由正式时间线呈现。"
        "closing也不用数字或钟点重新布置行动。没有可补充的原因时why_this_plan为null。"
        "开场简短，why最多两句，结尾一句。只输出 JSON："
        "{{schema_version:'{}',opening:string,why_this_plan:string|null,closing:string,"
        "proactive_suggestion:string|null,risk_note:string|null}}。"
    ).format(NARRATOR_SCHEMA_VERSION)
    system += "用户可见字段只放最终文案，不得包含注释、写作建议、反事实改写、内部字段名或推理。结尾无需重复具体时间。"
    system += COPY_SEMANTICS_RULES + COPY_FACT_AUTHORITY
    return system, "latest={}\napproved_suggestion={}\ncontext={}\nselected_final_candidate={}\ncopy_semantics={}".format(
        latest_user_text or "",
        json.dumps(approved_suggestion, ensure_ascii=False),
        _copy_context(context),
        json.dumps(_candidate_payload(candidate), ensure_ascii=False, sort_keys=True),
        json.dumps(copy_semantic_facts(context, actual_tasks), ensure_ascii=False),
    )


def build_copy_check_prompt(context, candidate, narrative, actual_tasks=()):
    system = (
        "你是 CampusFlow Copy Fact Checker。逐项检查地点—动作、计划/完成、meal/study、"
        "movement destination、before/after、concurrency、duration 和最新意图。逐项比较 copy 中的时间与"
        "最终 timeline，并区分到教学楼、到教室和课程开始。不能修改结构化计划。"
        "凡使用先后连接关系的两项活动，必须核对它们在最终 timeline 中的实际顺序。"
        "只核对事实是否成立，不因文风或重述已确认事实而修订正确文案。"
        "why_this_plan和closing只表达定性原因或收尾，不重复正式时间线的数字和钟点。"
        "用户可见开场应如实说明未满足的明确偏好；不要删掉有最终时间线依据的取舍说明。"
        "输出 JSON：{{schema_version:'{}',safe:bool,reason:string|null,corrected_copy:object|null}}。"
        "safe=true 时 corrected_copy=null；否则可给完整 p5.grounded-narrator.v1 文案。"
    ).format(COPY_CHECK_SCHEMA_VERSION)
    system += "corrected_copy 的每个字段都直接展示给用户，禁止夹带‘注：若…应改为…’等审核意见和内部字段。信息充足时不得追加确认开始时间或已知截止时间的追问。"
    system += COPY_SEMANTICS_RULES + COPY_FACT_AUTHORITY
    return system, "facts={}\ncandidate={}\ncopy={}\ncopy_semantics={}".format(
        _copy_context(context), json.dumps(_candidate_payload(candidate), ensure_ascii=False, sort_keys=True),
        json.dumps(_narrative_payload(narrative), ensure_ascii=False, sort_keys=True),
        json.dumps(copy_semantic_facts(context, actual_tasks), ensure_ascii=False),
    )


def parse_grounded_narrative(text):
    payload = extract_json_object(text)
    required = {
        "schema_version", "opening", "why_this_plan", "closing",
        "proactive_suggestion", "risk_note",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise AgenticParseError("grounded narrator payload invalid")
    if payload.get("schema_version") != NARRATOR_SCHEMA_VERSION:
        raise AgenticParseError("grounded narrator schema mismatch")
    return GroundedNarrative(
        _required(payload, "opening"), _optional(payload.get("why_this_plan")),
        _required(payload, "closing"), _optional(payload.get("proactive_suggestion")),
        _optional(payload.get("risk_note")),
    )


def parse_copy_fact_check(text):
    payload = extract_json_object(text)
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "safe", "reason", "corrected_copy"
    }:
        raise AgenticParseError("copy check payload invalid")
    if payload.get("schema_version") != COPY_CHECK_SCHEMA_VERSION:
        raise AgenticParseError("copy check schema mismatch")
    safe = payload.get("safe")
    if not isinstance(safe, bool):
        raise AgenticParseError("copy check safe must be bool")
    corrected = payload.get("corrected_copy")
    parsed = None
    if corrected is not None:
        if not isinstance(corrected, dict):
            raise AgenticParseError("corrected_copy invalid")
        corrected = dict(corrected)
        corrected.setdefault("schema_version", NARRATOR_SCHEMA_VERSION)
        parsed = parse_grounded_narrative(json.dumps(corrected, ensure_ascii=False))
    if safe and parsed is not None:
        raise AgenticParseError("safe check cannot contain correction")
    return CopyFactCheck(safe, parsed, _optional(payload.get("reason")))


def grounded_copy_fallback(context, candidate):
    opening = "我按现在确定的时间、地点和任务顺序把方案整理好了。"
    remaining = [ref for ref, value in candidate.remaining_work if value > 0]
    closing = "按这份时间线往下走就好，剩余任务之后还能继续调整。" if remaining else "按这份时间线往下走就好，祝你接下来顺利。"
    # The fallback may state one observable property of the selected final
    # timeline.  It never invents a causal reason or a new recommendation.
    why = observable_plan_note(context)
    return GroundedNarrative(opening, why, closing, generated=False)


def _deterministic_copy_guard(context, candidate, narrative, actual_tasks=()):
    allowed_locations = {
        item.execution_location.display_name
        for item in context.active_tasks if item.execution_location is not None
    }
    if context.current_location is not None:
        allowed_locations.add(context.current_location.display_name)
    text = " ".join(
        value for value in (
            narrative.opening, narrative.why_this_plan, narrative.closing,
            narrative.proactive_suggestion, narrative.risk_note,
        ) if value
    )
    if re.search(r"schema_version|task_ref|current_location_source|selected_final_candidate|"
                 r"(?:内部|模型的|我的)推理|(?:注[：:]|鉴于facts|应改为|作为.{0,8}审[核查])|<think>", text, re.I):
        return grounded_copy_fallback(context, candidate)
    if has_invalid_progress_or_completion(context, candidate, narrative, actual_tasks):
        # A checker can introduce a false progress claim into one field of an
        # otherwise faithful explanation. Replace only that field, then run all
        # remaining guards on the resulting copy. No model fact is accepted
        # merely because another field passed.
        fallback = grounded_copy_fallback(context, candidate)
        fields = ("opening", "why_this_plan", "closing", "proactive_suggestion", "risk_note")
        changes = {}
        for field in fields:
            isolated = SimpleNamespace(**{name: getattr(narrative, name) if name == field else None
                                          for name in fields})
            if has_invalid_progress_or_completion(context, candidate, isolated, actual_tasks):
                changes[field] = getattr(fallback, field)
        narrative = replace(narrative, generated=False, **changes)
        if has_invalid_progress_or_completion(context, candidate, narrative, actual_tasks):
            return fallback
        text = " ".join(getattr(narrative, field) for field in fields if getattr(narrative, field))
    if (not _opening_points_to_final_facts(context, narrative.opening)
            and not opening_states_known_progress(context, narrative.opening, actual_tasks)):
        narrative = replace(
            narrative, opening=observable_plan_opening(context)
        )
        text = " ".join(
            value for value in (
                narrative.opening, narrative.why_this_plan, narrative.closing,
                narrative.proactive_suggestion, narrative.risk_note,
            ) if value
        )
    if context.current_location_source == "assumed" and context.current_location is not None:
        name = context.current_location.display_name
        assumption_markers = ("你正在", "你现在在", "你现在就在", "你在{}".format(name))
        if name in text and any(marker in text for marker in assumption_markers):
            return grounded_copy_fallback(context, candidate)
    # Meal and buffer urgency are time facts, never model intuition.  A meal
    # present in the final candidate is not allowed to acquire an unsupported
    # "tight/no buffer" warning in copy.
    if candidate.meal_timing and any(marker in text for marker in (
        "晚饭时间较紧", "吃饭时间较紧", "饭后缺少缓冲", "餐后缺少缓冲", "没有缓冲",
    )):
        return grounded_copy_fallback(context, candidate)
    if _has_invalid_final_time_claim(context, text):
        return grounded_copy_fallback(context, candidate)
    foreign_campus = "北洋园" if context.selected_campus_id == "weijinlu" else "卫津路"
    if foreign_campus in text:
        return grounded_copy_fallback(context, candidate)
    task_titles = {item.task_ref: item.title for item in context.active_tasks}
    commitment_titles = {
        item.commitment_ref: item.title for item in context.fixed_commitments
    }
    for movement in context.movements:
        destination = movement.destination_name
        if not destination:
            continue
        expected = task_titles.get(
            movement.destination_activity_ref,
            commitment_titles.get(movement.destination_activity_ref),
        )
        for title in task_titles.values():
            if title != expected and "去{}{}".format(destination, title) in text:
                return grounded_copy_fallback(context, candidate)
    return narrative


_RANGE_RE = re.compile(
    r"^(?P<start>\d{1,2}:\d{2})[–-](?P<end>\d{1,2}:\d{2})[：:]\s*(?P<body>.*)$"
)
_CURRENT_RE = re.compile(r"^现在[：:]\s*(?P<body>.*)做到\s*(?P<end>\d{1,2}:\d{2})")
_ARRIVAL_CLAIM_RE = re.compile(
    r"(?P<time>\d{1,2}:\d{2})(?P<before>前)?(?:到达|到)(?P<place>教学楼|教室)"
)
_MENTIONED_RANGE_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2})[–-](?P<end>\d{1,2}:\d{2})"
)
_FORWARD_RELATION_RE = re.compile(r"(?:随后|然后|接着|之后|再|紧接着)")


def observable_plan_note(context):
    """Return one concise observation computed from the final timeline."""
    rows = _timeline_rows(context)
    if not rows:
        return None
    task_by_title = {item.title: item for item in context.active_tasks}
    study = []
    for start, end, body in rows:
        task = next((item for title, item in task_by_title.items() if title in body), None)
        if task is not None and task.activity_kind != "meal":
            study.append((start, end, task.title))
    chains = []
    for row in study:
        if chains and chains[-1][-1][1] == row[0]:
            chains[-1].append(row)
        else:
            chains.append([row])
    longest = max(chains, key=lambda values: sum(end - start for start, end, _ in values), default=[])
    parts = []
    if len(longest) >= 2:
        titles = tuple(dict.fromkeys(title for _, _, title in longest))
        minutes = sum(end - start for start, end, _ in longest)
        parts.append("{}连续安排{}分钟".format("和".join(titles), minutes))

    occupied = _merge_ranges((start, end) for start, end, _ in rows)
    meal_ends = []
    meal_titles = {item.title for item in context.active_tasks if item.activity_kind == "meal"}
    for start, end, body in rows:
        if any(title in body for title in meal_titles):
            meal_ends.append(end)
    gaps = []
    for left, right in zip(occupied, occupied[1:]):
        if right[0] - left[1] >= 5:
            gaps.append((left[1], right[0]))
    post_meal = [gap for gap in gaps if gap[0] in meal_ends]
    if post_meal:
        gap = max(post_meal, key=lambda item: item[1] - item[0])
        parts.append("较长的休整余量在饭后{}–{}".format(
            _minute_text(gap[0]), _minute_text(gap[1])
        ))
    return "；".join(parts) + "。" if parts else None


def observable_plan_opening(context):
    """Build a non-evaluative summary from the first final activity."""
    rows = _timeline_rows(context)
    first_body = rows[0][2] if rows else ""
    task = next(
        (item for item in context.active_tasks if item.title in first_body),
        None,
    )
    if task is not None:
        return "先从{}开始，后续安排按这份时间线衔接。".format(task.title)
    commitment = next(
        (item for item in context.fixed_commitments if item.title in first_body),
        None,
    )
    if commitment is not None:
        return "先按时间线衔接{}，其余安排继续顺着可行窗口推进。".format(
            commitment.title
        )
    return "这是根据目前信息整理的安排。"


def _opening_points_to_final_facts(context, text):
    """Require an initial summary to name something in its final candidate.

    This is a positive grounding rule rather than a blacklist of flattering
    wording.  What-if adoption has its own ref-bound deterministic change
    summary, while initial/feedback narration must point at a visible task,
    commitment or exact final time before it can be published.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    names = tuple(item.title for item in context.active_tasks) + tuple(
        item.title for item in context.fixed_commitments
    )
    if any(name in text for name in names):
        return True
    final_times = set()
    for start, end, _ in _timeline_rows(context):
        final_times.add(_minute_text(start))
        final_times.add(_minute_text(end))
    return any(value in text for value in final_times)


def _has_invalid_final_time_claim(context, text):
    """Reject only machine-checkable time claims contradicted by final facts."""
    if _has_reversed_final_sequence_claim(context, text):
        return True
    if _has_invalid_task_clock_claim(context, text):
        return True
    for match in _ARRIVAL_CLAIM_RE.finditer(text):
        claimed = _clock_minutes(match.group("time"))
        expected_values = []
        for commitment in context.fixed_commitments:
            expected_text = (
                commitment.class_arrival_deadline
                if match.group("place") == "教学楼"
                else commitment.classroom_arrival_time
            )
            if expected_text:
                expected_values.append(_iso_clock_minutes(expected_text))
        if expected_values:
            valid = (
                any(expected <= claimed for expected in expected_values)
                if match.group("before")
                else claimed in expected_values
            )
            if not valid:
                return True

    meal_ends = {}
    meal_titles = {item.title for item in context.active_tasks if item.activity_kind == "meal"}
    for _, end, body in _timeline_rows(context):
        for title in meal_titles:
            if title in body:
                meal_ends[title] = max(meal_ends.get(title, 0), end)
    for title, actual_end in meal_ends.items():
        aliases = (title, title[1:]) if title.startswith("吃") and len(title) > 1 else (title,)
        pattern = re.compile(
            r"(?P<time>\d{{1,2}}:\d{{2}})前[^。；，]*?(?:吃完|结束)[^。；，]*?(?:{})".format(
                "|".join(re.escape(alias) for alias in aliases)
            )
        )
        if any(_clock_minutes(match.group("time")) < actual_end for match in pattern.finditer(text)):
            return True
    return False


def _has_invalid_task_clock_claim(context, text):
    rows = _timeline_rows(context)
    for clause in re.split(r"[，。；\n]", text):
        clocks = re.findall(r"\d{1,2}:\d{2}", clause)
        if len(clocks) != 1:
            continue
        clock = _clock_minutes(clocks[0])
        if "出发" in clause and context.movements:
            starts = {_iso_clock_minutes(m.starts_at) for m in context.movements if m.starts_at}
            if starts and clock not in starts:
                return True
        if re.search(re.escape(clocks[0]) + r"\s*(?:开始收拾|收拾|开始准备出发)", clause):
            starts = {_iso_clock_minutes(m.preparation_starts_at)
                      for m in context.movements if m.preparation_starts_at}
            if starts and clock not in starts:
                return True
        # A named event's end must not borrow its start, especially while the
        # end is explicitly unknown. Do not infer subjects across clauses.
        if re.search(r"下课|结束", clause):
            named = [c for c in context.fixed_commitments if c.title in clause]
            classes = [c for c in context.fixed_commitments if c.commitment_kind == "class"]
            subjects = named or (classes if len(classes) == 1 and "下课" in clause else [])
            if len(subjects) == 1:
                title = re.escape(subjects[0].title)
                time = re.escape(clocks[0])
                explicit_end = re.search(
                    time + r"\s*(?:" + title + r"\s*)?(?:结束|下课)|"
                    + title + r"\s*(?:将于|于|在)?\s*" + time + r"\s*(?:结束|下课)", clause)
                if explicit_end:
                    end = subjects[0].ends_at
                    if end is None or _iso_clock_minutes(end) != clock:
                        return True
        for task in context.active_tasks:
            if task.title not in clause:
                continue
            intervals = [(start, end) for start, end, body in rows if task.title in body]
            if not intervals:
                continue
            first, last = min(s for s, _ in intervals), max(e for _, e in intervals)
            if any(word in clause for word in ("完成", "写完", "结束")):
                if ("前" in clause and clock < last) or ("前" not in clause and clock != last):
                    return True
            elif "开始" in clause and clock != first:
                return True
            elif any(word in clause for word in ("接着", "继续")) and not any(s <= clock < e for s, e in intervals):
                return True
    return False


def _has_reversed_final_sequence_claim(context, text):
    """Validate expressed forward relations against the selected final order.

    This does not ban connective words.  It accepts natural narration when
    the two cited intervals move forward, and rejects only a relation whose
    exact intervals both exist in the final timeline but are reversed.
    """
    known = {(start, end) for start, end, _ in _timeline_rows(context)}
    matches = tuple(_MENTIONED_RANGE_RE.finditer(text or ""))
    for left, right in zip(matches, matches[1:]):
        left_range = (
            _clock_minutes(left.group("start")),
            _clock_minutes(left.group("end")),
        )
        right_range = (
            _clock_minutes(right.group("start")),
            _clock_minutes(right.group("end")),
        )
        if left_range not in known or right_range not in known:
            continue
        relation = text[left.end():right.start()]
        if _FORWARD_RELATION_RE.search(relation) and right_range[0] < left_range[1]:
            return True
    return False


def _timeline_rows(context):
    rows = []
    now = datetime.fromisoformat(context.current_time)
    now_minutes = now.hour * 60 + now.minute
    for raw in context.selected_timeline:
        line = str(raw).strip()
        match = _RANGE_RE.match(line)
        if match:
            start = _clock_minutes(match.group("start"))
            end = _clock_minutes(match.group("end"))
            if end >= start:
                rows.append((start, end, match.group("body").strip()))
            continue
        match = _CURRENT_RE.match(line)
        if match:
            end = _clock_minutes(match.group("end"))
            if end >= now_minutes:
                rows.append((now_minutes, end, match.group("body").strip()))
    return tuple(sorted(rows, key=lambda item: (item[0], item[1], item[2])))


def _merge_ranges(values):
    merged = []
    for start, end in sorted(values):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return tuple((start, end) for start, end in merged)


def _clock_minutes(value):
    hour, minute = (int(part) for part in value.split(":", 1))
    return hour * 60 + minute


def _iso_clock_minutes(value):
    parsed = datetime.fromisoformat(value)
    return parsed.hour * 60 + parsed.minute


def _minute_text(value):
    return "{:02d}:{:02d}".format(value // 60, value % 60)


def _candidate_payload(item):
    return {
        "candidate_id": item.candidate_id,
        "timeline": list(item.timeline),
        "planned_minutes_by_task": list(item.task_completion),
        "unallocated_minutes_after_entire_plan": list(item.remaining_work),
        "concurrency_pairs": list(item.concurrency_pairs),
        "unresolved_questions": list(item.unresolved_questions),
    }


def _narrative_payload(item):
    return {
        "schema_version": NARRATOR_SCHEMA_VERSION,
        "opening": item.opening,
        "why_this_plan": item.why_this_plan,
        "closing": item.closing,
        "proactive_suggestion": item.proactive_suggestion,
        "risk_note": item.risk_note,
    }


def _required(payload, name):
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise AgenticParseError("{} must be text".format(name))
    return value.strip()


def _optional(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AgenticParseError("optional copy must be text")
    return value.strip()
