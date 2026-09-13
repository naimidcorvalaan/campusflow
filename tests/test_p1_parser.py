import copy
import json

import pytest

from src.p1_models import MAX_DURATION_MINUTES, MAX_TASKS, SourceKind
from src.p1_parser import parse_task_understanding


USER_TEXT = "完成计组实验3"
FEATURE_FIELDS = (
    "location_requirement", "location_text", "environment_requirements",
    "equipment_requirements", "estimated_total_minutes", "is_splittable",
    "minimum_slice_minutes", "attention_required", "interruption_allowed",
    "may_have_open_hours",
)


def make_features(**overrides):
    features = {
        "location_requirement": "location_requirement_unknown",
        "location_text": None,
        "environment_requirements": ["安静环境"],
        "equipment_requirements": ["电脑"],
        "estimated_total_minutes": 90,
        "is_splittable": True,
        "minimum_slice_minutes": 30,
        "attention_required": "high",
        "interruption_allowed": True,
        "may_have_open_hours": False,
    }
    features.update(overrides)
    return features


def has_value(value):
    if isinstance(value, list):
        return bool(value)
    return value is not None


def make_evidence(features, source="ai_estimated"):
    evidence = {}
    for field_name in FEATURE_FIELDS:
        if has_value(features[field_name]):
            evidence[field_name] = {
                "source": source,
                "explanation": "根据用户表达和任务内容作出合理判断",
            }
    return evidence


def make_task(task_ref="input-task-1", title=USER_TEXT, original_text=USER_TEXT,
              features=None, evidence=None, needs_confirmation=None):
    features = make_features() if features is None else features
    return {
        "task_ref": task_ref,
        "title": title,
        "original_text": original_text,
        "understood_features": features,
        "field_evidence": make_evidence(features) if evidence is None else evidence,
        "needs_confirmation": ["location_requirement"] if needs_confirmation is None else needs_confirmation,
    }


def make_question(task_ref="input-task-1", field_name="location_requirement"):
    return {
        "question_id": "q1",
        "task_ref": task_ref,
        "field_name": field_name,
        "question": "该任务是否需要特定地点？",
        "blocking_for_task_understanding": False,
        "quick_options": ["不需要特定地点", "需要特定地点", "暂不确定"],
    }


def make_payload(tasks=None, questions=None):
    return {
        "schema_version": "p1.task-understanding.v1",
        "task_interpretations": [make_task()] if tasks is None else tasks,
        "clarification_questions": [make_question()] if questions is None else questions,
    }


def parse_payload(payload, user_text=USER_TEXT):
    return parse_task_understanding(json.dumps(payload, ensure_ascii=False), user_text)


def assert_rejected(result, error_type=None):
    assert result.status == "rejected"
    assert result.document is None
    if error_type:
        assert result.error_type == error_type


def test_single_task_keeps_title_fragment_and_all_sources():
    result = parse_payload(make_payload())
    assert result.status == "ok"
    task = result.tasks[0]
    assert task.title == USER_TEXT
    assert task.original_text == USER_TEXT
    assert task.field_evidence["original_text"].source is SourceKind.USER_STATED
    assert task.field_evidence["estimated_total_minutes"].source is SourceKind.AI_ESTIMATED


def test_empty_task_interpretations_are_a_successful_no_todo_result():
    result = parse_payload(make_payload(tasks=[], questions=[]))
    assert result.status == "ok"
    assert result.tasks == ()


def test_user_text_extracted_duration_is_accepted():
    text = "背半小时单词"
    features = make_features(location_requirement="no_specific_location", environment_requirements=[], equipment_requirements=[], estimated_total_minutes=30, is_splittable=False, minimum_slice_minutes=30, attention_required="low")
    task = make_task(title="背单词", original_text=text, features=features,
                     evidence=make_evidence(features, "ai_extracted_from_user_text"), needs_confirmation=[])
    result = parse_payload(make_payload([task], []), text)
    assert result.status == "ok"
    assert result.tasks[0].field_evidence["estimated_total_minutes"].source is SourceKind.AI_EXTRACTED_FROM_USER_TEXT


def test_ai_estimated_duration_is_preserved():
    result = parse_payload(make_payload())
    assert result.tasks[0].features.estimated_total_minutes == 90
    assert result.tasks[0].field_evidence["estimated_total_minutes"].source is SourceKind.AI_ESTIMATED


