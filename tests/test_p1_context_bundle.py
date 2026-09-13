from datetime import datetime

from src.p1_context_bundle import ExtractionStatus, build_p1_context_bundle
from src.p1_extraction_models import P1TaskUnderstandingParseResult
from src.p1_models import (AttentionLevel, ClarificationQuestion, FieldEvidence, LocationRequirement, SCHEMA_VERSION, SourceKind, TaskUnderstanding, TaskUnderstandingDocument, TaskUnderstandingFeatures)
from src.p1_window_extraction_models import P1WindowParseResult
from src.p1_window_models import (AvailabilityLevel, CurrentContext, FixedCommitment, WINDOW_SCHEMA_VERSION, WindowClarificationQuestion, WindowConstraints, WindowContextDocument)


NOW = datetime(2026, 5, 1, 10, 5)


def task_result(ok=True, questions=()):
    if not ok:
        return P1TaskUnderstandingParseResult("rejected", "format_error", "safe", None)
    features = TaskUnderstandingFeatures(LocationRequirement.LOCATION_REQUIREMENT_UNKNOWN, None, (), (), 90, True, 30, AttentionLevel.HIGH, True, False)
    evidence = {"estimated_total_minutes": FieldEvidence(SourceKind.AI_ESTIMATED, "用户未说明时长，按实验暂估"), "is_splittable": FieldEvidence(SourceKind.AI_ESTIMATED, "按任务特点暂估"), "minimum_slice_minutes": FieldEvidence(SourceKind.AI_ESTIMATED, "按有效片段暂估"), "attention_required": FieldEvidence(SourceKind.AI_ESTIMATED, "按实验特点暂估"), "interruption_allowed": FieldEvidence(SourceKind.AI_ESTIMATED, "按任务特点暂估"), "may_have_open_hours": FieldEvidence(SourceKind.AI_ESTIMATED, "按任务特点暂估"), "location_requirement": FieldEvidence(SourceKind.AI_ESTIMATED, "暂不确定地点要求")}
    task = TaskUnderstanding("task-1", "完成实验", "完成实验", features, evidence, ("location_requirement",))
    doc = TaskUnderstandingDocument(SCHEMA_VERSION, "完成实验", (task,), questions)
    return P1TaskUnderstandingParseResult("ok", None, "ok", doc)


def window_result(ok=True, location="31楼", start=NOW.replace(hour=11, minute=30), next_location="46楼", constraints=None, questions=(), unknown=False, include_known=True):
    if not ok:
        return P1WindowParseResult("rejected", "format_error", "Authorization Bearer secret", None)
    current_needs = () if location else ("current_location_text",)
    current = CurrentContext(NOW, NOW, location, {}, current_needs)
    known = FixedCommitment("class-1", "上课", "上课", start, None, next_location, AvailabilityLevel.UNAVAILABLE, {}, ())
    commitments = (known,) if include_known else ()
    if unknown:
        commitments = (FixedCommitment("unknown", "未知安排", "未知安排", None, None, "45楼", AvailabilityLevel.UNAVAILABLE, {}, ("starts_at",)), known)
    if constraints is None:
        constraints = WindowConstraints(None, None, 10, {}, ())
    doc = WindowContextDocument(WINDOW_SCHEMA_VERSION, "输入", current, commitments, constraints, questions)
    return P1WindowParseResult("ok", None, "ok", doc)


def test_complete_bundle_preserves_documents_and_not_route_feasibility():
    bundle = build_p1_context_bundle(task_result(), window_result())
    assert bundle.extraction_status is ExtractionStatus.COMPLETE
    assert len(bundle.tasks) == 1 and bundle.current_context.current_location_text == "31楼"
    assert bundle.route_context_ready
    assert not hasattr(bundle, "route_feasible")


def test_task_success_window_failure_keeps_tasks_and_reports_window_parse_failure():
    bundle = build_p1_context_bundle(task_result(), window_result(False))
    assert bundle.extraction_status is ExtractionStatus.PARTIAL
    assert len(bundle.tasks) == 1 and bundle.window_document is None
    assert bundle.primary_question.field_name == "model_output"
    assert "时间信息已经收到" in bundle.primary_question.question


