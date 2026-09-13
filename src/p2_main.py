"""CampusFlow P2 独立页面入口（Python 3.8 兼容）；导入时不执行页面。

本轮使用离线 mock caller，不调用真实 TJU API / 网络 / .env。
页面只允许三个动作触发新规划：开始全天计划 / 应用反馈并重新规划 / 刷新方案；
普通 rerun 命中会话缓存，不重新调用 Agent。
"""

import html
import re
from dataclasses import dataclass, replace
from datetime import datetime

from src.campusflow_ui import CAMPUSFLOW_THEME_CSS
from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_companion_copy import CONTEXT_INITIAL, companion_fallbacks
from src.p2_day_plan import compact_plan_lines
from src.p2_models import DayPlanningState, TaskProgress, TaskState
from src.p2_question_filter import filter_pending_questions
from src.p2_session import P2SessionController, P2SessionTurn
from src.p2_window_derivation import derive_day_state
from src.p3_route_planner import final_plan_overlap_errors

P2_INPUT_KEY = "p2_feedback_input"


_TASK_NONE = '{"schema_version": "p2.task-reconciliation.v1", "updates": [], "questions": []}'
_TASK_PROGRESS_30 = (
    '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
    '{"target_task_ref": "day_task_001", "new_task_title": null, '
    '"progress_delta_minutes": 30, "set_total_minutes": null, '
    '"set_total_source": null, "lifecycle_action": "none", '
    '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
)
_TASK_SKIP = (
    '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
    '{"target_task_ref": "day_task_002", "new_task_title": null, '
    '"progress_delta_minutes": null, "set_total_minutes": null, '
    '"set_total_source": null, "lifecycle_action": "skip_today", '
    '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
)
_TASK_RESUME = (
    '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
    '{"target_task_ref": "day_task_002", "new_task_title": null, '
    '"progress_delta_minutes": null, "set_total_minutes": null, '
    '"set_total_source": null, "lifecycle_action": "resume_today", '
    '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
)

_COMMITMENT_NONE = (
    '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [], "questions": []}'
)
_COMMITMENT_DELAY = (
    '{"schema_version": "p2.commitment-reconciliation.v1", "updates": ['
    '{"target_commitment_ref": "day_commitment_001", "action": "delay", '
    '"title": null, "starts_at": null, "ends_at": null, "delay_minutes": 20}], '
    '"questions": []}'
)
_COMMITMENT_ADD = (
    '{"schema_version": "p2.commitment-reconciliation.v1", "updates": ['
    '{"target_commitment_ref": null, "action": "add", "title": "组会", '
    '"starts_at": "14:00", "ends_at": "15:00", "delay_minutes": null}], '
    '"questions": []}'
)
_COMMITMENT_CANCEL = (
    '{"schema_version": "p2.commitment-reconciliation.v1", "updates": ['
    '{"target_commitment_ref": "day_commitment_003", "action": "cancel", '
    '"title": null, "starts_at": null, "ends_at": null, "delay_minutes": null}], '
    '"questions": []}'
)

_ROUTER_TASK = (
    '{"schema_version": "p2.feedback-router.v1", "route": "task", "reason": null}'
)
_ROUTER_COMMITMENT = (
    '{"schema_version": "p2.feedback-router.v1", "route": "commitment", "reason": null}'
)
_ROUTER_UNKNOWN = (
    '{"schema_version": "p2.feedback-router.v1", "route": "unclear", "reason": null}'
)

_PLAN_EMPTY = (
    '{"schema_version": "p2.day-plan-intent.v1", "task_order": [], '
    '"include_low_attention": false, "task_estimates": [], "rationale": null}'
)
_REVIEW_ACCEPT = (
    '{"schema_version": "p2.day-review.v1", "decision": "accept", '
    '"reason": "ok", "suggested_task_order": null, "include_low_attention": null}'
)


def build_demo_state() -> DayPlanningState:
    """结构化/半结构化首次全天计划 demo 输入（固定时间，便于复现）。"""
    now = datetime(2026, 9, 1, 9, 0)
    day_end = datetime(2026, 9, 1, 22, 0)
    commitments = (
        FixedCommitment(
            "day_commitment_001", "上课", "上课",
            datetime(2026, 9, 1, 10, 0), datetime(2026, 9, 1, 11, 30),
            None, AvailabilityLevel.UNAVAILABLE, {}, (),
        ),
        FixedCommitment(
            "day_commitment_002", "实验", "实验",
            datetime(2026, 9, 1, 14, 0), datetime(2026, 9, 1, 15, 30),
            None, AvailabilityLevel.UNAVAILABLE, {}, (),
        ),
    )
    tasks = (
        TaskProgress(
            "day_task_001", "计组实验3", 120, 50,
            SourceKind.AI_EXTRACTED_FROM_USER_TEXT, TaskState.ACTIVE, True, 30,
        ),
        TaskProgress(
            "day_task_002", "背单词", 30, 0,
            SourceKind.AI_EXTRACTED_FROM_USER_TEXT, TaskState.ACTIVE, False, None,
        ),
    )
    return derive_day_state(now, day_end, commitments, tasks, 10, {}, (), now)


