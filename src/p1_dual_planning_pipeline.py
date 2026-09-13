"""Select strict P1 planning first, then a bounded Agent fallback when needed."""
from dataclasses import replace
from datetime import datetime

from src.p1_agent_planning_models import P1DualPlanningResult
from src.p1_agent_planning_pipeline import run_p1_agent_planning, safe_failure_view
from src.p1_planning_models import PlanningStatus
from src.p1_planning_pipeline import run_p1_planning
from src.p1_result_formatter import format_p1_result
from src.p1_route_models import CandidateValidationStatus
from src.p1_planning_diagnostics import (
    DiagnosticStage,
    DiagnosticStatus,
    SafeDiagnostic,
    SafeErrorCategory,
)
from src.p1_view_models import DiagnosticView


MAX_MODEL_CALLS_PER_ACTION = 10

_STAGE_LABELS = {
    DiagnosticStage.TASK_EXTRACTION: "任务理解",
    DiagnosticStage.WINDOW_EXTRACTION: "时间理解",
    DiagnosticStage.CANDIDATE_GENERATION: "候选方案生成",
    DiagnosticStage.AGENT_GENERATOR: "Agent方案生成",
    DiagnosticStage.AGENT_RESCUE_GENERATOR: "Agent救援生成",
    DiagnosticStage.AGENT_DIRECT_ANSWER: "Agent直接回答",
    DiagnosticStage.AGENT_REVIEWER: "Agent方案审查",
    DiagnosticStage.AGENT_REVISER: "Agent方案修订",
    DiagnosticStage.AGENT_PRESENTER: "Agent方案整理",
    DiagnosticStage.FINAL_VIEW: "最终展示",
}
_STATUS_LABELS = {
    DiagnosticStatus.NOT_STARTED: "未开始",
    DiagnosticStatus.SUCCESS: "成功",
    DiagnosticStatus.CALL_FAILED: "调用失败",
    DiagnosticStatus.EMPTY_OUTPUT: "返回为空",
    DiagnosticStatus.PARSE_FAILED: "解析失败",
    DiagnosticStatus.NOT_ACTIONABLE: "未形成可执行方案",
    DiagnosticStatus.BUDGET_EXHAUSTED: "调用预算已用尽",
    DiagnosticStatus.INTERNAL_ERROR: "内部处理失败",
    DiagnosticStatus.FALLBACK_STARTED: "已进入兜底",
    DiagnosticStatus.FALLBACK_COMPLETED: "兜底完成",
}
_ERROR_LABELS = {
    SafeErrorCategory.AUTHENTICATION: "认证",
    SafeErrorCategory.NETWORK: "网络",
    SafeErrorCategory.TIMEOUT: "超时",
    SafeErrorCategory.RATE_LIMIT: "限流",
    SafeErrorCategory.SERVER: "服务端",
    SafeErrorCategory.INVALID_RESPONSE: "返回格式",
    SafeErrorCategory.INTERNAL: "内部",
}


def _text(value, limit=300):
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _source(value):
    return getattr(value, "value", value)


def _safe_structured_context(result):
    bundle = result.context_bundle
    tasks = []
    for task in bundle.tasks:
        features = task.features
        tasks.append({
            "task_ref": task.task_ref,
            "title": _text(task.title, 100),
            "estimated_total_minutes": features.estimated_total_minutes,
            "minimum_slice_minutes": features.minimum_slice_minutes,
            "is_splittable": features.is_splittable,
            "location_requirement": _source(features.location_requirement),
            "location_text": _text(features.location_text, 120),
            "attention_required": _source(features.attention_required),
            "needs_confirmation": list(task.needs_confirmation),
        })
    window = bundle.window_document
    window_payload = None
    if window is not None:
        window_payload = {
            "current_datetime": window.current_context.current_datetime.isoformat(),
            "current_location_text": _text(window.current_context.current_location_text, 120),
            "effective_time_constraint": (
                None if window.effective_time_constraint is None
                else window.effective_time_constraint.isoformat()),
            "arrival_deadline": (
                None if window.arrival_deadline is None else window.arrival_deadline.isoformat()),
            "commitments": [
                {
                    "title": _text(item.title, 100),
                    "starts_at": None if item.starts_at is None else item.starts_at.isoformat(),
                    "ends_at": None if item.ends_at is None else item.ends_at.isoformat(),
                    "location_text": _text(item.location_text, 120),
                    "availability_during": _source(item.availability_during),
                }
                for item in window.commitments
            ],
        }
    validation = result.final_route_validation
    route_payload = None
    if validation is not None:
        route_payload = {
            "validation_status": validation.status.value,
            "route_data_trust": validation.route_data_trust.value,
            "total_walking_minutes": validation.total_walking_minutes,
            "free_window_task_minutes": validation.free_window_task_minutes,
            "safe_reason": _text(validation.safe_reason),
        }
    return {"tasks": tasks, "window": window_payload, "route_validation": route_payload}


