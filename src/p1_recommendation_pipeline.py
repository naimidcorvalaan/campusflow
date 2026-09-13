"""P1g：候选生成、确定性路线验证及最多一次受控重试。"""
import json

from src.p1_candidate_models import CandidateDecisionStatus
from src.p1_candidate_parser import parse_p1_candidate
from src.p1_candidate_prompt_builder import build_p1_candidate_prompt
from src.p1_candidate_extraction_models import P1CandidateParseResult
from src.p1_recommendation_models import (P1RecommendationResult, QuickAction,
                                          RecommendationStatus, SecondCallReason)
from src.p1_route_validator import select_validated_candidate


_ACTIONS = (QuickAction.EXTEND_TIME, QuickAction.CHANGE_TASK, QuickAction.UPDATE_LOCATION)
_SCHEMA_REMINDER = "上次输出未通过严格解析。请只返回符合既定 schema 的纯 JSON。"


def _failed_result():
    return P1CandidateParseResult("rejected", "call_error", "候选建议暂时无法生成。", None)


def _call(candidate_callable, system_prompt, user_prompt):
    try:
        value = candidate_callable(system_prompt, user_prompt)
    except Exception:
        return None
    return value if isinstance(value, str) else None


def _route_feedback(selection, bundle):
    def validation(value):
        if value is None:
            return None
        candidate = value.candidate
        steps = [{"task_ref": step.task_ref, "planned_minutes": step.planned_minutes,
                  "execution_context": step.execution_context.value,
                  "commitment_ref": step.commitment_ref} for step in candidate.steps]
        deadline = _time_limit(bundle)
        item = {
            "candidate_ref": candidate.candidate_ref,
            "steps": steps,
            "status": value.status.value,
            "reason": value.safe_reason,
            "walking_minutes": value.total_walking_minutes,
            "expected_finish_at": None if value.expected_finish_at is None else value.expected_finish_at.isoformat(),
            "time_limit": None if deadline is None else deadline.isoformat(),
            "route_data_trust": value.route_data_trust.value,
        }
        if value.status.value == "time_infeasible" and value.expected_finish_at is not None and deadline is not None:
            overrun = int((value.expected_finish_at - deadline).total_seconds() // 60)
            if overrun > 0:
                item["overrun_minutes"] = overrun
        return item
    data = {"primary": validation(selection.primary_validation), "alternative": validation(selection.alternative_validation),
            "time_boundary": None if _time_limit(bundle) is None else _time_limit(bundle).isoformat()}
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _time_limit(bundle):
    window = bundle.window_document
    if window is None:
        return None
    limits = [value for value in (window.effective_time_constraint, window.arrival_deadline) if value is not None]
    return min(limits) if limits else None


def _retry_user_prompt(base_prompt, reason, selection=None, bundle=None):
    if reason is SecondCallReason.SCHEMA_REPAIR:
        return base_prompt + "\n\n" + _SCHEMA_REMINDER
    return base_prompt + "\n\n程序路线验证反馈（仅基于当前数据）：" + _route_feedback(selection, bundle) + "\n请提出不同于失败候选的方案；不要重复原方案。"


def _result(status, first, first_selection, second, second_selection, calls, regenerated, reason, summary, candidate=None, validation=None, actions=(), supplement_question=None):
    return P1RecommendationResult(status, first, first_selection, second, second_selection,
                                  candidate, validation, calls, regenerated, reason, summary, tuple(actions), supplement_question)


def _selection_result(document, bundle, route_provider):
    return select_validated_candidate(document, bundle, route_provider)


def _complete_from_selection(first, first_selection, second, second_selection, calls, regenerated, reason):
    selection = second_selection if second_selection is not None else first_selection
    if selection.selected_candidate is not None:
        primary = selection.primary_validation
        status = RecommendationStatus.REGENERATED_SUCCESS if regenerated else (
            RecommendationStatus.SELECTED_PRIMARY if primary is not None and selection.selected_candidate is primary.candidate else RecommendationStatus.SELECTED_ALTERNATIVE)
        return _result(status, first, first_selection, second, second_selection, calls, regenerated, reason,
                       selection.safe_summary, selection.selected_candidate, selection.selected_validation)
    if selection.needs_regeneration:
        return _result(RecommendationStatus.BOTH_CANDIDATES_INFEASIBLE, first, first_selection, second, second_selection,
                       calls, regenerated, reason, "当前候选在已知时间与路线条件下都无法执行。", actions=_ACTIONS)
    return _result(RecommendationStatus.NEEDS_INFORMATION, first, first_selection, second, second_selection,
                   calls, regenerated, reason, selection.safe_summary, actions=_ACTIONS,
                   supplement_question=selection.supplement_question)


def _complete_document(first, first_selection, second, document, selection, calls, regenerated, reason):
    if document.decision_status is CandidateDecisionStatus.NO_EXECUTABLE_TASKS:
        return _result(RecommendationStatus.NO_EXECUTABLE_TASKS, first, first_selection, second, selection,
                       calls, regenerated, reason, selection.safe_summary)
    if document.decision_status is CandidateDecisionStatus.MISSING_INFORMATION:
        return _result(RecommendationStatus.NEEDS_INFORMATION, first, first_selection, second, selection,
                       calls, regenerated, reason, selection.safe_summary, actions=_ACTIONS,
                       supplement_question=selection.supplement_question)
    return _complete_from_selection(first, first_selection, second, selection, calls, regenerated, reason)


def run_p1_recommendation(bundle, candidate_callable, route_provider):
    """最多调用候选 callable 两次；不重新理解用户输入或修改 bundle。"""
    system_prompt, base_user_prompt = build_p1_candidate_prompt(bundle)
    first_raw = _call(candidate_callable, system_prompt, base_user_prompt)
    first = _failed_result() if first_raw is None else parse_p1_candidate(first_raw, bundle)
    calls = 1
    if first.document is None:
        retry = _call(candidate_callable, system_prompt, _retry_user_prompt(base_user_prompt, SecondCallReason.SCHEMA_REPAIR))
        second = _failed_result() if retry is None else parse_p1_candidate(retry, bundle)
        if second.document is None:
            status = RecommendationStatus.CALL_FAILED if retry is None else RecommendationStatus.CANDIDATE_PARSE_FAILED
            return _result(status, first, None, second, None, 2, False, SecondCallReason.SCHEMA_REPAIR, "候选建议暂时无法解析，请稍后重试。", actions=_ACTIONS)
        selection = _selection_result(second.document, bundle, route_provider)
        return _complete_document(first, None, second, second.document, selection, 2, False, SecondCallReason.SCHEMA_REPAIR)
    if first.document.decision_status is CandidateDecisionStatus.NO_EXECUTABLE_TASKS:
        selection = _selection_result(first.document, bundle, route_provider)
        return _result(RecommendationStatus.NO_EXECUTABLE_TASKS, first, selection, None, None, calls, False, None, selection.safe_summary)
    if first.document.decision_status is CandidateDecisionStatus.MISSING_INFORMATION:
        selection = _selection_result(first.document, bundle, route_provider)
        return _result(RecommendationStatus.NEEDS_INFORMATION, first, selection, None, None, calls, False, None, selection.safe_summary, actions=_ACTIONS,
                       supplement_question=selection.supplement_question)
    first_selection = _selection_result(first.document, bundle, route_provider)
    if not first_selection.needs_regeneration:
        return _complete_from_selection(first, first_selection, None, None, calls, False, None)
    retry = _call(candidate_callable, system_prompt, _retry_user_prompt(base_user_prompt, SecondCallReason.ROUTE_REGENERATION, first_selection, bundle))
    second = _failed_result() if retry is None else parse_p1_candidate(retry, bundle)
    if second.document is None:
        status = RecommendationStatus.CALL_FAILED if retry is None else RecommendationStatus.CANDIDATE_PARSE_FAILED
        return _result(status, first, first_selection, second, None, 2, True, SecondCallReason.ROUTE_REGENERATION, "重新生成的候选暂时无法解析。", actions=_ACTIONS)
    second_selection = _selection_result(second.document, bundle, route_provider)
    return _complete_document(first, first_selection, second, second.document, second_selection, 2, True, SecondCallReason.ROUTE_REGENERATION)