_SAFE_FALLBACK_TEXT = "这个反馈暂时无法确定对应哪个任务，请再说明一下。"

_FORBIDDEN_INTERNAL_PATTERNS = (
    "task_ref",
    "commitment_ref",
    "allocation_ref",
    "window_ref",
    "schema_version",
    "traceback",
    r"\bjson\b",
    r"\bACTIVE\b",
    r"\bCOMPLETED\b",
    r"\bSKIPPED_TODAY\b",
    r"\bABANDONED\b",
    r"day_task_\w*",
    r"day_window_\w*",
    r"day_commitment_\w*",
    r"allocation_\w*",
    r"\[内部(引用|状态)\]",
    r"<[\w.]+ object at 0x[0-9a-fA-F]+>",
    r"\bTaskProgress\b",
    r"\bDayPlanningState\b",
    r"\bLifecycleAction\b",
    r"\bTaskState\b",
    r"\bReconciliationUpdate\b",
    r"\bP2AgenticDayResult\b",
    r"\bP2SessionTurn\b",
    r"\bDayIntakeOutcome\b",
    r"\bDayWindow\b",
    r"\bFixedCommitment\b",
    r"\bTaskAllocation\b",
    r"\bDayAllocationPlan\b",
    r"\bprogress_delta_minutes\b",
    r"\bset_total_minutes\b",
    r"\bset_total_source\b",
    r"\bminimum_slice_minutes\b",
    r"\blifecycle_action\b",
    r"\bis_splittable\b",
    r"\bnew_task_title\b",
    r"\btotal_source\b",
    r"\bestimated_total_minutes\b",
    r"\binclude_low_attention\b",
    r"\btask_order\b",
    r"\bsuggested_task_order\b",
    r"\bstarts_at\b",
    r"\bends_at\b",
    r"\bstarts_in_minutes\b",
    r"\bduration_minutes\b",
    r"\blocation_text\b",
    r"\bdelay_minutes\b",
    r"\bday_end\b",
    r"\breference_datetime\b",
    r"\bresume_today\b",
    r"\bskip_today\b",
    r"\bcomplete\b",
    r"\babandon\b",
    r"\ballocator\b",
    r"\bsplittability\b",
    r"\bminimum_slice\b",
    r"\bpeak_multiplier\b",
    r"\btransition_buffer\b",
    r"\bwindow capacity\b",
    r"\bschema\b",
    r"\bref\b",
    r"\benum\b",
    r"\bprovenance\b",
)
_FORBIDDEN_INTERNAL_RE = re.compile(
    "|".join("({})".format(p) for p in _FORBIDDEN_INTERNAL_PATTERNS),
    re.IGNORECASE,
)

def sanitize_user_facing_text(text: str) -> str:
    """前端硬护栏：所有用户可见 question / warning / fallback 文本在渲染前统一经过本函数。

    检测到内部 ref、状态枚举、schema 字段、JSON、traceback 或 Python/dataclass repr 时，
    不原样展示，改为一条安全、中性的中文提示，避免向用户泄露内部参数。
    """
    if not isinstance(text, str):
        return ""
    stripped = text.strip()
    if not stripped:
        return ""
    if _FORBIDDEN_INTERNAL_RE.search(stripped):
        return _SAFE_FALLBACK_TEXT
    return stripped