def test_window_success_task_failure_keeps_window_and_reports_task_parse_failure():
    bundle = build_p1_context_bundle(task_result(False), window_result())
    assert bundle.extraction_status is ExtractionStatus.PARTIAL
    assert bundle.window_document is not None
    assert bundle.primary_question.field_name == "model_output"
    assert "任务信息已经收到" in bundle.primary_question.question


def test_both_fail_is_safe_and_does_not_echo_sensitive_error():
    bundle = build_p1_context_bundle(task_result(False), window_result(False))
    assert bundle.extraction_status is ExtractionStatus.FAILED
    assert "Authorization" not in " ".join(bundle.safe_error_summaries)
    assert bundle.primary_question.field_name == "model_output"
    assert "信息已经收到" in bundle.primary_question.question


def test_missing_boundary_precedes_unknown_start_location_and_task_questions():
    q = WindowClarificationQuestion("w1", "window_constraints", "free_duration_minutes", "空闲多久？", True, ())
    window = window_result(constraints=WindowConstraints(None, None, 10, {}, ("free_duration_minutes",)), questions=(q,), location=None, include_known=False)
    task_q = ClarificationQuestion("t1", "task-1", "location_requirement", "地点？", False, ())
    bundle = build_p1_context_bundle(task_result(questions=(task_q,)), window)
    assert [question.field_name for question in bundle.prioritized_questions[:3]] == ["free_duration_minutes", "current_location_text", "location_requirement"]


def test_unknown_start_precedes_location_and_disables_route_context():
    bundle = build_p1_context_bundle(task_result(), window_result(unknown=True, location=None))
    assert bundle.primary_question.field_name == "starts_at"
    assert bundle.has_unresolved_commitment_start
    assert not bundle.route_context_ready


def test_confirmed_next_missing_location_is_prioritized_and_not_route_ready():
    bundle = build_p1_context_bundle(task_result(), window_result(next_location=None))
    assert bundle.primary_question.field_name == "location_text"
    assert not bundle.route_context_ready


def test_window_questions_keep_stable_order_before_task_questions():
    q1 = WindowClarificationQuestion("w1", "class-1", "ends_at", "何时下课？", False, ("12点",))
    q2 = WindowClarificationQuestion("w2", "window_constraints", "ends_at", "何时结束空闲？", False, ())
    t1 = ClarificationQuestion("t1", "task-1", "location_requirement", "地点？", False, ("不需要特定地点",))
    bundle = build_p1_context_bundle(task_result(questions=(t1,)), window_result(questions=(q1, q2)))
    assert [x.source for x in bundle.prioritized_questions] == ["window", "window", "task"]
    assert bundle.prioritized_questions[0].quick_options == ("12点",)
    assert bundle.prioritized_questions[2].quick_options == ("不需要特定地点",)


def test_ai_sources_and_confirmation_fields_are_preserved_without_upgrade():
    bundle = build_p1_context_bundle(task_result(), window_result())
    assert any(item.field_name == "estimated_total_minutes" and item.evidence.source is SourceKind.AI_ESTIMATED for item in bundle.ai_estimated_task_fields)
    assert any(item.field_name == "location_requirement" for item in bundle.user_confirmation_fields)


def test_missing_current_location_confirmation_has_no_evidence():
    window = window_result(location=None)
    bundle = build_p1_context_bundle(task_result(), window)
    fields = [item for item in bundle.user_confirmation_fields if item.field_name == "current_location_text"]
    assert len(fields) == 1
    assert fields[0].evidence is None


def test_missing_time_boundary_confirmation_has_no_evidence():
    window = window_result(include_known=False, constraints=WindowConstraints(None, None, 10, {}, ("free_duration_minutes",)))
    bundle = build_p1_context_bundle(task_result(), window)
    fields = [item for item in bundle.user_confirmation_fields if item.field_name == "free_duration_minutes"]
    assert len(fields) == 1
    assert fields[0].evidence is None


def test_ai_estimated_confirmation_keeps_evidence():
    bundle = build_p1_context_bundle(task_result(), window_result())
    fields = [item for item in bundle.user_confirmation_fields if item.field_name == "location_requirement"]
    assert len(fields) == 1
    assert fields[0].evidence is not None
    assert fields[0].evidence.source is SourceKind.AI_ESTIMATED
