"""Tolerant, bounded Agent fallback for P1 planning.

The fallback never changes strict P1 domain objects. It converts a small and
partially optional model response directly into the existing read-only view.
"""
import json
import re
from typing import Optional

from src.p1_agent_planning_models import (
    AgentPlanDraft,
    AgentPlanStatus,
    AgentPlanStep,
    AgentReview,
    P1AgentPlanningResult,
    ReviewDecision,
    has_actionable_plan,
)
from src.p1_view_models import (
    NoticeView,
    P1ResultView,
    PlanCardView,
    QuestionView,
    QuickOptionView,
    TaskStepView,
)
from src.p1_planning_diagnostics import (
    DiagnosticStage,
    DiagnosticStatus,
    SafeDiagnostic,
    SafeErrorCategory,
    safe_error_category,
)


_SENSITIVE = (
    "authorization", "bearer", "api key", "api_key", "api-key", "traceback",
    "http://", "https://", "object at 0x", "task_ref", "commitment_ref",
)
_MAX_TEXT = 240
_MAX_STEPS = 8
_MAX_MINUTES = 1440
_MAX_WALKING = 600
_ACTION_PATTERN = re.compile(r"(先|现在|立即|开始|完成|整理|背|复习|阅读|处理|前往)")
_TIME_PATTERN = re.compile(r"(\d+\s*分钟|\d+\s*小时|半小时|一小时)")


class AgentOutputError(ValueError):
    pass


_PLAIN_ACTION_PATTERN = re.compile(
    r"现在|立即|开始|先|再|然后|完成|整理|背|复习|阅读|处理|前往|出发|预留")
_PLAIN_TIME_PATTERN = re.compile(
    r"(?:\d+\s*(?:分钟|分|小时)|半小时|一小时|\d+\s*[～~\-至到]\s*\d+\s*分钟|提前\s*出发|准备出发)")
_NOT_A_PLAN_TEXT = (
    "方案待补充信息", "已审查方案整理完成", "请稍后重试", "暂时无法完成规划",
    "需要补充信息", "路线尚未验证",
)


def _safe_text(value, limit=_MAX_TEXT):
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().split())
    if not text or any(token in text.casefold() for token in _SENSITIVE):
        return None
    return text[:limit]