def _plan_lines(turn: P2SessionTurn):
    """当前方案行：有移动时用 P3 移动渲染（合并真实时间），否则用 P2 摘要。

    P3e 精简：最终“当前方案”内部顺序为
    [opening] -> [本轮更新（仅 feedback 真实变化）] -> [实际计划时间表]
    -> [小空档（仅真实空档）] -> [closing]。
    已删除 plan_title / 今日主线 / 为什么这么排 / 当前节奏。
    所有文案字段必须过 sanitizer，不安全时整段替换为安全 fallback。
    """
    opening = None
    change_summary = None
    gap_tip = None
    gap_tip_kind = None
    closing = None
    companion = getattr(turn, "companion_copy", None)
    if companion is not None:
        context_type = getattr(companion, "context_type", CONTEXT_INITIAL)
        fallback_opening, fallback_closing = companion_fallbacks(context_type)

        def _safe(value, fallback):
            cleaned = sanitize_user_facing_text(value) if value is not None else ""
            if not cleaned or cleaned == _SAFE_FALLBACK_TEXT:
                return fallback
            return cleaned

        opening = _safe(getattr(companion, "opening", None), fallback_opening)
        closing = _safe(getattr(companion, "closing", None), fallback_closing)
        change_summary = _safe(getattr(companion, "change_summary", None), None)
        gap_tip = _safe(getattr(companion, "gap_tip", None), None)
        gap_tip_kind = getattr(companion, "gap_tip_kind", None)
    blocks = getattr(turn, "movement_blocks", ())
    execution_context = getattr(turn, "execution_context", None)
    # A legacy P3 turn may carry the empty context snapshot merely because it
    # passed through the session layer.  Only real execution facts opt into
    # P4-specific wording (route facts remain unchanged otherwise).
    if not _has_execution_presentation_facts(execution_context):
        execution_context = None
    if execution_context is not None:
        timeline_errors = final_plan_overlap_errors(
            turn.result.updated_state,
            turn.result.allocation_plan,
            getattr(turn, "movement_blocks", ()),
        )
        if timeline_errors:
            # P4 routes, task bindings and class prep must originate from one
            # final turn. Fail closed instead of rendering a mixed timeline.
            return ("当前方案正在重新校验，请刷新后重试。",)
    if blocks or execution_context is not None:
        from src.p3_route_planner import render_movement_lines

        plan_lines = render_movement_lines(
            blocks,
            turn.result.allocation_plan,
            turn.result.updated_state,
            execution_context=execution_context,
            concurrent_allocations=getattr(turn, "concurrent_allocations", ()),
        )
    else:
        plan_lines = compact_plan_lines(
            turn.result.allocation_plan,
            turn.result.updated_state,
            concurrent_allocations=getattr(turn, "concurrent_allocations", ()),
        )
    lines = []
    if opening:
        lines.append(opening)
    from src.p4_execution_presentation import assumed_location_line
    assumption = assumed_location_line(execution_context)
    if assumption:
        lines.append(sanitize_user_facing_text(assumption))
    if change_summary:
        lines.append("本轮更新：" + change_summary)
    for line in plan_lines:
        if line.startswith("今天暂未安排："):
            lines.append(_unallocated_plan_copy(line, turn.result))
        else:
            lines.append(line)
    if gap_tip:
        label = "一段空闲时间：" if gap_tip_kind == "free" else "小空档："
        lines.append(label + gap_tip)
    if closing:
        lines.append(closing)
    return tuple(lines)


def _unallocated_plan_copy(line, result):
    """Describe a partially scheduled task without implying a fake later slot."""
    title = line[len("今天暂未安排："):].strip()
    state = result.updated_state
    refs = [task.task_ref for task in state.tasks if task.title == title]
    if refs and all(task.total_minutes is None for task in state.tasks if task.task_ref in refs):
        return "{}的任务时间不确定；可展开上方估时确认范围和用时，或在调整框补充分钟数。".format(title)
    planned = any(item.task_ref in refs for item in result.allocation_plan.allocations)
    if planned:
        if any(item.ends_at is None for item in state.commitments):
            return "{}还有一部分，暂不安排，等你确认下课时间后再继续调整。".format(title)
        return "{}还有一部分，之后可以再继续。".format(title)
    # This is genuinely unplanned; do not imply that the system knows when it
    # will happen.
    return "{}暂未安排，之后可以再决定是否继续。".format(title)


def _has_execution_presentation_facts(context) -> bool:
    # A legacy P3 intake can also carry a resolved current location.  P4 copy
    # is enabled only after Day Intake supplied an execution semantic; this
    # keeps existing movement-only plans byte-for-byte compatible.
    return bool(
        getattr(context, "confirmations", ())
        or getattr(context, "concurrency_authorizations", ())
        or any(
            getattr(binding, "activity_kind", None) in ("generic", "meal")
            for binding in getattr(context, "bindings", ())
        )
    )


def render_page_text(turn: P2SessionTurn, extra_questions=()) -> str:
    """P2e/P3e：页面只保留两个输出口——“当前方案”主区块 + “待确认问题”副区块。

    - “今天接下来 / AI 暂估 / 提示”不再作为独立区块；
    - AI 暂估内联在对应任务行；移动行（步行/骑行前往）按真实时间段并入主区块；
    - 普通提示不抢占主界面。
    """
    lines = ["# 当前方案"]
    lines.extend(_plan_lines(turn))
    # Keep the text fallback on the same P5 contract as the card UI: one
    # high-information clarification at most, never an unranked checklist.
    questions = _pending_questions(turn, extra_questions)
    if questions:
        lines.append("")
        lines.append("## 待确认问题")
        lines.extend("- " + sanitize_user_facing_text(question) for question in questions)
    return "\n".join(lines)

_PLAN_TIME_RE = re.compile(r"^\d{1,2}:\d{2}")


@dataclass(frozen=True)
class PlanDisplayEntry:
    """A visual-only projection of one persisted final-plan line.

    This object deliberately carries no planning decision.  It only lets the
    Streamlit renderer distinguish primary actions from deterministic helper
    steps without having to change the allocator or route model.
    """

    time_text: str
    body: str
    is_primary: bool
    is_current: bool
    kind: str
    auto_selected_meal: bool = False
    has_ai_estimate: bool = False
    has_building_arrival: bool = False


