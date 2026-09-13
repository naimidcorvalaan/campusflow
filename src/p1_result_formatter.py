"""P1i：把 P1PlanningResult 纯确定性地转换为中文展示结构。"""
from datetime import datetime

from src.p1_candidate_models import ExecutionContext
from src.p1_planning_models import PlanningStatus
from src.p1_route_models import RouteDataTrust
from src.p1_view_models import (NoticeView, P1ResultView, PlanCardView,
                                QuestionView, QuickOptionView, RouteSegmentView,
                                TaskStepView)


_ACTION_LABELS = {"extend_time": "延长可用时间", "change_task": "换个任务", "update_location": "修改地点"}
_SOURCE_LABELS = {"ai_estimated": "AI 暂估", "ai_extracted_from_user_text": "根据你的描述",
                  "system_default": "系统默认", "system_verified": "程序确认",
                  "user_confirmed": "你已确认"}
_FORBIDDEN = ("traceback", "authorization", "bearer", "api key", "api_key", "api-key", "http://", "https://", "{", "[", "]", "object at 0x", "task_ref", "commitment_ref", "path=")


def _safe(value):
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if any(token in text.casefold() for token in _FORBIDDEN):
        return ""
    return text[:200]


def _time(value):
    return None if not isinstance(value, datetime) else value.strftime("%Y-%m-%d %H:%M")


def _task_map(result):
    return {task.task_ref: task for task in result.context_bundle.tasks}


def _steps(candidate, result):
    tasks = _task_map(result)
    values = []
    for step in candidate.steps:
        task = tasks.get(step.task_ref)
        if task is None:
            continue
        environment = "当前空档" if step.execution_context is ExecutionContext.FREE_WINDOW else "固定安排期间"
        values.append(TaskStepView(_safe(task.title) or "任务", step.planned_minutes, environment))
    return tuple(values)


def _headline(candidate, validation, result):
    steps = _steps(candidate, result)
    phrases = ["%s %d 分钟" % (step.title, step.planned_minutes) for step in steps]
    if not phrases:
        return "我已整理好当前建议。"
    text = "建议先" + phrases[0]
    for phrase in phrases[1:]:
        text += "，然后" + phrase
    window = result.context_bundle.window_document
    next_item = None if window is None or not window.next_commitment_is_confirmed else window.earliest_known_commitment
    if next_item is not None and next_item.location_text:
        text += "，然后前往" + _safe(next_item.location_text)
    return text + "。"


def _card(candidate, validation, result):
    steps = _steps(candidate, result)
    segments = () if validation is None else tuple(RouteSegmentView(_safe(item.start_location), _safe(item.end_location), item.walking_minutes) for item in validation.segments)
    return PlanCardView("当前方案", steps,
                        None if validation is None else validation.total_candidate_task_minutes,
                        None if validation is None else validation.free_window_task_minutes,
                        None if validation is None else validation.total_walking_minutes,
                        None if validation is None else _time(validation.expected_finish_at), segments,
                        _safe(candidate.rationale) or None,
                        tuple(item for item in (_safe(x) for x in candidate.assumptions) if item),
                        tuple(item for item in (_safe(x) for x in candidate.warnings) if item),
                        result.is_tentative)


def _route_notice(validation):
    if validation is None:
        return None
    trust = validation.route_data_trust
    if trust is RouteDataTrust.SYNTHETIC_TEST:
        return NoticeView("当前使用演示路线数据，不代表真实北洋园步行时间。", "warning")
    if trust is RouteDataTrust.ESTIMATED_UNVERIFIED:
        return NoticeView("当前步行时间为未核验估计，请预留额外时间。", "warning")
    return NoticeView("当前使用已核验路线数据。", "info")