def _safe_plan_text(value, limit=800):
    """Keep a concise human-readable plan without accepting sensitive echoes."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or any(token in text.casefold() for token in _SENSITIVE):
        return None
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:limit]


def _extract_object(raw):
    if not isinstance(raw, str) or len(raw) > 30000:
        raise AgentOutputError("invalid output")
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", raw):
        try:
            value, _ = decoder.raw_decode(raw[match.start():])
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    raise AgentOutputError("no object")


def _optional_minutes(value, maximum):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AgentOutputError("invalid minutes")
    if value < 0 or value > maximum:
        raise AgentOutputError("invalid minutes")
    return value


def _string_list(value, limit=8):
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > limit:
        raise AgentOutputError("invalid list")
    result = []
    for item in value:
        text = _safe_text(item)
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _plain_plan_text(raw, allow_direct=False):
    """Accept ordinary Chinese guidance; JSON is optional only for compatibility."""
    text = _safe_plan_text(raw)
    if text:
        text = re.sub(r"^```(?:json|markdown|text)?\s*", "", text,
                      flags=re.IGNORECASE).replace("```", "").strip()
    if not text or any(marker in text for marker in _NOT_A_PLAN_TEXT):
        return None
    if allow_direct and len(text) >= 8:
        return text
    if _PLAIN_ACTION_PATTERN.search(text) and _PLAIN_TIME_PATTERN.search(text):
        return text
    return None


def parse_agent_plan(raw):
    """Parse a deliberately small schema; harmless unknown keys are ignored."""
    value = _extract_object(raw)
    status_value = value.get("status", "proposed")
    try:
        status = AgentPlanStatus(status_value)
    except (TypeError, ValueError):
        raise AgentOutputError("invalid status")
    raw_steps = value.get("steps", [])
    if raw_steps is None:
        raw_steps = []
    if not isinstance(raw_steps, list) or len(raw_steps) > _MAX_STEPS:
        raise AgentOutputError("invalid steps")
    steps = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            raise AgentOutputError("invalid step")
        title = _safe_text(raw_step.get("title"), 80)
        minutes = raw_step.get("minutes")
        if not title or isinstance(minutes, bool) or not isinstance(minutes, int):
            raise AgentOutputError("invalid step")
        if minutes <= 0 or minutes > _MAX_MINUTES:
            raise AgentOutputError("invalid step")
        steps.append(AgentPlanStep(
            title,
            minutes,
            _safe_text(raw_step.get("location_text"), 100),
            _safe_text(raw_step.get("timing_note"), 160),
        ))
    walk_min = _optional_minutes(value.get("estimated_walking_minutes_min"), _MAX_WALKING)
    walk_max = _optional_minutes(value.get("estimated_walking_minutes_max"), _MAX_WALKING)
    if (walk_min is None) != (walk_max is None) or (
            walk_min is not None and walk_max is not None and walk_min > walk_max):
        raise AgentOutputError("invalid walking range")
    plan_text = _safe_text(value.get("plan_text"), 800)
    if status is AgentPlanStatus.PROPOSED and not steps and not _plain_plan_text(plan_text):
        status = AgentPlanStatus.NEEDS_INFORMATION
    return AgentPlanDraft(
        status,
        _safe_text(value.get("headline")),
        _plain_plan_text(plan_text),
        tuple(steps),
        walk_min,
        walk_max,
        _string_list(value.get("assumptions")),
        _string_list(value.get("warnings")),
        _safe_text(value.get("question")),
        _string_list(value.get("quick_options"), 4),
    )


def parse_agent_output(raw):
    """Prefer a safe ordinary-Chinese plan; retain old small JSON compatibility."""
    if not isinstance(raw, str):
        raise AgentOutputError("invalid output")
    stripped = raw.lstrip()
    if not stripped.startswith("{"):
        plan_text = _plain_plan_text(raw)
        if plan_text:
            return AgentPlanDraft(
                AgentPlanStatus.PROPOSED, None, plan_text, (), None, None,
                (), (), None, ())
    try:
        return parse_agent_plan(raw)
    except AgentOutputError:
        plan_text = _plain_plan_text(raw)
        if plan_text:
            return AgentPlanDraft(
                AgentPlanStatus.PROPOSED, None, plan_text, (), None, None,
                (), (), None, ())
        raise


def parse_agent_review(raw):
    if isinstance(raw, str):
        plain = _safe_text(raw, 300)
        if plain and not plain.lstrip().startswith("{"):
            if any(word in plain for word in ("修改", "修订", "调整", "不建议")):
                return AgentReview(ReviewDecision.REVISE, plain)
            return AgentReview(ReviewDecision.ACCEPT, plain)
    try:
        value = _extract_object(raw)
        decision = ReviewDecision(value.get("decision"))
        return AgentReview(decision, _safe_text(value.get("feedback")))
    except (AgentOutputError, TypeError, ValueError):
        # The review call still happened; an unusable review must not discard a
        # useful, already-normalized draft.
        return AgentReview(ReviewDecision.ACCEPT, None)


def _draft_payload(draft):
    return {
        "status": draft.status.value,
        "headline": draft.headline,
        "plan_text": draft.plan_text,
        "steps": [
            {"title": item.title, "minutes": item.minutes,
             "location_text": item.location_text, "timing_note": item.timing_note}
            for item in draft.steps
        ],
        "estimated_walking_minutes_min": draft.estimated_walking_minutes_min,
        "estimated_walking_minutes_max": draft.estimated_walking_minutes_max,
        "assumptions": list(draft.assumptions),
        "warnings": list(draft.warnings),
        "question": draft.question,
        "quick_options": list(draft.quick_options),
    }


def _minimal_agent_context(context_payload):
    """Keep the natural-language fallback independent of strict-P1 internals."""
    context_payload = context_payload if isinstance(context_payload, dict) else {}
    structured = context_payload.get("successful_structured_information")
    structured = structured if isinstance(structured, dict) else {}
    tasks = []
    for task in structured.get("tasks", [])[:8]:
        if not isinstance(task, dict):
            continue
        tasks.append({
            "title": _safe_text(task.get("title"), 100),
            "estimated_total_minutes": task.get("estimated_total_minutes"),
            "minimum_slice_minutes": task.get("minimum_slice_minutes"),
            "is_splittable": task.get("is_splittable"),
            "location_requirement": task.get("location_requirement"),
            "location_text": _safe_text(task.get("location_text"), 120),
            "attention_required": task.get("attention_required"),
        })
    window = structured.get("window")
    if isinstance(window, dict):
        window = {
            "current_location": _safe_text(window.get("current_location_text"), 120),
            "effective_time_constraint": window.get("effective_time_constraint"),
            "arrival_deadline": window.get("arrival_deadline"),
            "next_commitment": (window.get("commitments") or [])[:1],
        }
    interaction = context_payload.get("interaction_context")
    return {
        "user_input": _safe_plan_text(context_payload.get("original_user_input"), 1200),
        "reference_datetime": context_payload.get("reference_datetime"),
        "understood_tasks": tasks,
        "time_window": window,
        "user_confirmation": interaction if isinstance(interaction, dict) else None,
        "route_data_trust": context_payload.get("route_data_trust"),
    }


def _generator_prompts(context_payload):
    system = """你是 CampusFlow 的校园碎片时间规划助手。请直接用自然中文给出现在可以执行的安排，不要返回 JSON、schema、字段名或内部引用。