def test_three_tasks_are_independent_and_questions_use_stable_refs_after_reorder():
    text = "完成计组实验3，给导师发表格，再背半小时单词"
    first = make_task("experiment", "完成计组实验3", "完成计组实验3")
    second_features = make_features(location_requirement="no_specific_location", environment_requirements=[], equipment_requirements=[], estimated_total_minutes=30, is_splittable=False, minimum_slice_minutes=30, attention_required="low")
    second = make_task("words", "背单词", "背半小时单词", second_features, make_evidence(second_features, "ai_extracted_from_user_text"), [])
    third = make_task("mail", "给导师发表格", "给导师发表格")
    question = make_question("words", "estimated_total_minutes")
    second["needs_confirmation"] = ["estimated_total_minutes"]
    result = parse_payload(make_payload([third, second, first], [question]), text)
    assert result.status == "ok"
    assert result.questions[0].task_ref == "words"
    assert result.questions[0].field_name == "estimated_total_minutes"


@pytest.mark.parametrize("requirement,location_text,needs", [
    ("no_specific_location", None, []),
    ("specific_location", "北区菜鸟驿站", []),
    ("specific_location", None, ["location_text"]),
    ("location_requirement_unknown", None, ["location_requirement"]),
])
def test_three_location_states(requirement, location_text, needs):
    features = make_features(location_requirement=requirement, location_text=location_text)
    result = parse_payload(make_payload([make_task(features=features, needs_confirmation=needs)], []))
    assert result.status == "ok"


def test_empty_arrays_and_null_fields_need_no_evidence():
    features = make_features(location_requirement="no_specific_location", location_text=None,
                             environment_requirements=[], equipment_requirements=[],
                             estimated_total_minutes=None, is_splittable=None,
                             minimum_slice_minutes=None, attention_required=None,
                             interruption_allowed=None, may_have_open_hours=None)
    result = parse_payload(make_payload([make_task(features=features, evidence={"location_requirement": {"source": "ai_estimated", "explanation": "用户没有说明地点限制"}}, needs_confirmation=[])], []))
    assert result.status == "ok"


@pytest.mark.parametrize("field_name", FEATURE_FIELDS)
def test_each_nonempty_important_field_requires_evidence(field_name):
    task = make_task()
    if field_name == "location_text":
        task["understood_features"][field_name] = "一间实验室"
        task["field_evidence"] = make_evidence(task["understood_features"])
    task["field_evidence"].pop(field_name)
    assert_rejected(parse_payload(make_payload([task], [])), "validation_error")


def test_false_boolean_requires_evidence_and_empty_field_cannot_have_evidence():
    task = make_task()
    task["field_evidence"].pop("may_have_open_hours")
    assert_rejected(parse_payload(make_payload([task], [])), "validation_error")
    task = make_task()
    task["understood_features"]["location_text"] = None
    task["field_evidence"]["location_text"] = {"source": "ai_estimated", "explanation": "没有具体地点"}
    assert_rejected(parse_payload(make_payload([task], [])), "validation_error")


@pytest.mark.parametrize("source", ["user_stated", "user_confirmed", "system_default", "system_verified"])
def test_model_cannot_claim_user_or_system_sources(source):
    task = make_task()
    task["field_evidence"]["estimated_total_minutes"]["source"] = source
    assert_rejected(parse_payload(make_payload([task], [])), "validation_error")


@pytest.mark.parametrize("source,explanation", [
    ("ai_estimated", ""), ("ai_extracted_from_user_text", "probably 30 minutes"),
    ("ai_estimated", "置信度 80%"), ("ai_extracted_from_user_text", "百分比 80"),
])
def test_both_ai_sources_require_natural_chinese_non_percentage_explanation(source, explanation):
    task = make_task()
    task["field_evidence"]["estimated_total_minutes"] = {"source": source, "explanation": explanation}
    assert_rejected(parse_payload(make_payload([task], [])), "validation_error")


