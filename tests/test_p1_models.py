from dataclasses import replace

from src.p1_models import (
    AttentionLevel,
    ClarificationQuestion,
    FieldEvidence,
    LocationRequirement,
    SCHEMA_VERSION,
    SourceKind,
    TaskUnderstanding,
    TaskUnderstandingDocument,
    TaskUnderstandingFeatures,
)


def make_features():
    return TaskUnderstandingFeatures(
        location_requirement=LocationRequirement.LOCATION_REQUIREMENT_UNKNOWN,
        location_text=None,
        environment_requirements=("安静环境",),
        equipment_requirements=("电脑",),
        estimated_total_minutes=90,
        is_splittable=True,
        minimum_slice_minutes=30,
        attention_required=AttentionLevel.HIGH,
        interruption_allowed=True,
        may_have_open_hours=False,
    )


def test_source_kind_supports_confirmed_future_updates_without_generic_wrappers():
    evidence = FieldEvidence(SourceKind.AI_ESTIMATED, "按任务内容暂估")

    confirmed = replace(evidence, source=SourceKind.USER_CONFIRMED)

    assert confirmed.source is SourceKind.USER_CONFIRMED
    assert confirmed.explanation == "按任务内容暂估"
    assert {source.value for source in SourceKind} == {
        "user_stated",
        "user_confirmed",
        "system_default",
        "system_verified",
        "ai_extracted_from_user_text",
        "ai_estimated",
    }


def test_location_requirement_has_exactly_three_general_states():
    assert {item.value for item in LocationRequirement} == {
        "no_specific_location",
        "specific_location",
        "location_requirement_unknown",
    }


def test_source_kind_distinguishes_user_text_extraction_from_estimation():
    assert SourceKind.AI_EXTRACTED_FROM_USER_TEXT is not SourceKind.AI_ESTIMATED


def test_arbitrary_title_and_original_text_are_separate_fields():
    task = TaskUnderstanding(
        task_ref="input-task-1",
        title="给导师发送修改后的表格",
        original_text="给王老师发一下改完的表格",
        features=make_features(),
        field_evidence={
            "estimated_total_minutes": FieldEvidence(
                SourceKind.AI_ESTIMATED, "按发送前检查流程暂估"
            )
        },
        needs_confirmation=("estimated_total_minutes",),
    )

    assert task.title == "给导师发送修改后的表格"
    assert task.original_text == "给王老师发一下改完的表格"
    assert task.task_ref == "input-task-1"


def test_document_can_hold_multiple_independent_task_cards():
    first = TaskUnderstanding(
        "input-task-1", "完成计组实验3", "完成计组实验3",
        make_features(), {}, ()
    )
    second = TaskUnderstanding(
        "input-task-2", "背单词", "背半小时单词",
        replace(
            make_features(),
            location_requirement=LocationRequirement.NO_SPECIFIC_LOCATION,
            attention_required=AttentionLevel.LOW,
        ),
        {}, ()
    )
    question = ClarificationQuestion(
        "q1",
        "input-task-1",
        "location_requirement",
        "是否必须在机房完成？",
        False,
        ("需要", "不需要"),
    )

    document = TaskUnderstandingDocument(
        SCHEMA_VERSION,
        "完成计组实验3，再背半小时单词",
        (first, second),
        (question,),
    )

    assert [task.task_ref for task in document.tasks] == [
        "input-task-1",
        "input-task-2",
    ]
    assert document.original_user_input == "完成计组实验3，再背半小时单词"


def test_features_use_free_text_requirements_not_task_type_enum():
    features = make_features()

    assert features.environment_requirements == ("安静环境",)
    assert features.equipment_requirements == ("电脑",)
    assert not hasattr(features, "task_type")