@dataclass(frozen=True)
class CurrentPlanDisplay:
    opening: str
    intro_notes: tuple
    entries: tuple
    closing: str
    side_notes: tuple


_HELPER_LABELS = (
    "收拾东西",
    "进楼 / 找教室",
    "到教室后签到 / 课前准备",
)
_LINE_SPLIT_RE = re.compile(
    r"^(?P<time>现在|\d{1,2}:\d{2}(?:–\d{1,2}:\d{2})?起?)：( ?)(?P<body>.*)$"
)
_BUILDING_ARRIVAL_RE = re.compile(r"(，?\d{1,2}:\d{2}到教学楼)")
_SLACK_RENDER_THRESHOLD_MINUTES = 5


def build_current_plan_display(turn: P2SessionTurn) -> CurrentPlanDisplay:
    """Turn persisted final facts into a compact card/timeline view model.

    The line strings remain the existing deterministic presentation output.
    The only structured visual annotation is the auto-meal marker, which is
    derived from the destination activity binding rather than title matching.
    """
    lines = tuple(_plan_lines(turn))
    body_indices = [index for index, line in enumerate(lines) if _is_plan_body_line(line)]
    if not body_indices:
        return CurrentPlanDisplay(
            opening=lines[0] if lines else "",
            intro_notes=tuple(lines[1:]),
            entries=(),
            closing="",
            side_notes=(),
        )

    first, last = body_indices[0], body_indices[-1]
    intro = list(lines[:first])
    trailing = list(lines[last + 1:])
    opening = intro.pop(0) if intro else ""
    closing = ""
    if trailing and not trailing[-1].startswith(("一段空闲时间：", "小空档：")):
        closing = trailing.pop()

    entries = _with_visible_slack(
        tuple(_display_entry_for_line(turn, line) for line in lines[first:last + 1])
    )
    side_notes = tuple(
        line for line in trailing
        if line.startswith(("一段空闲时间：", "小空档：", "本轮更新："))
    )
    intro_notes = tuple(intro) + tuple(
        line for line in trailing if line not in side_notes
    )
    return CurrentPlanDisplay(
        opening=opening,
        intro_notes=intro_notes,
        entries=entries,
        closing=closing,
        side_notes=side_notes,
    )


def _display_entry_for_line(turn: P2SessionTurn, line: str) -> PlanDisplayEntry:
    match = _LINE_SPLIT_RE.match(line)
    if match is None:
        return PlanDisplayEntry("", line, True, False, "note")
    time_text = match.group("time")
    body = match.group("body").strip()
    is_current = time_text == "现在"
    helper = body in _HELPER_LABELS
    concurrent = body.startswith("同时：")
    if helper:
        body = {
            "进楼 / 找教室": "进楼找教室",
            "到教室后签到 / 课前准备": "课前准备",
        }.get(body, body)
    auto_meal = _line_is_auto_meal_movement(turn, time_text)
    kind = "concurrent" if concurrent else _display_entry_kind(body, helper)
    if kind == 'task' and any(
        c.starts_at is not None and time_text.startswith(c.starts_at.strftime('%H:%M'))
        and body == c.title for c in turn.result.updated_state.commitments
    ):
        kind = 'fixed'
    return PlanDisplayEntry(
        time_text=time_text,
        body=body,
        # "现在：收拾东西" is the user's immediate action and deliberately
        # remains prominent even though the same step is helper-styled later.
        is_primary=is_current or (not helper and not concurrent),
        is_current=is_current,
        kind=kind,
        auto_selected_meal=auto_meal,
        has_ai_estimate="（AI暂估）" in body,
        has_building_arrival=bool(_BUILDING_ARRIVAL_RE.search(body)),
    )


def _with_visible_slack(entries):
    """Expose real final-timeline gaps instead of silently backward-packing them.

    This only projects gaps from adjacent deterministic rendered intervals; it
    never invents a task or treats an unknown-ended commitment as free time.
    """
    output = []
    previous_end = None
    for entry in entries:
        start, end = _entry_minutes(entry.time_text)
        # The active action is rendered as “现在：…做到 HH:MM”, so it has no
        # normal range prefix.  Its deterministic end is still part of the
        # final timeline complement and must seed a visible later slack item.
        if entry.time_text == "现在":
            active_end = re.search(r"做到\s*(\d{1,2}):(\d{2})", entry.body)
            if active_end is not None:
                end = int(active_end.group(1)) * 60 + int(active_end.group(2))
        if previous_end is not None and start is not None:
            gap = start - previous_end
            if gap >= _SLACK_RENDER_THRESHOLD_MINUTES:
                output.append(PlanDisplayEntry(
                    time_text="{}–{}".format(_minutes_hm(previous_end), _minutes_hm(start)),
                    body="修整一下", is_primary=False, is_current=False, kind="helper",
                ))
        output.append(entry)
        if end is not None:
            previous_end = end
        elif start is not None and not entry.time_text.endswith("起"):
            previous_end = start
        elif entry.time_text.endswith("起"):
            previous_end = None
    return tuple(output)


