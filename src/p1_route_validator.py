"""P1f 确定性候选路线验证；不调用大模型。"""
from datetime import timedelta

from src.p1_candidate_models import CandidateDecisionStatus, ExecutionContext
from src.p1_context_bundle import P1ContextBundle
from src.p1_models import LocationRequirement
from src.p1_route_models import (CandidateRouteValidation, CandidateSelectionResult,
                                 CandidateValidationStatus, LocationResolutionStatus,
                                 ProviderRouteStatus, RouteDataTrust, RouteSegment)


def _trust(provider):
    try:
        return provider.route_data_trust
    except Exception:
        return RouteDataTrust.ESTIMATED_UNVERIFIED


def _result(candidate, status, provider, walking, total_minutes, free_minutes,
            finish, segments, reason, target_ref=None, field_name=None,
            target_label=None):
    return CandidateRouteValidation(candidate, status, _trust(provider), walking,
                                    total_minutes, free_minutes, finish,
                                    tuple(segments), reason, target_ref,
                                    field_name, target_label)


def _resolve(provider, location_text):
    try:
        return provider.resolve_location(location_text)
    except Exception:
        return None


def _route(provider, start_id, end_id):
    try:
        return provider.calculate_route(start_id, end_id)
    except Exception:
        return None


def _limit(document):
    values = [x for x in (document.effective_time_constraint, document.arrival_deadline) if x is not None]
    return min(values) if values else None


def validate_candidate(candidate, bundle, provider):
    total_minutes = sum(step.planned_minutes for step in candidate.steps)
    free_minutes = sum(
        step.planned_minutes for step in candidate.steps
        if step.execution_context is ExecutionContext.FREE_WINDOW)
    if not isinstance(bundle, P1ContextBundle) or bundle.window_document is None:
        return _result(candidate, CandidateValidationStatus.INSUFFICIENT_TIME_BOUNDARY, provider, None, total_minutes, free_minutes, None, (), "缺少可用于路线验证的时间窗口信息。")
    document = bundle.window_document
    if document.has_unresolved_commitment_start:
        return _result(candidate, CandidateValidationStatus.INSUFFICIENT_TIME_BOUNDARY, provider, None, total_minutes, free_minutes, None, (), "存在开始时间未确认的固定安排，暂不能验证路线。")
    deadline = _limit(document)
    if deadline is None:
        return _result(candidate, CandidateValidationStatus.INSUFFICIENT_TIME_BOUNDARY, provider, None, total_minutes, free_minutes, None, (), "缺少明确的时间边界，暂不能验证路线。")
    context = document.current_context
    task_by_ref = {task.task_ref: task for task in bundle.tasks}
    free_steps = [step for step in candidate.steps if step.execution_context is ExecutionContext.FREE_WINDOW]
    needs_movement = any(task_by_ref[step.task_ref].features.location_requirement is LocationRequirement.SPECIFIC_LOCATION for step in free_steps)
    next_item = document.earliest_known_commitment if document.next_commitment_is_confirmed else None
    if next_item is not None and next_item.location_text is not None:
        needs_movement = True
    current_id = None
    current_name = context.current_location_text
    if needs_movement:
        resolved = _resolve(provider, context.current_location_text)
        if resolved is None or resolved.status is LocationResolutionStatus.DATA_INSUFFICIENT:
            return _result(candidate, CandidateValidationStatus.ROUTE_DATA_INSUFFICIENT, provider, None, total_minutes, free_minutes, None, (), "路线数据暂不足，暂不能验证当前位置。")
        if resolved.status is not LocationResolutionStatus.RESOLVED:
            return _result(candidate, CandidateValidationStatus.LOCATION_UNRESOLVED, provider, None, total_minutes, free_minutes, None, (), "当前地点缺失或无法识别，暂不能验证路线。", "current_context", "current_location_text", "当前位置")
        current_id, current_name = resolved.location_id, resolved.display_name
    now, walking, segments = context.current_datetime, 0, []
    for step in candidate.steps:
        task = task_by_ref[step.task_ref]
        if step.execution_context is not ExecutionContext.FREE_WINDOW:
            continue
        features = task.features
        if features.location_requirement is LocationRequirement.LOCATION_REQUIREMENT_UNKNOWN:
            return _result(candidate, CandidateValidationStatus.LOCATION_UNRESOLVED, provider, None, total_minutes, free_minutes, None, segments, "任务地点要求尚未确认，暂不能验证路线。", task.task_ref, "location_requirement", task.title)
        if features.location_requirement is LocationRequirement.SPECIFIC_LOCATION:
            resolved = _resolve(provider, features.location_text)
            if resolved is None or resolved.status is LocationResolutionStatus.DATA_INSUFFICIENT:
                return _result(candidate, CandidateValidationStatus.ROUTE_DATA_INSUFFICIENT, provider, None, total_minutes, free_minutes, None, segments, "路线数据暂不足，暂不能验证任务地点。")
            if resolved.status is not LocationResolutionStatus.RESOLVED:
                return _result(candidate, CandidateValidationStatus.LOCATION_UNRESOLVED, provider, None, total_minutes, free_minutes, None, segments, "任务地点缺失或无法识别，暂不能验证路线。", task.task_ref, "location_text", task.title)
            route = _route(provider, current_id, resolved.location_id)
            if route is None or route.status is ProviderRouteStatus.DATA_INSUFFICIENT:
                return _result(candidate, CandidateValidationStatus.ROUTE_DATA_INSUFFICIENT, provider, None, total_minutes, free_minutes, None, segments, "路线数据暂不足，暂不能计算地点之间的步行。")
            if route.status is ProviderRouteStatus.UNREACHABLE:
                return _result(candidate, CandidateValidationStatus.ROUTE_UNREACHABLE, provider, None, total_minutes, free_minutes, None, segments, "地点之间的路线不可达。")
            walking += route.walking_minutes
            now += timedelta(minutes=route.walking_minutes)
            segments.append(RouteSegment(current_name, resolved.display_name, route.walking_minutes, route.path))
            current_id, current_name = resolved.location_id, resolved.display_name
        now += timedelta(minutes=step.planned_minutes)
    if next_item is not None:
        if next_item.location_text is None:
            return _result(candidate, CandidateValidationStatus.LOCATION_UNRESOLVED, provider, None, total_minutes, free_minutes, None, segments, "下一项固定安排地点缺失，暂不能验证路线。", next_item.commitment_ref, "location_text", next_item.title)
        resolved = _resolve(provider, next_item.location_text)
        if resolved is None or resolved.status is LocationResolutionStatus.DATA_INSUFFICIENT:
            return _result(candidate, CandidateValidationStatus.ROUTE_DATA_INSUFFICIENT, provider, None, total_minutes, free_minutes, None, segments, "路线数据暂不足，暂不能验证下一项固定安排地点。")
        if resolved.status is not LocationResolutionStatus.RESOLVED:
            return _result(candidate, CandidateValidationStatus.LOCATION_UNRESOLVED, provider, None, total_minutes, free_minutes, None, segments, "下一项固定安排地点无法识别，暂不能验证路线。", next_item.commitment_ref, "location_text", next_item.title)
        route = _route(provider, current_id, resolved.location_id)
        if route is None or route.status is ProviderRouteStatus.DATA_INSUFFICIENT:
            return _result(candidate, CandidateValidationStatus.ROUTE_DATA_INSUFFICIENT, provider, None, total_minutes, free_minutes, None, segments, "路线数据暂不足，暂不能计算前往下一项安排的步行。")
        if route.status is ProviderRouteStatus.UNREACHABLE:
            return _result(candidate, CandidateValidationStatus.ROUTE_UNREACHABLE, provider, None, total_minutes, free_minutes, None, segments, "前往下一项固定安排的路线不可达。")
        walking += route.walking_minutes
        now += timedelta(minutes=route.walking_minutes)
        segments.append(RouteSegment(current_name, resolved.display_name, route.walking_minutes, route.path))
    if now > deadline:
        return _result(candidate, CandidateValidationStatus.TIME_INFEASIBLE, provider, walking, total_minutes, free_minutes, now, segments, "按当前路线数据计算，完成后可能超过时间限制。")
    trust_note = "按测试数据计算可行，不能代表真实校园路线。" if provider.route_data_trust.value == "synthetic_test" else "按当前路线数据计算可行。"
    return _result(candidate, CandidateValidationStatus.FEASIBLE, provider, walking, total_minutes, free_minutes, now, segments, trust_note)