def _safe_interaction(value):
    if not isinstance(value, dict):
        return None
    confirmation = value.get("confirmation")
    previous = value.get("previous_suggestion")
    safe = {}
    if isinstance(confirmation, dict):
        safe["confirmation"] = {
            "action": _text(confirmation.get("action"), 60),
            "field_name": _text(confirmation.get("field_name"), 80),
            "target_label": _text(confirmation.get("target_label"), 100),
            "selected_value": confirmation.get("selected_value"),
            "user_value": confirmation.get("user_value"),
        }
    if isinstance(previous, dict):
        safe["previous_suggestion"] = {
            "headline": _text(previous.get("headline")),
            "steps": previous.get("steps", [])[:8]
            if isinstance(previous.get("steps"), list) else [],
        }
    return safe or None


def _structured_call_count(result):
    summary = result.call_summary
    return summary.task_attempts + summary.window_attempts + summary.candidate_calls


def _parse_diagnostic(stage, parsed, attempts, call_count):
    if parsed is None:
        return SafeDiagnostic(stage, DiagnosticStatus.NOT_STARTED, attempts, call_count)
    if getattr(parsed, "document", None) is not None:
        return SafeDiagnostic(stage, DiagnosticStatus.SUCCESS, attempts, call_count)
    if getattr(parsed, "error_type", None) == "call_error":
        return SafeDiagnostic(stage, DiagnosticStatus.CALL_FAILED, attempts, call_count,
                              SafeErrorCategory.INTERNAL)
    return SafeDiagnostic(stage, DiagnosticStatus.PARSE_FAILED, attempts, call_count,
                          SafeErrorCategory.INVALID_RESPONSE)


def _strict_diagnostics(result, calls):
    if result is None:
        return (
            SafeDiagnostic(DiagnosticStage.TASK_EXTRACTION, DiagnosticStatus.INTERNAL_ERROR,
                           0, calls, SafeErrorCategory.INTERNAL),
            SafeDiagnostic(DiagnosticStage.WINDOW_EXTRACTION, DiagnosticStatus.INTERNAL_ERROR,
                           0, calls, SafeErrorCategory.INTERNAL),
        )
    summary = result.call_summary
    bundle = result.context_bundle
    records = [
        _parse_diagnostic(DiagnosticStage.TASK_EXTRACTION, getattr(bundle, "task_result", None),
                          summary.task_attempts, summary.task_attempts),
        _parse_diagnostic(DiagnosticStage.WINDOW_EXTRACTION, getattr(bundle, "window_result", None),
                          summary.window_attempts, summary.task_attempts + summary.window_attempts),
    ]
    recommendation = getattr(result, "recommendation_result", None)
    if recommendation is None:
        status = (DiagnosticStatus.NOT_STARTED if summary.candidate_calls == 0
                  else DiagnosticStatus.INTERNAL_ERROR)
        records.append(SafeDiagnostic(DiagnosticStage.CANDIDATE_GENERATION, status,
                                      summary.candidate_calls, calls,
                                      SafeErrorCategory.INTERNAL if status is DiagnosticStatus.INTERNAL_ERROR else None))
    elif recommendation.status.value == "call_failed":
        records.append(SafeDiagnostic(DiagnosticStage.CANDIDATE_GENERATION,
                                      DiagnosticStatus.CALL_FAILED, recommendation.call_count,
                                      calls, SafeErrorCategory.INTERNAL))
    elif recommendation.status.value == "candidate_parse_failed":
        records.append(SafeDiagnostic(DiagnosticStage.CANDIDATE_GENERATION,
                                      DiagnosticStatus.PARSE_FAILED, recommendation.call_count,
                                      calls, SafeErrorCategory.INVALID_RESPONSE))
    else:
        records.append(SafeDiagnostic(DiagnosticStage.CANDIDATE_GENERATION,
                                      DiagnosticStatus.SUCCESS, recommendation.call_count, calls))
    return tuple(records)


def _diagnostic_views(records):
    return tuple(DiagnosticView(
        _STAGE_LABELS[item.stage], _STATUS_LABELS[item.status],
        item.attempts, item.call_count,
        None if item.error_category is None else _ERROR_LABELS[item.error_category],
    ) for item in records)


def _view_with_diagnostics(view, records, total, final_failure):
    if not final_failure:
        return replace(view, model_call_count=total)
    return replace(view, diagnostics=_diagnostic_views(records), model_call_count=total)


def _interaction_is_reflected(result, interaction_context):
    if not isinstance(interaction_context, dict):
        return True
    confirmation = interaction_context.get("confirmation")
    if not isinstance(confirmation, dict):
        return True
    field = confirmation.get("field_name")
    target_ref = confirmation.get("target_ref")
    value = confirmation.get("user_value")
    if value in (None, ""):
        value = confirmation.get("selected_value")
    if field == "estimated_total_minutes":
        try:
            expected = int(value)
        except (TypeError, ValueError):
            return False
        return any(
            task.task_ref == target_ref and task.features.estimated_total_minutes == expected
            for task in result.context_bundle.tasks)
    window = result.context_bundle.window_document
    if field == "current_location_text" and window is not None:
        return window.current_context.current_location_text == value
    if field == "location_text":
        for task in result.context_bundle.tasks:
            if task.task_ref == target_ref:
                return task.features.location_text == value
    if field == "location_requirement":
        for task in result.context_bundle.tasks:
            if task.task_ref == target_ref:
                return task.features.location_requirement.value == value
    return False