def _question_options(result):
    question = result.primary_question
    if not question:
        return None
    field = None
    target_ref = None
    target_label = None
    bundle_question = result.context_bundle.primary_question
    if bundle_question is not None and bundle_question.question == question:
        field = bundle_question.field_name
        target_ref = bundle_question.target_ref
    if field is None:
        estimate = next((item for item in result.ai_estimated_fields
                         if item.field_name == "estimated_total_minutes"), None)
        if estimate is not None:
            field, target_ref = estimate.field_name, estimate.target_ref
    validation = result.final_route_validation
    if field is None and validation is not None and validation.supplement_field_name:
        field = validation.supplement_field_name
        target_ref = validation.supplement_target_ref
        target_label = _safe(validation.supplement_target_label)
    tasks = _task_map(result)
    if target_ref in tasks:
        target_label = _safe(tasks[target_ref].title) or "任务"
    elif target_ref == "current_context":
        target_label = "当前位置"
    elif target_ref == "window_constraints":
        target_label = "当前空档"
    else:
        window = result.context_bundle.window_document
        commitment = None if window is None else next(
            (item for item in window.commitments if item.commitment_ref == target_ref), None)
        if commitment is not None:
            target_label = _safe(commitment.title) or "固定安排"
    metadata = {"target_ref": target_ref, "field_name": field,
                "target_label": target_label}
    text = _safe(question) or "请补充必要信息。"
    if field == "estimated_total_minutes":
        task = tasks.get(target_ref)
        minutes = None if task is None else task.features.estimated_total_minutes
        value = None if minutes is None else str(minutes)
        if isinstance(minutes, int):
            label = target_label or "任务"
            text = "“%s”完整时长目前按 %d 分钟估计（AI 暂估）。是否按这个估计继续？" % (label, minutes)
        options = (
            QuickOptionView("就按这个", "confirm_estimate", value, **metadata),
            QuickOptionView("调整时长", "adjust_duration", value, **metadata),
        )
    elif field in ("free_duration_minutes", "ends_at", "time_boundary"):
        options = (
            QuickOptionView("15 分钟", "set_duration", "15", **metadata),
            QuickOptionView("30 分钟", "set_duration", "30", **metadata),
            QuickOptionView("1 小时", "set_duration", "60", **metadata),
            QuickOptionView("自定义", "set_duration", None, **metadata),
        )
    elif field == "location_requirement":
        options = (
            QuickOptionView("不需要特定地点", "set_location_requirement", "no_specific_location", **metadata),
            QuickOptionView("需要特定地点", "set_location_requirement", "specific_location", **metadata),
            QuickOptionView("暂不确定", "set_location_requirement", "location_requirement_unknown", **metadata),
        )
    elif field in ("current_location_text", "location_text"):
        options = (QuickOptionView("填写地点", "set_location", None, **metadata),)
    elif field == "task_input":
        options = (QuickOptionView("添加任务", "change_task", None, **metadata),)
    elif bundle_question is not None and bundle_question.quick_options:
        options = tuple(QuickOptionView(label, "choose_option", label, **metadata)
                        for label in bundle_question.quick_options)
    else:
        options = (QuickOptionView("确认", "confirm", None, **metadata),
                   QuickOptionView("修改", "edit", None, **metadata))
    return QuestionView(text, options)


def _alternative(result):
    recommendation = result.recommendation_result
    if recommendation is None:
        return None
    selected = result.final_candidate or result.tentative_candidate
    validations = {}
    for selection in (recommendation.first_route_selection, recommendation.second_route_selection):
        if selection is None:
            continue
        for validation in (selection.primary_validation, selection.alternative_validation):
            if validation is not None:
                validations[validation.candidate.candidate_ref] = validation
    documents = [recommendation.second_candidate_result, recommendation.first_candidate_result]
    for parsed in documents:
        document = None if parsed is None else parsed.document
        if document is None:
            continue
        for candidate in (document.primary_candidate, document.alternative_candidate):
            if candidate is not None and candidate is not selected:
                steps = _steps(candidate, result)
                if steps:
                    validation = validations.get(candidate.candidate_ref)
                    if validation is not None and validation.status.value in ("time_infeasible", "route_unreachable"):
                        continue
                    prefix = "可作为备选：" if validation is not None and validation.is_feasible else "另一个思路（尚未验证）："
                    return prefix + "，".join("%s %d 分钟" % (item.title, item.planned_minutes) for item in steps)
    return None