必须包含：现在先做什么；每项做多少分钟；什么时候停止任务并准备出发。路线没有真实数据时，给出明确标记为“AI 暂估”的步行时间区间；不要因为路线未知而拒绝规划，也不要只说“需要补充信息”。

若完整任务较长但可拆分，请先安排一个合理片段。地点不在程序地图中仅表示路线数据未知，不代表用户没有提供地点。不要编造官方路线或真实校园数据。"""
    return system, "规划上下文：\n" + json.dumps(
        _minimal_agent_context(context_payload), ensure_ascii=False)


def _review_prompts(context_payload, draft):
    system = """你是 CampusFlow 方案审查员。只审查下面的中文方案是否明显违背已知时间线、是否把 AI 暂估冒充事实、是否重复询问用户已提供的信息。
如果可用，直接回复“可以继续”；如果必须修改，回复“需要修改：”加一条简短意见。不得重写、替换或清空方案正文。"""
    payload = {"context": _minimal_agent_context(context_payload),
               "plan_text": draft.plan_text, "steps": _draft_payload(draft)["steps"]}
    return system, json.dumps(payload, ensure_ascii=False)


def _revision_prompts(context_payload, draft, feedback):
    system, _ = _generator_prompts(context_payload)
    user = {"context": _minimal_agent_context(context_payload),
            "previous_plan": draft.plan_text or _draft_payload(draft)["steps"],
            "review_feedback": feedback or "请只做必要修正。"}
    return system + "\n这是唯一一次修订。保留原方案中已有的可执行安排；直接返回修订后的中文方案。", json.dumps(user, ensure_ascii=False)


def _rescue_prompts(context_payload):
    system, _ = _generator_prompts(context_payload)
    return (system + "\n上一条没有形成可用方案。不要分析缺什么；直接说现在做什么、做多久、何时出发或预留时间。至少给出一个任务安排。",
            "救援规划上下文：\n" + json.dumps(_minimal_agent_context(context_payload), ensure_ascii=False))


def _direct_answer_prompts(context_payload):
    """A deliberately minimal final content rescue, independent of strict schemas."""
    context_payload = context_payload if isinstance(context_payload, dict) else {}
    system = """你是大学生碎片时间规划助手。直接给出可立即执行的中文计划，不要返回 JSON，不要分析缺少字段。路线未知时可以给出明确标记的 AI 暂估区间，但不得冒充官方数据。"""
    user = {
        "user_input": _safe_plan_text(context_payload.get("original_user_input"), 1200),
        "reference_datetime": context_payload.get("reference_datetime"),
    }
    return system, json.dumps(user, ensure_ascii=False)


def _presenter_prompts(draft):
    system = """把已经审查的方案整理成一句自然、简短的中文 headline。不得添加新事实、路线数字或任务。