def _should_fallback(result, interaction_context=None):
    if not _interaction_is_reflected(result, interaction_context):
        return True
    if result.status is PlanningStatus.FINAL_CANDIDATE:
        return False
    if result.status is PlanningStatus.TENTATIVE_CANDIDATE:
        validation = result.final_route_validation
        return validation is None or validation.status in (
            CandidateValidationStatus.LOCATION_UNRESOLVED,
            CandidateValidationStatus.ROUTE_DATA_INSUFFICIENT,
            CandidateValidationStatus.INSUFFICIENT_TIME_BOUNDARY,
        )
    return True


def _suppress_confirmed_question(view, interaction_context):
    if not isinstance(interaction_context, dict) or view.primary_question is None:
        return view
    confirmation = interaction_context.get("confirmation")
    if not isinstance(confirmation, dict):
        return view
    field = confirmation.get("field_name")
    target = confirmation.get("target_ref")
    if any(option.field_name == field and option.target_ref == target
           for option in view.primary_question.quick_options):
        return replace(view, primary_question=None)
    return view


def run_p1_dual_planning(
        user_text, reference_datetime, task_caller, window_caller, candidate_caller,
        route_provider, agent_caller=None, interaction_context=None,
        planning_function=run_p1_planning, formatter=format_p1_result,
        agent_function=run_p1_agent_planning):
    """Run strict planning, then use at most the remaining part of a 10-call budget."""
    if not isinstance(user_text, str) or not user_text.strip():
        raise ValueError("user_text must not be blank")
    if not isinstance(reference_datetime, datetime):
        raise TypeError("reference_datetime must be datetime")
    try:
        structured = planning_function(
            user_text, reference_datetime, task_caller, window_caller,
            candidate_caller, route_provider)
        structured_calls = _structured_call_count(structured)
    except Exception:
        # Use the strict channel's maximum as a conservative budget charge.
        structured = None
        structured_calls = 6
    if structured is not None and not _should_fallback(structured, interaction_context):
        view = _suppress_confirmed_question(formatter(structured), interaction_context)
        return P1DualPlanningResult(
            view, structured, None, False,
            structured_calls, structured_calls,
            _strict_diagnostics(structured, structured_calls))
    remaining = max(0, MAX_MODEL_CALLS_PER_ACTION - structured_calls)
    if agent_caller is None or remaining < 3:
        diagnostics = _strict_diagnostics(structured, structured_calls) + (
            SafeDiagnostic(DiagnosticStage.AGENT_GENERATOR,
                           DiagnosticStatus.BUDGET_EXHAUSTED if remaining < 3
                           else DiagnosticStatus.NOT_STARTED, 0, structured_calls),
            SafeDiagnostic(DiagnosticStage.FINAL_VIEW, DiagnosticStatus.INTERNAL_ERROR,
                           0, structured_calls, SafeErrorCategory.INTERNAL),
        )
        view = _view_with_diagnostics(safe_failure_view(), diagnostics, structured_calls, True)
        return P1DualPlanningResult(
            view, structured, None, True,
            structured_calls, structured_calls, diagnostics)
    context = {
        "original_user_input": user_text.strip(),
        "reference_datetime": reference_datetime.isoformat(),
        "successful_structured_information": (
            None if structured is None else _safe_structured_context(structured)),
        "interaction_context": _safe_interaction(interaction_context),
        "route_data_trust": getattr(route_provider.route_data_trust, "value", "unknown"),
    }
    strict_records = _strict_diagnostics(structured, structured_calls)
    agent = agent_function(
        context, agent_caller, agent_caller, agent_caller, agent_caller,
        # This is a fresh per-operation budget.  The Agent path normally uses
        # three calls, but an ordinary-text rescue/direct answer may safely
        # use the remaining slots without ever exceeding the global cap.
        max_calls=remaining)
    diagnostics = strict_records + (
        SafeDiagnostic(DiagnosticStage.AGENT_GENERATOR, DiagnosticStatus.FALLBACK_STARTED,
                       0, structured_calls),
    ) + agent.diagnostics
    total = structured_calls + agent.call_count
    if agent.view is not None:
        diagnostics += (SafeDiagnostic(DiagnosticStage.FINAL_VIEW,
                                       DiagnosticStatus.FALLBACK_COMPLETED, 1, total),)
        view = _view_with_diagnostics(agent.view, diagnostics, total, False)
    else:
        diagnostics += (SafeDiagnostic(DiagnosticStage.FINAL_VIEW,
                                       DiagnosticStatus.NOT_ACTIONABLE, 0, total),)
        view = _view_with_diagnostics(safe_failure_view(), diagnostics, total, True)
    return P1DualPlanningResult(view, structured, agent, True, structured_calls, total,
                                diagnostics)