def _entry_minutes(time_text):
    if not time_text or time_text == "现在" or time_text.endswith("起"):
        return None, None
    match = re.match(r"^(\d{1,2}):(\d{2})(?:–(\d{1,2}):(\d{2}))?$", time_text)
    if match is None:
        return None, None
    start = int(match.group(1)) * 60 + int(match.group(2))
    if match.group(3) is None:
        return start, start
    end = int(match.group(3)) * 60 + int(match.group(4))
    return start, end if end >= start else None


def _minutes_hm(value):
    return "{:02d}:{:02d}".format(value // 60, value % 60)


def _line_is_auto_meal_movement(turn: P2SessionTurn, time_text: str) -> bool:
    """Read the meal tag from structured destination provenance only."""
    context = getattr(turn, "execution_context", None)
    if context is None:
        return False
    from src.p4_execution_presentation import is_auto_selected_meal_destination

    for block in getattr(turn, "movement_blocks", ()):
        if not is_auto_selected_meal_destination(block, context):
            continue
        if time_text == "现在":
            now = turn.result.updated_state.now
            if block.window_start <= now < block.end_time:
                return True
        elif block.window_start.strftime("%H:%M") == time_text[:5]:
            return True
    return False


def _display_entry_kind(body: str, helper: bool) -> str:
    if helper:
        return "helper"
    if body.startswith(("步行去", "骑行去", "高峰期步行去", "高峰期骑行去")):
        return "movement"
    if "吃饭" in body:
        return "meal"
    if body == "上课" or body.endswith("上课"):
        return "class"
    return "task"


def _is_plan_body_line(line: str) -> bool:
    """判断是否为“实际计划时间表”行（区别于 opening / 本轮更新 / 小空档 / closing）。"""
    if (
        line.startswith("现在：")
        or line.startswith("现在暂时没有安排")
        or line.startswith("稍后再安排：")
    ):
        return True
    return bool(_PLAN_TIME_RE.match(line))


def _bold_plan_time(line: str) -> str:
    """Streamlit 层轻量强调：加粗行首时间，让人一眼扫到“几点 → 做什么”。"""
    match = re.match(r"^(\d{1,2}:\d{2}(?:–\d{1,2}:\d{2})?起?)", line)
    if match is None:
        return line
    return "**{}**{}".format(match.group(1), line[match.end():])


_CURRENT_PLAN_CSS = CAMPUSFLOW_THEME_CSS


def render_page_streamlit(
    st, turn: P2SessionTurn, extra_questions=(), saved_snapshot=False,
    default_walk_hint=False, part="all",
) -> None:
    """Render the persisted plan as an action workspace and continuous timeline.

    This is intentionally presentation-only: ``CurrentPlanDisplay`` consumes
    the same final turn facts as the text fallback, and cannot alter routing,
    allocation, confirmations, or companion-call budgets.
    """
    display = build_current_plan_display(turn)
    if saved_snapshot:
        display = _as_saved_plan_display(display, turn)
    questions = () if saved_snapshot else _pending_questions(turn, extra_questions)
    if part not in ("all", "focus", "details", "timeline", "summary"):
        raise ValueError("Unknown plan presentation part")
    st.markdown(_CURRENT_PLAN_CSS, unsafe_allow_html=True)
    provisional_reason = "" if saved_snapshot else _provisional_reason(display, questions)
    provisional = bool(provisional_reason)
    if part in ("all", "focus"):
        hero = _hero_html(
            display, saved_snapshot=saved_snapshot, provisional=provisional,
            question=provisional_reason, turn=turn,
        )
        side = "" if saved_snapshot else _side_card_html(turn)
        st.markdown('<div class="cf-plan-grid">{}{}</div>'.format(hero, side)
                    if side else hero, unsafe_allow_html=True)
    if part == "focus":
        return

    # The reliable current action is already the hero. Repeating the same row
    # at the top of the full timeline made the page feel like two plans.
    timeline_entries = display.entries
    if part != "timeline" and not provisional and not saved_snapshot:
        timeline_entries = tuple(item for item in display.entries if not item.is_current)
    if part == "timeline":
        st.markdown(
            '<div class="cf-plan-grid">{}{}</div>'.format(
                _timeline_card_html(
                    timeline_entries, default_walk_hint=default_walk_hint
                ),
                _rhythm_html(turn),
            ),
            unsafe_allow_html=True,
        )
    elif part == "summary":
        st.markdown(_timeline_card_html(timeline_entries, default_walk_hint=default_walk_hint, compact=True), unsafe_allow_html=True)
    else:
        _render_timeline_card(st, timeline_entries, default_walk_hint=default_walk_hint)

    # Preserve the verified companion explanation without letting it compete
    # with the current action.  Native details keeps it keyboard accessible
    # and collapsed in the normal action-first view.
    if display.opening and not saved_snapshot:
        st.markdown(
            '<details class="cf-plan-explanation"><summary>安排依据</summary>'
            '<div>{}</div></details>'.format(
                html.escape(sanitize_user_facing_text(display.opening))
            ),
            unsafe_allow_html=True,
        )

    if display.closing and part not in ("summary", "timeline"):
        st.markdown(
            '<div class="cf-plan-closing">{}</div>'.format(
                html.escape(sanitize_user_facing_text(display.closing))
            ),
            unsafe_allow_html=True,
        )


def _pending_questions(turn, extra_questions):
    from src.p4_execution_presentation import execution_confirmation_questions
    questions = filter_pending_questions(
        turn.all_questions
        + tuple(extra_questions)
        + execution_confirmation_questions(getattr(turn, "execution_context", None)),
        turn.result.updated_state,
        turn.movement_blocks,
    )
    if getattr(turn, "agent_intelligence", None) is None:
        return questions
    # P5 asks one high-information question in the existing confirmation card;
    # it never turns the page into a checklist of every unknown fact.
    smart = getattr(turn.agent_intelligence, "clarification", None)
    if smart is not None and smart.should_ask and smart.question in questions:
        return (smart.question,)
    return tuple(questions[:1])


def _provisional_reason(display, questions):
    """Return only an existing fact that blocks a useful current action."""
    if questions:
        return questions[0]
    if any(item.is_current for item in display.entries):
        return ""
    for line in (
        tuple(display.intro_notes) + tuple(display.side_notes)
        + ((display.closing,) if display.closing else ())
    ):
        if "任务时间不确定" in line:
            return line
    return ""


def _as_saved_plan_display(display, turn):
    """Remove present-tense claims from a recovered, non-replanned snapshot."""
    entries = []
    for item in display.entries:
        body = item.body
        time_text = item.time_text
        if item.is_current:
            time_text = "保存时"
            body = body.replace("现在：", "", 1).strip()
        entries.append(replace(item, time_text=time_text, body=body, is_current=False))
    from src.p4_execution_presentation import assumed_location_line
    assumption = assumed_location_line(getattr(turn, "execution_context", None))
    notes = tuple(note for note in display.intro_notes if note != assumption)
    return replace(display, entries=tuple(entries), intro_notes=notes)


def _split_focus_body(body):
    match = re.match(r"^(.*?)[，,]\s*(做到\s*\d{1,2}:\d{2}.*)$", body or "")
    return (match.group(1).strip(), match.group(2).strip()) if match else ((body or "").strip(), "")


def _hero_html(display, saved_snapshot=False, provisional=False, question="", turn=None):
    opening = html.escape(sanitize_user_facing_text(display.opening))
    visible_notes = tuple(
        note for note in display.intro_notes
        if not (provisional and question and note == question)
    )
    notes = "".join(
        '<div class="cf-plan-assumption">{}</div>'.format(
            html.escape(sanitize_user_facing_text(note))
        )
        for note in visible_notes
    )
    current = next((item for item in display.entries if item.is_current), None)
    current_index = display.entries.index(current) if current is not None else -1
    next_item = next((item for item in display.entries[current_index + 1:] if item.is_primary), None)
    if next_item is None:
        next_item = next((item for item in display.entries if item.is_primary and item is not current), None)

    if saved_snapshot:
        contents = '<div class="cf-plan-opening">{}</div>'.format(opening) if opening else ""
        title = "上次保存的方案"
    elif provisional:
        safe_question = html.escape(sanitize_user_facing_text(question))
        contents = '<div class="cf-focus-action">缺少必要信息</div>'
        if safe_question:
            contents += '<div class="cf-plan-opening">{}</div>'.format(safe_question)
        elif opening:
            contents += '<div class="cf-plan-opening">{}</div>'.format(opening)
        title = "待确认"
    else:
        action, until = _split_focus_body(current.body if current is not None else display.opening)
        contents = '<div class="cf-focus-main"><div><div class="cf-focus-action">{}</div>'.format(
            html.escape(sanitize_user_facing_text(action))
        )
        if until:
            contents += '<div class="cf-focus-until">{}</div>'.format(
                html.escape(sanitize_user_facing_text(until))
            )
        minutes = None
        if current is not None and turn is not None:
            match = re.search(r"做到\s*(\d{1,2}):(\d{2})", until)
            now = turn.result.updated_state.now
            if match:
                remaining = int(match.group(1)) * 60 + int(match.group(2)) - now.hour * 60 - now.minute
                if remaining > 0:
                    minutes = remaining
            location = getattr(getattr(getattr(turn, 'execution_context', None), 'current_location', None), 'location', None)
            if location is not None and current.kind not in ('movement', 'class'):
                contents += '<div class="cf-focus-location">{}</div>'.format(html.escape(location.display_name))
        contents += '</div>'
        if minutes is not None:
            contents += '<div class="cf-focus-minutes">{}<small>分钟 · 本段</small></div>'.format(minutes)
        contents += '</div>'
        if next_item is not None:
            contents += '<div class="cf-focus-next"><b>下一安排</b><span>{}　{}</span></div>'.format(
                html.escape(sanitize_user_facing_text(next_item.time_text)),
                html.escape(sanitize_user_facing_text(next_item.body)),
            )
        title = "当前安排"
    return (
        '<section class="cf-current-plan cf-plan-hero{}">'
        '<div class="cf-plan-title"><span class="cf-plan-kicker">●</span>{}</div>{}{}'
        '</section>'
    ).format(
        ' cf-plan-provisional' if provisional and not saved_snapshot else '', title, contents, notes,
    )


def _render_timeline_card(st, entries, default_walk_hint=False):
    st.markdown(
        _timeline_card_html(entries, default_walk_hint=default_walk_hint),
        unsafe_allow_html=True,
    )


def _timeline_card_html(entries, default_walk_hint=False, compact=False):
    hint_pending = bool(default_walk_hint)
    chunks = []
    for index, item in enumerate(entries):
        if compact and index == 4:
            chunks.append('<details class="cf-plan-explanation"><summary>后续 {} 段安排</summary>'.format(len(entries) - 4))
        chunks.append(_timeline_entry_html(item))
        if hint_pending and item.kind == "movement" and item.body.startswith("步行"):
            chunks.append(
                '<div class="cf-walk-default-hint">默认按步行计算 · 可在个人设置中修改</div>'
            )
            hint_pending = False
    if compact and len(entries) > 4:
        chunks.append('</details>')
    contents = "".join(chunks)
    if not contents:
        contents = '<div class="cf-body">暂时还没有可展示的安排。</div>'
    return '<section class="cf-current-plan cf-timeline-card"><div class="cf-section-eyebrow">今天</div>{}</section>'.format(contents)


def _timeline_entry_html(item):
    body = _decorate_entry_body(item)
    if item.kind == "concurrent":
        if body.startswith("同时："):
            body = body[len("同时："):]
        return (
            '<div class="cf-concurrent"><span class="cf-time">{}</span>'
            '<div><span class="cf-concurrent-badge">同时</span>{}</div></div>'
        ).format(html.escape(item.time_text), body)
    if not item.is_primary:
        return (
            '<div class="cf-helper"><span class="cf-time">{}</span><div>{}</div></div>'
        ).format(html.escape(item.time_text), body)
    icon = {"movement": "›", "meal": "餐", "class": "课", "task": "•"}.get(item.kind, "•")
    extra = " cf-current" if item.is_current else ""
    if item.kind in ('class', 'fixed'):
        extra += ' cf-fixed'
    return (
        '<div class="cf-timeline-item{}"><div class="cf-node">{}</div>'
        '<div class="cf-time">{}</div><div class="cf-body">{}</div></div>'
    ).format(extra, icon, html.escape(item.time_text), body)


def _decorate_entry_body(item):
    """Escape final copy, then apply purely visual badges from final facts."""
    body = html.escape(item.body)
    if item.auto_selected_meal:
        marker = "【已为您选择就近食堂】"
        badge = '<span class="cf-pill cf-pill-meal">就近食堂</span>'
        body = body.replace(marker, badge)
        if badge not in body:
            body += badge
    if item.has_ai_estimate:
        marker = "（AI暂估）"
        body = body.replace(
            marker,
            '<span class="cf-pill cf-pill-ai">暂估</span>',
        )
    if item.has_building_arrival:
        body = _BUILDING_ARRIVAL_RE.sub(
            lambda match: '<span class="cf-pill cf-pill-arrival">{}</span>'.format(
                html.escape(match.group(1).lstrip("，"))
            ),
            body,
        )
    return body


def _render_side_cards(st, notes, turn):
    contents = _side_card_html(turn)
    if contents:
        st.markdown(contents, unsafe_allow_html=True)


def _side_card_html(turn):
    """Read only published commitments/movements; never call route planning."""
    state = turn.result.updated_state
    upcoming = sorted((c for c in state.commitments if c.starts_at and c.starts_at >= state.now), key=lambda c: c.starts_at)
    chunks = []
    if upcoming:
        fixed = upcoming[0]
        chunks.append('<div class="cf-side-title">下一固定安排</div><div class="cf-fixed-clock">{}</div><div class="cf-fixed-title">{}</div><div>{}</div>'.format(
            fixed.starts_at.strftime('%H:%M'), html.escape(fixed.title), html.escape(fixed.location_text or '地点待确认')))
        fixed_leg = next((b for b in getattr(turn, 'movement_blocks', ()) if b.destination_activity_ref == fixed.commitment_ref), None)
        if fixed_leg is not None:
            chunks.append('<div class="cf-fixed-departure">{} 出发 · {} 到达</div>'.format(fixed_leg.window_start.strftime('%H:%M'), fixed_leg.end_time.strftime('%H:%M')))
    movements = sorted((b for b in getattr(turn, 'movement_blocks', ()) if b.end_time > state.now), key=lambda b: b.window_start)
    if movements:
        block = movements[0]
        chunks.append('<div class="cf-route-summary"><div class="cf-side-title">校园移动</div><strong>{} › {}</strong><div>{} 出发 · 约 {} 分钟</div>{}</div>'.format(
            html.escape(block.origin_name), html.escape(block.destination_name), block.window_start.strftime('%H:%M'), block.estimated_minutes,
            '<div>{} 开始收拾 · {} 分钟准备</div>'.format(block.transition_start.strftime('%H:%M'), block.transition_minutes) if block.transition_start else ''))
    companion = getattr(turn, "companion_copy", None)
    lifestyle = sanitize_user_facing_text(getattr(companion, "lifestyle_hint", None)) if companion else ""
    if lifestyle:
        chunks.append('<details class="cf-side-hint"><summary>安排提示</summary><div>{}</div></details>'.format(
            html.escape(lifestyle)
        ))
    return '<aside class="cf-side-card">{}</aside>'.format(''.join(chunks)) if chunks else ''


def _rhythm_html(turn):
    plan = turn.result.allocation_plan
    movements = getattr(turn, 'movement_blocks', ())
    return '<aside class="cf-side-card"><div class="cf-side-title">当天安排概览</div><div class="cf-rhythm-stat"><b>{}</b>分钟已安排任务</div><div class="cf-rhythm-stat"><b>{}</b>段校园移动</div><div class="cf-rhythm-stat"><b>{}</b>项固定安排</div><div class="cf-side-hint">计划用时不代表已完成。进度以实际反馈为准。</div></aside>'.format(
        plan.total_planned_minutes, len(movements), len(turn.result.updated_state.commitments))


def _has_side_cards(notes, turn):
    return bool(_side_card_html(turn))


class DemoMockCaller:
    """离线演示 mock：把 5 个示例反馈映射为固定 Agent JSON（非真实模型）。"""

    def __init__(self):
        self.call_count = 0

    def __call__(self, system, user):
        self.call_count += 1
        if "feedback-router" in system:
            return _demo_route(user)
        if "commitment-reconciliation" in system:
            return _demo_commitment_proposal(user)
        if "task-reconciliation" in system:
            return _demo_task_proposal(user)
        if "day-plan-intent" in system:
            return _PLAN_EMPTY
        if "day-review" in system:
            return _REVIEW_ACCEPT
        return _TASK_NONE


def _is_resume_phrase(user_text):
    return any(
        phrase in user_text
        for phrase in ("又想背单词", "还是背单词", "把背单词加回来", "单词还是要背")
    )


def _demo_route(user_text):
    if "又做了30分钟" in user_text or "不背单词" in user_text or _is_resume_phrase(user_text):
        return _ROUTER_TASK
    if "晚了" in user_text or "推迟" in user_text or "临时有个组会" in user_text or "取消了" in user_text:
        return _ROUTER_COMMITMENT
    return _ROUTER_UNKNOWN


def _demo_commitment_proposal(user_text):
    if "晚了" in user_text or "推迟" in user_text:
        return _COMMITMENT_DELAY
    if "临时有个组会" in user_text:
        return _COMMITMENT_ADD
    if "取消了" in user_text:
        return _COMMITMENT_CANCEL
    return _COMMITMENT_NONE


def _demo_task_proposal(user_text):
    if "又做了30分钟" in user_text:
        return _TASK_PROGRESS_30
    if "不背单词" in user_text:
        return _TASK_SKIP
    if _is_resume_phrase(user_text):
        return _TASK_RESUME
    return _TASK_NONE


def main(st=None):
    if st is None:
        import streamlit as st
    st.set_page_config(page_title="CampusFlow P2 全天规划")
    if "p2_session" not in st.session_state:
        st.session_state["p2_session"] = P2SessionController(st.session_state, DemoMockCaller())
    session = st.session_state["p2_session"]
    st.title("CampusFlow P2 全天规划")
    st.info("当前为离线 mock 演示，未连接真实 TJU API。")
    with st.form("p2_feedback_form"):
        feedback = st.text_input(
            "反馈（例如：我刚又做了30分钟实验 / 下课晚了20分钟 / 14点临时有个组会，大概1小时）",
            key=P2_INPUT_KEY,
        )
        submitted = st.form_submit_button("应用反馈并重新规划")
    if submitted and str(feedback).strip():
        session.apply_feedback(str(feedback).strip())
        _rerun(st)
        return
    if st.button("开始全天计划", key="p2_start"):
        session.start_day(build_demo_state())
        _rerun(st)
        return
    if st.button("刷新方案", key="p2_refresh"):
        session.refresh()
        _rerun(st)
        return
    turn = session.last_turn()
    if turn is None:
        st.write("点击“开始全天计划”开始。")
        return
    render_page_streamlit(st, turn)


def _rerun(st):
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


if __name__ == "__main__":
    main()
