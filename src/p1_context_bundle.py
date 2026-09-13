"""P1c：把已解析的 P1a 任务与 P1b 窗口结果安全组合，不调用外部服务。"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from src.p1_extraction_models import P1TaskUnderstandingParseResult
from src.p1_models import FieldEvidence, SourceKind, TaskUnderstanding
from src.p1_window_extraction_models import P1WindowParseResult
from src.p1_window_models import CurrentContext, FixedCommitment, WindowContextDocument


class ExtractionStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True)
class BundleQuestion:
    """页面可直接排序显示的确认问题；不复制 P1a/P1b 的领域模型。"""
    source: str
    target_ref: str
    field_name: str
    question: str
    blocking: bool
    quick_options: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SourcedField:
    source_area: str
    target_ref: str
    field_name: str
    evidence: FieldEvidence


@dataclass(frozen=True)
class ConfirmationField:
    """待确认字段；字段尚无值时 evidence 合法地为 None。"""
    source_area: str
    target_ref: str
    field_name: str
    evidence: Optional[FieldEvidence]


@dataclass(frozen=True)
class P1ContextBundle:
    task_result: P1TaskUnderstandingParseResult
    window_result: P1WindowParseResult
    extraction_status: ExtractionStatus
    prioritized_questions: Tuple[BundleQuestion, ...]
    safe_error_summaries: Tuple[str, ...]

    @property
    def task_document(self):
        return self.task_result.document

    @property
    def window_document(self) -> Optional[WindowContextDocument]:
        return self.window_result.document

    @property
    def tasks(self) -> Tuple[TaskUnderstanding, ...]:
        return self.task_result.tasks

    @property
    def current_context(self) -> Optional[CurrentContext]:
        return None if self.window_document is None else self.window_document.current_context

    @property
    def commitments(self) -> Tuple[FixedCommitment, ...]:
        return () if self.window_document is None else self.window_document.commitments

    @property
    def has_time_boundary(self) -> bool:
        return self.window_document is not None and self.window_document.has_time_boundary

    @property
    def has_current_location(self) -> bool:
        return self.current_context is not None and self.current_context.current_location_text is not None

    @property
    def has_unresolved_commitment_start(self) -> bool:
        return self.window_document is not None and self.window_document.has_unresolved_commitment_start

    @property
    def route_context_ready(self) -> bool:
        """仅表示未来可尝试路线核验，不表示路线存在、可达或可行。"""
        document = self.window_document
        if document is None or not self.has_current_location or not document.has_time_boundary:
            return False
        if document.has_unresolved_commitment_start:
            return False
        next_item = document.earliest_known_commitment
        return next_item is None or next_item.location_text is not None

    @property
    def primary_question(self) -> Optional[BundleQuestion]:
        return self.prioritized_questions[0] if self.prioritized_questions else None

    @property
    def other_questions(self) -> Tuple[BundleQuestion, ...]:
        return self.prioritized_questions[1:]

    @property
    def ai_estimated_task_fields(self) -> Tuple[SourcedField, ...]:
        return _task_fields_with_source(self.tasks, SourceKind.AI_ESTIMATED)

    @property
    def ai_extracted_from_user_text_fields(self) -> Tuple[SourcedField, ...]:
        fields = list(_task_fields_with_source(self.tasks, SourceKind.AI_EXTRACTED_FROM_USER_TEXT))
        document = self.window_document
        if document is not None:
            fields.extend(_window_fields_with_source(document, SourceKind.AI_EXTRACTED_FROM_USER_TEXT))
        return tuple(fields)

    @property
    def user_confirmation_fields(self) -> Tuple[ConfirmationField, ...]:
        fields = []
        for task in self.tasks:
            for field_name in task.needs_confirmation:
                fields.append(ConfirmationField("task", task.task_ref, field_name, task.field_evidence.get(field_name)))
        document = self.window_document
        if document is not None:
            for field_name in document.current_context.needs_confirmation:
                fields.append(ConfirmationField("window", "current_context", field_name, document.current_context.field_evidence.get(field_name)))
            for item in document.commitments:
                for field_name in item.needs_confirmation:
                    fields.append(ConfirmationField("window", item.commitment_ref, field_name, item.field_evidence.get(field_name)))
            for field_name in document.constraints.needs_confirmation:
                fields.append(ConfirmationField("window", "window_constraints", field_name, document.constraints.field_evidence.get(field_name)))
        return tuple(fields)


def _task_fields_with_source(tasks, source):
    result = []
    for task in tasks:
        for field_name, evidence in task.field_evidence.items():
            if evidence.source is source:
                result.append(SourcedField("task", task.task_ref, field_name, evidence))
    return tuple(result)


def _window_fields_with_source(document, source):
    result = []
    for field_name, evidence in document.current_context.field_evidence.items():
        if evidence.source is source:
            result.append(SourcedField("window", "current_context", field_name, evidence))
    for item in document.commitments:
        for field_name, evidence in item.field_evidence.items():
            if evidence.source is source:
                result.append(SourcedField("window", item.commitment_ref, field_name, evidence))
    for field_name, evidence in document.constraints.field_evidence.items():
        if evidence.source is source:
            result.append(SourcedField("window", "window_constraints", field_name, evidence))
    return tuple(result)


def _question_from_window(question):
    return BundleQuestion("window", question.target_ref, question.field_name, question.question, question.blocking, question.quick_options)


def _question_from_task(question):
    return BundleQuestion("task", question.task_ref, question.field_name, question.question, question.blocking_for_task_understanding, question.quick_options)


def _find(questions, target_ref, field_name):
    for question in questions:
        if question.target_ref == target_ref and question.field_name == field_name:
            return question
    return None


def _program_question(target_ref, field_name, text):
    return BundleQuestion("program", target_ref, field_name, text, True, ())


def _parse_failure_question(task_result, window_result):
    task_failed = task_result.document is None
    window_failed = window_result.document is None
    call_failed = (
        (task_failed and task_result.error_type == "call_error")
        or (window_failed and window_result.error_type == "call_error")
    )
    if task_failed and window_failed:
        text = ("信息已经收到，但模型调用暂未完成，请重试。" if call_failed else
                "信息已经收到，但模型返回格式暂未通过校验，请重试。")
        return _program_question("extraction", "model_output", text)
    if window_failed:
        text = ("你的时间信息已经收到，但时间理解调用暂未完成，请重试一次。"
                if call_failed else
                "你的时间信息已经收到，但模型返回格式暂未通过校验，请重试一次。")
        return _program_question("window_context", "model_output", text)
    if task_failed:
        text = ("你的任务信息已经收到，但任务理解调用暂未完成，请重试一次。"
                if call_failed else
                "你的任务信息已经收到，但模型返回格式暂未通过校验，请重试一次。")
        return _program_question("task_context", "model_output", text)
    return None


def _prioritized(task_result, window_result):
    window_questions = [] if window_result.document is None else [_question_from_window(x) for x in window_result.questions]
    task_questions = [_question_from_task(x) for x in task_result.questions]
    selected = []

    task_failed = task_result.document is None
    window_failed = window_result.document is None
    failure_question = _parse_failure_question(task_result, window_result)
    if failure_question is not None:
        selected.append(failure_question)

    if not window_failed:
        document = window_result.document
        if not document.has_time_boundary:
            selected.append(_find(window_questions, "window_constraints", "free_duration_minutes") or _find(window_questions, "window_constraints", "ends_at") or _program_question("window_constraints", "time_boundary", "请补充你接下来有多少可用时间。"))
        if document.has_unresolved_commitment_start:
            for item in document.commitments:
                if item.starts_at is None:
                    selected.append(_find(window_questions, item.commitment_ref, "starts_at") or _program_question(item.commitment_ref, "starts_at", "请补充这项安排的开始时间。"))
        if document.current_context.current_location_text is None:
            selected.append(_find(window_questions, "current_context", "current_location_text") or _program_question("current_context", "current_location_text", "请告诉我你现在在哪里。"))
        next_item = document.earliest_known_commitment
        if document.next_commitment_is_confirmed and next_item is not None and next_item.location_text is None:
            selected.append(_find(window_questions, next_item.commitment_ref, "location_text") or _program_question(next_item.commitment_ref, "location_text", "请补充下一项安排的地点。"))
        selected.extend(question for question in window_questions if question not in selected)

    if task_result.document is not None and not task_result.tasks:
        selected.append(_program_question("task_context", "task_input", "请告诉我这段时间想完成什么。"))
    selected.extend(question for question in task_questions if question not in selected)
    return tuple(selected)


def build_p1_context_bundle(task_result, window_result) -> P1ContextBundle:
    if not isinstance(task_result, P1TaskUnderstandingParseResult) or not isinstance(window_result, P1WindowParseResult):
        raise TypeError("必须传入 P1a 和 P1b 的解析结果。")
    task_ok, window_ok = task_result.document is not None, window_result.document is not None
    status = ExtractionStatus.COMPLETE if task_ok and window_ok else (ExtractionStatus.FAILED if not task_ok and not window_ok else ExtractionStatus.PARTIAL)
    errors = []
    if not task_ok:
        errors.append("任务理解调用暂未完成。" if task_result.error_type == "call_error"
                      else "任务模型输出未通过严格校验。")
    if not window_ok:
        errors.append("时间窗口调用暂未完成。" if window_result.error_type == "call_error"
                      else "窗口模型输出未通过严格校验。")
    return P1ContextBundle(task_result, window_result, status, _prioritized(task_result, window_result), tuple(errors))