def _status_text(result):
    mapping = {PlanningStatus.NEEDS_INFORMATION: ("补充一点信息", "还需要一点信息，我就能继续安排。"),
               PlanningStatus.NO_EXECUTABLE_TASKS: ("添加任务", "告诉我这段时间想完成什么吧。"),
               PlanningStatus.CANDIDATES_INFEASIBLE: ("暂时来不及", "当前两个方案加上步行时间后都来不及。"),
               PlanningStatus.CANDIDATE_FAILED: ("暂时无法生成方案", "候选建议暂时无法完成，请稍后再试。"),
               PlanningStatus.PARTIAL_EXTRACTION: ("模型输出需要重试", _safe(result.safe_summary) or "部分模型输出未通过严格校验，请重试。"),
               PlanningStatus.EXTRACTION_FAILED: ("模型输出需要重试", _safe(result.safe_summary) or "信息已经收到，但模型返回格式暂未通过校验，请重试。")}
    return mapping.get(result.status, ("当前建议", "我已为你整理当前安排。"))


def _source_notes(result, candidate):
    if candidate is None:
        return ()
    tasks = _task_map(result)
    sources = []
    important = ("estimated_total_minutes", "minimum_slice_minutes", "location_requirement", "location_text", "attention_required")
    for step in candidate.steps:
        task = tasks.get(step.task_ref)
        if task is None:
            continue
        for name in important:
            evidence = task.field_evidence.get(name)
            if evidence is not None:
                sources.append(evidence.source.value)
    window = result.context_bundle.window_document
    if window is not None:
        for evidence in window.current_context.field_evidence.values():
            sources.append(evidence.source.value)
        for evidence in window.constraints.field_evidence.values():
            sources.append(evidence.source.value)
        next_item = window.earliest_known_commitment
        if next_item is not None:
            for name in ("starts_at", "location_text"):
                evidence = next_item.field_evidence.get(name)
                if evidence is not None:
                    sources.append(evidence.source.value)
    labels = []
    for source in (["ai_estimated"] + sources):
        label = _SOURCE_LABELS.get(source)
        if label and label not in labels:
            labels.append(label)
    return tuple(labels)


def _selection_notice(result):
    recommendation = result.recommendation_result
    if recommendation is not None and recommendation.status.value == "selected_alternative":
        return NoticeView("原首选加上步行时间后可能来不及，已为你改用备选方案。", "info")
    return None


def format_p1_result(result):
    """不改变规划结果；只生成未来页面可分别渲染的展示字段。"""
    candidate = result.final_candidate or result.tentative_candidate
    validation = result.final_route_validation
    if candidate is not None:
        headline = _headline(candidate, validation, result)
        title = "当前方案"
        tentative = NoticeView("我先按这些信息给你安排，确认后会更准确。", "gentle") if result.is_tentative else None
    else:
        title, headline = _status_text(result)
        tentative = None
    action_targets = {
        "extend_time": ("window_constraints", "free_duration_minutes", "当前空档"),
        "change_task": ("task_context", "task_input", "任务"),
        "update_location": ("current_context", "current_location_text", "当前位置"),
    }
    actions = tuple(
        QuickOptionView(
            _ACTION_LABELS.get(item.value, "调整方案"), item.value, None,
            *(action_targets.get(item.value, (None, None, None))))
        for item in result.quick_actions)
    other_questions = []
    for text in result.other_questions:
        clean = _safe(text)
        if clean and clean not in other_questions:
            other_questions.append(clean)
    return P1ResultView(title, headline, None if candidate is None else _card(candidate, validation, result),
                        _alternative(result), _selection_notice(result), tentative, _route_notice(validation), _question_options(result),
                        tuple(other_questions), actions,
                        _source_notes(result, candidate), candidate is not None or bool(result.other_questions))