def test_question_rejects_unknown_ref_invalid_field_missing_need_and_legacy_path():
    payload = make_payload()
    payload["clarification_questions"][0]["task_ref"] = "missing"
    assert_rejected(parse_payload(payload), "validation_error")
    payload = make_payload()
    payload["clarification_questions"][0]["field_name"] = "route"
    assert_rejected(parse_payload(payload), "validation_error")
    payload = make_payload()
    payload["clarification_questions"][0]["field_name"] = "location_text"
    assert_rejected(parse_payload(payload), "validation_error")
    payload = make_payload()
    payload["clarification_questions"][0]["field_path"] = "task_interpretations[0]"
    assert_rejected(parse_payload(payload), "format_error")


def test_duplicate_question_ids_are_rejected():
    question = make_question()
    assert_rejected(parse_payload(make_payload(questions=[question, copy.deepcopy(question)])), "validation_error")


@pytest.mark.parametrize("features,accepted", [
    (make_features(is_splittable=False, estimated_total_minutes=90, minimum_slice_minutes=90), True),
    (make_features(is_splittable=False, estimated_total_minutes=90, minimum_slice_minutes=30), False),
    (make_features(is_splittable=False, estimated_total_minutes=90, minimum_slice_minutes=100), False),
    (make_features(is_splittable=False, estimated_total_minutes=90, minimum_slice_minutes=None), False),
    (make_features(is_splittable=False, estimated_total_minutes=None, minimum_slice_minutes=30), False),
    (make_features(is_splittable=True, estimated_total_minutes=90, minimum_slice_minutes=30), True),
])
def test_splittable_duration_consistency(features, accepted):
    result = parse_payload(make_payload([make_task(features=features)], []))
    assert (result.status == "ok") is accepted


@pytest.mark.parametrize("raw", ["```json\n{}\n```", "说明：{}", "{} 尾文", "{bad", "[]"])
def test_non_pure_or_invalid_json_is_rejected(raw):
    assert_rejected(parse_task_understanding(raw, USER_TEXT), "format_error")


@pytest.mark.parametrize("raw", [
    '{"schema_version":"p1.task-understanding.v1","schema_version":"x","task_interpretations":[],"clarification_questions":[]}',
    '{"schema_version":"p1.task-understanding.v1","task_interpretations":[{"task_ref":"a","task_ref":"b"}],"clarification_questions":[]}',
])
def test_duplicate_keys_are_rejected(raw):
    assert_rejected(parse_task_understanding(raw, USER_TEXT), "format_error")


@pytest.mark.parametrize("mutator", [
    lambda p: p.update({"route": []}),
    lambda p: p.update({"window": {}}),
    lambda p: p.update({"primary_candidate": {}}),
    lambda p: p["task_interpretations"][0].update({"lifecycle": "active"}),
    lambda p: p["task_interpretations"][0]["understood_features"].update({"location_id": "node"}),
    lambda p: p["task_interpretations"][0]["understood_features"].update({"completed_minutes": 1}),
])
def test_unknown_route_window_progress_and_lifecycle_fields_are_rejected(mutator):
    payload = make_payload()
    mutator(payload)
    assert_rejected(parse_payload(payload), "format_error")


@pytest.mark.parametrize("field,value", [
    ("estimated_total_minutes", True), ("estimated_total_minutes", 0),
    ("estimated_total_minutes", MAX_DURATION_MINUTES + 1),
    ("minimum_slice_minutes", False), ("location_requirement", "anywhere"),
])
def test_invalid_minutes_and_enums_are_rejected(field, value):
    task = make_task()
    task["understood_features"][field] = value
    assert_rejected(parse_payload(make_payload([task], [])), "validation_error")


def test_duplicate_refs_count_limit_and_forged_fragment_are_rejected():
    assert_rejected(parse_payload(make_payload([make_task(), make_task(title="另一个")], [])), "validation_error")
    tasks = [make_task(task_ref="task-%d" % index, title="任务%d" % index) for index in range(MAX_TASKS + 1)]
    assert_rejected(parse_payload(make_payload(tasks, [])), "validation_error")
    assert_rejected(parse_payload(make_payload([make_task(original_text="模型伪造文本")], [])), "validation_error")


