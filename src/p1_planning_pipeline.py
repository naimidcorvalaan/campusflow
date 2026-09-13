"""P1h：只编排 P1d、P1c 与 P1g 的完整离线控制器。"""
from src.p1_context_bundle import ExtractionStatus
from src.p1_intake_pipeline import run_p1_intake
from src.p1_planning_models import (P1PlanningResult, PlanningCallSummary,
                                    PlanningStatus)
from src.p1_recommendation_models import RecommendationStatus
from src.p1_route_models import RouteDataTrust
from src.p1_recommendation_pipeline import run_p1_recommendation


def _ai_fallback_question(bundle, candidate):
    if candidate is None or not candidate.uses_ai_estimates:
        return None
    priority = ("estimated_total_minutes", "minimum_slice_minutes", "location_requirement",
                "location_text", "attention_required")
    fields = list(candidate.ai_estimate_fields)
    fields.sort(key=lambda field: priority.index(field.field_name) if field.field_name in priority else len(priority))
    tasks = {task.task_ref: task for task in bundle.tasks}
    for field in fields:
        task = tasks.get(field.target_ref)
        if task is None:
            continue
        value = getattr(task.features, field.field_name, None)
        if field.field_name == "estimated_total_minutes" and isinstance(value, int):
            return "我暂时估计‘%s’需要 %d 分钟，这个时间合适吗？" % (task.title, value)
        if field.field_name == "minimum_slice_minutes" and isinstance(value, int):
            return "我暂时估计‘%s’至少需要连续 %d 分钟，这个安排合适吗？" % (task.title, value)
        if field.field_name in ("location_requirement", "location_text"):
            return "我暂时不确定‘%s’的地点要求，可以确认一下吗？" % task.title
        if field.field_name == "attention_required" and value is not None:
            return "我暂时估计‘%s’需要%s注意力，这个判断合适吗？" % (task.title, value.value)
        return "我对‘%s’的%s作了暂时估计，可以确认一下吗？" % (task.title, field.field_name)
    return None


def _questions(bundle, supplement=None, candidate=None):
    values = []
    if bundle.primary_question is not None:
        values.append(bundle.primary_question.question)
    for question in bundle.other_questions:
        values.append(question.question)
    if supplement:
        values.append(supplement)
    if not values:
        fallback = _ai_fallback_question(bundle, candidate)
        if fallback:
            values.append(fallback)
    unique = []
    for item in values:
        if item and item not in unique:
            unique.append(item)
    return (unique[0] if unique else None, tuple(unique[1:]))


def _summary(intake, recommendation=None):
    candidate_calls = 0 if recommendation is None else recommendation.call_count
    regenerated = False if recommendation is None else recommendation.regenerated
    return PlanningCallSummary(intake.task_attempts, intake.window_attempts, candidate_calls, regenerated)


def _last_document(recommendation):
    if recommendation.second_candidate_result is not None and recommendation.second_candidate_result.document is not None:
        return recommendation.second_candidate_result.document
    return recommendation.first_candidate_result.document


def _tentative_validation(recommendation, candidate):
    for selection in (recommendation.second_route_selection, recommendation.first_route_selection):
        if selection is None:
            continue
        for validation in (selection.primary_validation, selection.alternative_validation):
            if validation is not None and validation.candidate is candidate:
                return validation
    return None


def _is_tentative(candidate, validation, bundle):
    if candidate is None:
        return False
    if candidate.is_tentative or candidate.uses_ai_estimates or bundle.user_confirmation_fields:
        return True
    return validation is None or validation.route_data_trust is not RouteDataTrust.REAL_VERIFIED


def _result(status, intake, recommendation=None, final=None, validation=None, tentative=None,
            is_tentative=False, summary="", actions=(), supplement=None):
    primary, others = _questions(intake.bundle, supplement, final or tentative)
    return P1PlanningResult(status, intake, intake.bundle, recommendation, final, validation,
                            tentative, is_tentative, primary, others,
                            intake.bundle.ai_estimated_task_fields, _summary(intake, recommendation),
                            summary, tuple(actions))


def run_p1_planning(user_text, reference_datetime, task_caller, window_caller,
                    candidate_caller, route_provider):
    """执行既有 P1d 和 P1g；控制器本身不新增任何模型调用。"""
    intake = run_p1_intake(user_text, reference_datetime, task_caller, window_caller)
    bundle = intake.bundle
    if bundle.extraction_status is ExtractionStatus.FAILED:
        return _result(
            PlanningStatus.EXTRACTION_FAILED,
            intake,
            summary=(bundle.primary_question.question if bundle.primary_question is not None
                     else "信息已经收到，但模型返回格式暂未通过校验，请重试。"),
        )
    if bundle.extraction_status is ExtractionStatus.PARTIAL:
        return _result(
            PlanningStatus.PARTIAL_EXTRACTION,
            intake,
            summary=(bundle.primary_question.question if bundle.primary_question is not None
                     else "部分模型输出未通过严格校验，请重试。"),
        )
    if not bundle.tasks:
        return _result(PlanningStatus.NO_EXECUTABLE_TASKS, intake,
                       summary="请告诉我这段时间想完成什么。")
    recommendation = run_p1_recommendation(bundle, candidate_caller, route_provider)
    if recommendation.final_candidate is not None:
        tentative = _is_tentative(recommendation.final_candidate,
                                  recommendation.final_route_validation, bundle)
        status = PlanningStatus.TENTATIVE_CANDIDATE if tentative else PlanningStatus.FINAL_CANDIDATE
        summary = ("这是暂定建议，路线或时间尚未完全验证。" if tentative
                   else recommendation.safe_summary)
        return _result(status, intake, recommendation, recommendation.final_candidate,
                       recommendation.final_route_validation, None, tentative, summary,
                       recommendation.quick_actions, recommendation.supplement_question)
    document = _last_document(recommendation)
    candidate = None if document is None else (document.primary_candidate or document.alternative_candidate)
    validation = _tentative_validation(recommendation, candidate)
    if candidate is not None and recommendation.status is RecommendationStatus.NEEDS_INFORMATION:
        return _result(PlanningStatus.TENTATIVE_CANDIDATE, intake, recommendation, None, validation,
                       candidate, True, "这是暂定建议，路线或时间尚未完全验证。",
                       recommendation.quick_actions, recommendation.supplement_question)
    if recommendation.status is RecommendationStatus.NO_EXECUTABLE_TASKS:
        return _result(PlanningStatus.NO_EXECUTABLE_TASKS, intake, recommendation,
                       summary=recommendation.safe_summary, actions=recommendation.quick_actions)
    if recommendation.status is RecommendationStatus.BOTH_CANDIDATES_INFEASIBLE:
        return _result(PlanningStatus.CANDIDATES_INFEASIBLE, intake, recommendation,
                       summary=recommendation.safe_summary, actions=recommendation.quick_actions)
    if recommendation.status in (RecommendationStatus.CALL_FAILED, RecommendationStatus.CANDIDATE_PARSE_FAILED):
        return _result(PlanningStatus.CANDIDATE_FAILED, intake, recommendation,
                       summary=recommendation.safe_summary, actions=recommendation.quick_actions)
    return _result(PlanningStatus.NEEDS_INFORMATION, intake, recommendation,
                   summary=recommendation.safe_summary, actions=recommendation.quick_actions,
                   supplement=recommendation.supplement_question)