def select_validated_candidate(document, bundle, provider):
    if document.decision_status is CandidateDecisionStatus.NO_EXECUTABLE_TASKS:
        return CandidateSelectionResult(None, None, None, None, False, None, "当前没有可执行任务。")
    if document.decision_status is CandidateDecisionStatus.MISSING_INFORMATION:
        return CandidateSelectionResult(None, None, None, None, False, "请补充可用时间、地点或想完成的任务。", "信息不足，暂不进行路线验证。")
    primary = None if document.primary_candidate is None else validate_candidate(document.primary_candidate, bundle, provider)
    alternative = None if document.alternative_candidate is None else validate_candidate(document.alternative_candidate, bundle, provider)
    if primary is not None and primary.is_feasible:
        return CandidateSelectionResult(primary, alternative, primary.candidate, primary, False, None, "已保留首选方案。")
    if alternative is not None and alternative.is_feasible:
        return CandidateSelectionResult(primary, alternative, alternative.candidate, alternative, False, None, "原首选加上步行时间后可能来不及，已为你改用备选方案。")
    validations = [item for item in (primary, alternative) if item is not None]
    insufficient = any(item.status in (CandidateValidationStatus.LOCATION_UNRESOLVED, CandidateValidationStatus.INSUFFICIENT_TIME_BOUNDARY, CandidateValidationStatus.ROUTE_DATA_INSUFFICIENT) for item in validations)
    if insufficient:
        return CandidateSelectionResult(primary, alternative, None, None, False, "请补充当前位置、地点或可用时间后再验证。", "信息不足，暂不判断候选不可行。")
    return CandidateSelectionResult(primary, alternative, None, None, bool(validations), None, "现有候选均无法通过确定性验证，需要重新生成候选。")