@pytest.mark.parametrize("mutator", [
    lambda p: p.update({"unexpected": True}),
    lambda p: p["task_interpretations"][0].update({"unexpected": True}),
    lambda p: p["task_interpretations"][0]["understood_features"].update({"unexpected": True}),
    lambda p: p["task_interpretations"][0]["field_evidence"]["estimated_total_minutes"].update({"unexpected": True}),
    lambda p: p["clarification_questions"][0].update({"unexpected": True}),
])
def test_unknown_fields_are_rejected_at_every_schema_level(mutator):
    payload = make_payload()
    mutator(payload)
    assert_rejected(parse_payload(payload), "format_error")


@pytest.mark.parametrize("mutator", [
    lambda p: p.pop("schema_version"),
    lambda p: p["task_interpretations"][0].pop("title"),
    lambda p: p["task_interpretations"][0]["understood_features"].pop("attention_required"),
    lambda p: p["task_interpretations"][0]["field_evidence"]["estimated_total_minutes"].pop("explanation"),
    lambda p: p["clarification_questions"][0].pop("question"),
])
def test_missing_required_fields_are_rejected(mutator):
    payload = make_payload()
    mutator(payload)
    assert_rejected(parse_payload(payload), "format_error")


@pytest.mark.parametrize("requirement,location_text,needs", [
    ("no_specific_location", "图书馆", []),
    ("location_requirement_unknown", "图书馆", ["location_requirement"]),
    ("specific_location", None, []),
    ("location_requirement_unknown", None, []),
])
def test_invalid_location_combinations_are_rejected(requirement, location_text, needs):
    features = make_features(location_requirement=requirement, location_text=location_text)
    assert_rejected(parse_payload(make_payload([make_task(features=features, needs_confirmation=needs)], [])), "validation_error")


@pytest.mark.parametrize("field,value", [
    ("is_splittable", 1), ("interruption_allowed", "true"),
    ("may_have_open_hours", 0), ("environment_requirements", "安静"),
    ("equipment_requirements", [1]), ("attention_required", "very_high"),
    ("location_requirement", None), ("location_text", 1),
])
def test_wrong_feature_types_are_rejected(field, value):
    task = make_task()
    task["understood_features"][field] = value
    assert_rejected(parse_payload(make_payload([task], [])), "validation_error")


@pytest.mark.parametrize("mutator", [
    lambda t: t["field_evidence"].update({"route": {"source": "ai_estimated", "explanation": "无效字段"}}),
    lambda t: t["field_evidence"]["estimated_total_minutes"].update({"source": "not_a_source"}),
    lambda t: t["field_evidence"]["estimated_total_minutes"].update({"explanation": None}),
    lambda t: t["field_evidence"].update({"location_text": {"source": "ai_estimated", "explanation": "字段为空"}}),
    lambda t: (t["understood_features"].update({"environment_requirements": []}), t["field_evidence"].update({"environment_requirements": {"source": "ai_estimated", "explanation": "字段为空"}})),
])
def test_invalid_evidence_shapes_and_empty_values_are_rejected(mutator):
    task = make_task()
    mutator(task)
    assert_rejected(parse_payload(make_payload([task], [])))


def test_output_and_user_length_limits_are_enforced():
    assert_rejected(parse_task_understanding(" " * 50001, USER_TEXT), "validation_error")
    raw = json.dumps(make_payload(), ensure_ascii=False)
    assert_rejected(parse_task_understanding(raw, "字" * 4001), "validation_error")


def test_rejection_message_never_echoes_sensitive_model_content():
    payload = make_payload()
    payload["Authorization Bearer secret"] = {"api_key": "hidden"}
    result = parse_payload(payload)
    assert_rejected(result, "format_error")
    assert "Bearer" not in result.message
    assert "secret" not in result.message


@pytest.mark.parametrize("mutator", [
    lambda p: p["clarification_questions"][0].update({"question_id": "bad id"}),
    lambda p: p["clarification_questions"][0].update({"blocking_for_task_understanding": 1}),
])
def test_question_identifier_and_boolean_type_are_checked(mutator):
    payload = make_payload()
    mutator(payload)
    assert_rejected(parse_payload(payload), "validation_error")


@pytest.mark.parametrize("field_name", ["route", "original_text", "completed_minutes", "location_id"])
def test_evidence_cannot_reference_non_feature_fields(field_name):
    task = make_task()
    task["field_evidence"][field_name] = {"source": "ai_estimated", "explanation": "无效字段"}
    assert_rejected(parse_payload(make_payload([task], [])), "format_error")