可以返回普通中文文本，也可以返回 {"headline":"..."}。"""
    return system, json.dumps(_draft_payload(draft), ensure_ascii=False)


def _presented_headline(raw, draft):
    text = _safe_text(raw)
    if text and not text.startswith("{"):
        return text
    try:
        value = _extract_object(raw)
        text = _safe_text(value.get("headline"))
        if text:
            return text
    except AgentOutputError:
        pass
    if draft.headline:
        return draft.headline
    parts = ["%s %d 分钟" % (step.title, step.minutes) for step in draft.steps]
    if not parts:
        return ("还需要一点信息，我就能继续安排。"
                if draft.status is AgentPlanStatus.NEEDS_INFORMATION
                else "这段时间暂时没有识别到可执行任务。")
    if len(parts) == 1:
        return "建议先做%s。" % parts[0]
    return "建议先%s，然后%s。" % (parts[0], "，再".join(parts[1:]))


def _context_estimate_question(context_payload):
    structured = context_payload.get("successful_structured_information")
    interaction = context_payload.get("interaction_context")
    confirmed_field = None
    confirmed_label = None
    if isinstance(interaction, dict) and isinstance(interaction.get("confirmation"), dict):
        confirmed_field = interaction["confirmation"].get("field_name")
        confirmed_label = interaction["confirmation"].get("target_label")
    tasks = structured.get("tasks", []) if isinstance(structured, dict) else []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        minutes = task.get("estimated_total_minutes")
        confirmations = task.get("needs_confirmation", [])
        title = _safe_text(task.get("title"), 100)
        if (isinstance(minutes, int) and "estimated_total_minutes" in confirmations and title
                and not (confirmed_field == "estimated_total_minutes" and confirmed_label == title)):
            metadata = {
                "target_ref": task.get("task_ref"),
                "field_name": "estimated_total_minutes",
                "target_label": title,
            }
            return QuestionView(
                "“%s”完整时长目前按 %d 分钟估计（AI 暂估）。是否按这个估计继续？" % (title, minutes),
                (
                    QuickOptionView("就按这个", "confirm_estimate", str(minutes), **metadata),
                    QuickOptionView("调整时长", "adjust_duration", str(minutes), **metadata),
                ),
            )
    return None


def _view(draft, presenter_output=None, context_payload=None):
    headline = _presented_headline(presenter_output or "", draft)
    steps = tuple(TaskStepView(
        item.title, item.minutes, "当前空档", item.timing_note) for item in draft.steps)
    walking_notice = None
    if draft.estimated_walking_minutes_min is not None:
        walking_notice = NoticeView(
            "AI 暂估步行约 %d～%d 分钟，尚未使用真实天津大学路线数据。" % (
                draft.estimated_walking_minutes_min, draft.estimated_walking_minutes_max),
            "warning",
        )
    else:
        walking_notice = NoticeView("路线尚未使用真实天津大学路线数据，请预留额外时间。", "warning")
    question = None
    if draft.question:
        options = tuple(QuickOptionView(label, "agent_choice", label) for label in draft.quick_options)
        question = QuestionView(draft.question, options)
    if question is None and isinstance(context_payload, dict):
        question = _context_estimate_question(context_payload)
    card = None
    if has_actionable_plan(draft):
        task_minutes = sum(item.minutes for item in draft.steps) if draft.steps else None
        card = PlanCardView(
            "当前方案", steps, task_minutes, task_minutes, None, None, (),
            draft.plan_text or draft.headline, draft.assumptions, draft.warnings, True,
        )
    return P1ResultView(
        "当前方案", headline, card, None, None,
        NoticeView("我先按现有信息给你安排，确认后会更准确。", "gentle"),
        walking_notice, question, (), (),
        ("任务内容：根据你的描述", "未知任务属性与路线：AI 暂估"),
        bool(card or draft.assumptions or draft.warnings),
    )


def safe_failure_view():
    return P1ResultView(
        "暂时无法完成规划", "这次规划暂时没有完成，请稍后再试。", None, None,
        None, None, None, None, (), (), (), False,
    )


def _draft_from_direct_answer(raw):
    text = _plain_plan_text(raw, allow_direct=True)
    if not text:
        raise AgentOutputError("direct answer is empty")
    return AgentPlanDraft(
        AgentPlanStatus.PROPOSED, None, text, (), None, None,
        (), (), None, ())


def _completed_agent_result(draft, view, review, calls, revised, _summary, diagnostics):
    route_notice = (
        "AI 暂估步行时间区间，尚未使用真实天津大学路线数据。"
        if draft.estimated_walking_minutes_min is not None
        else "路线尚未使用真实天津大学路线数据，请预留额外时间。"
    )
    return P1AgentPlanningResult(
        draft.status, draft, view, calls, revised, "已生成 AI 暂定建议。",
        diagnostics, review.decision, review.feedback, True, route_notice)


def run_p1_agent_planning(context_payload, plan_generator, plan_reviewer,
                          plan_reviser, plan_presenter, max_calls=4):
    """Run generator + reviewer + optional one revision + presenter."""
    calls = 0
    diagnostics = []
    if max_calls < 3:
        diagnostics.append(SafeDiagnostic(
            DiagnosticStage.AGENT_GENERATOR, DiagnosticStatus.BUDGET_EXHAUSTED,
            0, calls))
        return P1AgentPlanningResult(AgentPlanStatus.FAILED, None, None, calls, False,
                                     "模型调用预算不足。", tuple(diagnostics))
    draft = None
    try:
        system, user = _generator_prompts(context_payload)
        calls += 1
        raw = plan_generator(system, user)
        if not isinstance(raw, str) or not raw.strip():
            diagnostics.append(SafeDiagnostic(
                DiagnosticStage.AGENT_GENERATOR, DiagnosticStatus.EMPTY_OUTPUT, 1, calls,
                SafeErrorCategory.INVALID_RESPONSE))
        else:
            draft = parse_agent_output(raw)
            diagnostics.append(SafeDiagnostic(
                DiagnosticStage.AGENT_GENERATOR,
                DiagnosticStatus.SUCCESS if has_actionable_plan(draft) else DiagnosticStatus.NOT_ACTIONABLE,
                1, calls))
    except AgentOutputError:
        diagnostics.append(SafeDiagnostic(
            DiagnosticStage.AGENT_GENERATOR, DiagnosticStatus.PARSE_FAILED, 1, calls,
            SafeErrorCategory.INVALID_RESPONSE))
        draft = None
    except Exception as exc:
        diagnostics.append(SafeDiagnostic(
            DiagnosticStage.AGENT_GENERATOR, DiagnosticStatus.CALL_FAILED, 1, calls,
            safe_error_category(exc)))
        draft = None
    if not has_actionable_plan(draft) and calls < max_calls - 1:
        try:
            system, user = _rescue_prompts(context_payload)
            calls += 1
            raw = plan_generator(system, user)
            if not isinstance(raw, str) or not raw.strip():
                diagnostics.append(SafeDiagnostic(
                    DiagnosticStage.AGENT_RESCUE_GENERATOR, DiagnosticStatus.EMPTY_OUTPUT,
                    1, calls, SafeErrorCategory.INVALID_RESPONSE))
            else:
                rescue_draft = parse_agent_output(raw)
                if has_actionable_plan(rescue_draft):
                    draft = rescue_draft
                diagnostics.append(SafeDiagnostic(
                    DiagnosticStage.AGENT_RESCUE_GENERATOR,
                    DiagnosticStatus.SUCCESS if has_actionable_plan(rescue_draft)
                    else DiagnosticStatus.NOT_ACTIONABLE,
                    1, calls))
        except AgentOutputError:
            diagnostics.append(SafeDiagnostic(
                DiagnosticStage.AGENT_RESCUE_GENERATOR, DiagnosticStatus.PARSE_FAILED,
                1, calls, SafeErrorCategory.INVALID_RESPONSE))
        except Exception as exc:
            diagnostics.append(SafeDiagnostic(
                DiagnosticStage.AGENT_RESCUE_GENERATOR, DiagnosticStatus.CALL_FAILED,
                1, calls, safe_error_category(exc)))
    # A useful natural-language answer must not be discarded merely because it
    # does not resemble our optional compact JSON compatibility format.
    if not has_actionable_plan(draft) and calls < max_calls:
        try:
            system, user = _direct_answer_prompts(context_payload)
            calls += 1
            raw = plan_generator(system, user)
            draft = _draft_from_direct_answer(raw)
            diagnostics.append(SafeDiagnostic(
                DiagnosticStage.AGENT_DIRECT_ANSWER, DiagnosticStatus.SUCCESS,
                1, calls))
        except AgentOutputError:
            diagnostics.append(SafeDiagnostic(
                DiagnosticStage.AGENT_DIRECT_ANSWER, DiagnosticStatus.NOT_ACTIONABLE,
                1, calls, SafeErrorCategory.INVALID_RESPONSE))
        except Exception as exc:
            diagnostics.append(SafeDiagnostic(
                DiagnosticStage.AGENT_DIRECT_ANSWER, DiagnosticStatus.CALL_FAILED,
                1, calls, safe_error_category(exc)))
    if not has_actionable_plan(draft):
        diagnostics.append(SafeDiagnostic(
            DiagnosticStage.FINAL_VIEW, DiagnosticStatus.NOT_ACTIONABLE, 0, calls))
        return P1AgentPlanningResult(AgentPlanStatus.FAILED, draft, None, calls, False,
                                     "兜底规划未形成可执行方案。", tuple(diagnostics))
    latest_actionable = draft
    # Direct answer may consume the final slot.  It is already a usable plan,
    # so render it deterministically rather than exceed the global budget just
    # to obtain a review sentence.
    if calls >= max_calls:
        review = AgentReview(ReviewDecision.ACCEPT, None)
        try:
            view = _view(draft, None, context_payload)
            diagnostics.append(SafeDiagnostic(
                DiagnosticStage.FINAL_VIEW, DiagnosticStatus.SUCCESS, 1, calls))
        except Exception:
            view = None
            diagnostics.append(SafeDiagnostic(
                DiagnosticStage.FINAL_VIEW, DiagnosticStatus.INTERNAL_ERROR, 1, calls,
                SafeErrorCategory.INTERNAL))
        return _completed_agent_result(draft, view, review, calls, False,
                                       "budget complete", tuple(diagnostics))
    system, user = _review_prompts(context_payload, draft)
    try:
        calls += 1
        raw = plan_reviewer(system, user)
        if not isinstance(raw, str) or not raw.strip():
            diagnostics.append(SafeDiagnostic(
                DiagnosticStage.AGENT_REVIEWER, DiagnosticStatus.EMPTY_OUTPUT, 1, calls,
                SafeErrorCategory.INVALID_RESPONSE))
            review = AgentReview(ReviewDecision.ACCEPT, None)
        else:
            review = parse_agent_review(raw)
            diagnostics.append(SafeDiagnostic(
                DiagnosticStage.AGENT_REVIEWER, DiagnosticStatus.SUCCESS, 1, calls))
    except Exception as exc:
        diagnostics.append(SafeDiagnostic(
            DiagnosticStage.AGENT_REVIEWER, DiagnosticStatus.CALL_FAILED, 1, calls,
            safe_error_category(exc)))
        review = AgentReview(ReviewDecision.ACCEPT, None)
    revised = False
    if review.decision is ReviewDecision.REVISE and calls < max_calls - 1:
        system, user = _revision_prompts(context_payload, draft, review.feedback)
        try:
            calls += 1
            raw = plan_reviser(system, user)
            revised_draft = parse_agent_output(raw)
            if has_actionable_plan(revised_draft):
                draft = revised_draft
                latest_actionable = revised_draft
                revised = True
                status = DiagnosticStatus.SUCCESS
            else:
                status = DiagnosticStatus.NOT_ACTIONABLE
            diagnostics.append(SafeDiagnostic(
                DiagnosticStage.AGENT_REVISER, status, 1, calls))
        except AgentOutputError:
            diagnostics.append(SafeDiagnostic(
                DiagnosticStage.AGENT_REVISER, DiagnosticStatus.PARSE_FAILED,
                1, calls, SafeErrorCategory.INVALID_RESPONSE))
        except Exception as exc:
            diagnostics.append(SafeDiagnostic(
                DiagnosticStage.AGENT_REVISER, DiagnosticStatus.CALL_FAILED,
                1, calls, safe_error_category(exc)))
    presenter_output = None
    if calls < max_calls:
        system, user = _presenter_prompts(draft)
        try:
            calls += 1
            presenter_output = plan_presenter(system, user)
            status = (DiagnosticStatus.SUCCESS if isinstance(presenter_output, str)
                      and presenter_output.strip() else DiagnosticStatus.EMPTY_OUTPUT)
            diagnostics.append(SafeDiagnostic(
                DiagnosticStage.AGENT_PRESENTER, status, 1, calls,
                None if status is DiagnosticStatus.SUCCESS else SafeErrorCategory.INVALID_RESPONSE))
        except Exception as exc:
            diagnostics.append(SafeDiagnostic(
                DiagnosticStage.AGENT_PRESENTER, DiagnosticStatus.CALL_FAILED,
                1, calls, safe_error_category(exc)))
            presenter_output = None
    draft = latest_actionable
    try:
        view = _view(draft, presenter_output, context_payload)
        diagnostics.append(SafeDiagnostic(
            DiagnosticStage.FINAL_VIEW, DiagnosticStatus.SUCCESS, 1, calls))
    except Exception:
        view = None
        diagnostics.append(SafeDiagnostic(
            DiagnosticStage.FINAL_VIEW, DiagnosticStatus.INTERNAL_ERROR, 1, calls,
            SafeErrorCategory.INTERNAL))
    return _completed_agent_result(draft, view, review,
                                 calls, revised, "已生成 AI 暂定建议。", tuple(diagnostics))
