import pytest

from src.extraction_models import ParseResult
from src.models import PlanningRequest, Task
from src.planning_pipeline import PlanningPipelineResult
from src.result_formatter import (
    _single_line_text,
    _task_type_label,
    format_planning_result,
)
from src.route_plan import RoutePlanResult
from src.schedule_checker import ScheduleCheckResult, ScheduledTask
from src.shortest_path import ShortestPathResult
from src.task_selector import TaskSelectionResult


def make_parse_result(status="ok", questions=None):
    return ParseResult(
        status=status,
        error_type=None,
        message="解析完成",
        questions=questions or [],
        planning_request=PlanningRequest(
            current_location="dormitory",
            destination="gate",
            current_time="09:00",
            tasks=[],
        ),
    )


def make_task(location="canteen", deadline="09:30"):
    return Task(
        location=location,
        description="吃饭",
        is_mandatory=True,
        estimated_duration_minutes=15,
        deadline=deadline,
    )


def make_pipeline_result(status="ok", deadline="09:30", deadline_met=True):
    task = make_task(deadline=deadline)
    segment_one = ShortestPathResult(
        start_id="dormitory",
        destination_id="canteen",
        path=("dormitory", "canteen"),
        total_minutes=6,
    )
    segment_two = ShortestPathResult(
        start_id="canteen",
        destination_id="gate",
        path=("canteen", "dormitory", "library", "gate"),
        total_minutes=26,
    )
    route_plan = RoutePlanResult(
        visit_order=("dormitory", "canteen", "gate"),
        walking_minutes=32,
        stay_minutes=15,
        total_minutes=47,
        segments=(segment_one, segment_two),
        tasks=(task,),
    )
    scheduled_task = ScheduledTask(
        task=task,
        location_id="canteen",
        arrival_time="09:06",
        finish_time="09:21",
        deadline=deadline,
        is_deadline_met=deadline_met,
    )
    schedule_result = ScheduleCheckResult(
        is_feasible=deadline_met is not False,
        start_time="09:00",
        finish_time="09:47",
        scheduled_tasks=(scheduled_task,),
        missed_deadlines=(scheduled_task,) if deadline_met is False else (),
    )
    return PlanningPipelineResult(
        parse_result=make_parse_result(),
        route_plan=route_plan,
        schedule_result=schedule_result,
        status=status,
        message="规划完成",
    )


def test_ok_formats_route_and_all_duration_fields():
    text = format_planning_result(make_pipeline_result())

    assert "推荐路线：dormitory → canteen → gate" in text
    assert "预计步行：32 分钟" in text
    assert "任务停留：15 分钟" in text
    assert "预计总耗时：47 分钟" in text


def test_ok_formats_each_task_schedule():
    text = format_planning_result(make_pipeline_result())

    assert "任务安排：" in text
    assert "到达 canteen：09:06" in text
    assert "完成：09:21" in text
    assert "截止：09:30" in text
    assert "状态：可按时完成" in text
    assert "任务：吃饭" in text


def test_none_deadline_displays_no_deadline():
    text = format_planning_result(
        make_pipeline_result(deadline=None, deadline_met=None)
    )

    assert "截止：无截止时间" in text
    assert "状态：无需检查" in text


def test_infeasible_lists_only_overdue_tasks_and_minutes():
    text = format_planning_result(
        make_pipeline_result(
            status="infeasible", deadline="09:20", deadline_met=False
        )
    )

    assert "路线仍可生成" in text
    assert "超时任务：" in text
    assert "- canteen：超时 1 分钟" in text


def test_needs_clarification_lists_questions():
    result = PlanningPipelineResult(
        parse_result=make_parse_result(
            status="needs_clarification",
            questions=["你现在在哪里？", "最终要去哪里？"],
        ),
        route_plan=None,
        schedule_result=None,
        status="needs_clarification",
        message="需要补充信息",
    )

    text = format_planning_result(result)

    assert "需要补充信息" in text
    assert "1. 你现在在哪里？" in text
    assert "2. 最终要去哪里？" in text


def test_error_uses_safe_chinese_text_without_trace_or_repr():
    result = PlanningPipelineResult(
        parse_result=make_parse_result(status="rejected"),
        route_plan=None,
        schedule_result=None,
        status="error",
        message="Traceback: RoutePlanResult(secret='api-key-value')",
    )

    text = format_planning_result(result)

    assert text == "规划失败：请检查输入信息后重试。"
    assert "Traceback" not in text
    assert "RoutePlanResult(" not in text
    assert "api-key-value" not in text


def make_error_result(message):
    return PlanningPipelineResult(
        parse_result=make_parse_result(status="rejected"),
        route_plan=None,
        schedule_result=None,
        status="error",
        message=message,
    )


def test_error_filters_bearer_credential():
    text = format_planning_result(make_error_result("认证失败：Bearer secret-token"))

    assert text == "规划失败：请检查输入信息后重试。"
    assert "Bearer" not in text
    assert "secret-token" not in text


def test_error_filters_api_key_with_underscore():
    text = format_planning_result(make_error_result("认证失败：api_key=secret"))

    assert text == "规划失败：请检查输入信息后重试。"
    assert "api_key" not in text


def test_error_filters_api_key_with_hyphen():
    text = format_planning_result(make_error_result("认证失败：api-key=secret"))

    assert text == "规划失败：请检查输入信息后重试。"
    assert "api-key" not in text


def test_error_filters_embedded_json_object():
    text = format_planning_result(
        make_error_result('解析失败：{"token":"secret"}')
    )

    assert text == "规划失败：请检查输入信息后重试。"
    assert "token" not in text
    assert "secret" not in text


def test_error_filters_embedded_json_array():
    text = format_planning_result(
        make_error_result('解析失败：["secret", "detail"]')
    )

    assert text == "规划失败：请检查输入信息后重试。"
    assert "secret" not in text
    assert "detail" not in text


def test_normal_chinese_error_is_preserved():
    text = format_planning_result(
        make_error_result("地点未知，请检查输入。")
    )

    assert text == "规划失败：地点未知，请检查输入。"


def test_overlong_error_uses_safe_fallback():
    text = format_planning_result(make_error_result("错误详情" * 60))

    assert text == "规划失败：请检查输入信息后重试。"


def test_empty_tasks_format_normally():
    route_plan = RoutePlanResult(
        visit_order=("dormitory", "gate"),
        walking_minutes=20,
        stay_minutes=0,
        total_minutes=20,
        segments=(),
        tasks=(),
    )
    schedule_result = ScheduleCheckResult(
        is_feasible=True,
        start_time="09:00",
        finish_time="09:20",
        scheduled_tasks=(),
        missed_deadlines=(),
    )
    result = PlanningPipelineResult(
        parse_result=make_parse_result(),
        route_plan=route_plan,
        schedule_result=schedule_result,
        status="ok",
        message="规划完成",
    )

    text = format_planning_result(result)

    assert "推荐路线：dormitory → gate" in text
    assert "任务停留：0 分钟" in text
    assert "任务安排：无" in text


def test_output_never_contains_result_object_repr():
    text = format_planning_result(make_pipeline_result())

    assert "PlanningPipelineResult(" not in text
    assert "RoutePlanResult(" not in text
    assert "ScheduleCheckResult(" not in text


def attach_selection(result, kept_tasks=(), dropped_tasks=(), feasible=True):
    selection = TaskSelectionResult(
        is_feasible=feasible,
        kept_tasks=tuple(kept_tasks),
        dropped_tasks=tuple(dropped_tasks),
        route_plan=result.route_plan,
        schedule_result=result.schedule_result,
        message="任务选择完成",
    )
    return PlanningPipelineResult(
        parse_result=result.parse_result,
        route_plan=result.route_plan,
        schedule_result=result.schedule_result,
        status=result.status,
        message=result.message,
        task_selection_result=selection,
    )


def test_ok_without_selection_keeps_legacy_format():
    text = format_planning_result(make_pipeline_result())

    assert "推荐路线" in text
    assert "保留任务：" not in text
    assert "暂时放弃" not in text
    assert "调整任务计划" not in text


def test_ok_without_dropped_tasks_says_all_tasks_are_kept():
    base = make_pipeline_result()
    result = attach_selection(base, kept_tasks=base.route_plan.tasks)

    text = format_planning_result(result)

    assert "全部任务均已保留。" in text
    assert "暂时放弃的可选任务" not in text
    assert "放弃部分" not in text


def test_ok_with_dropped_task_formats_selection_and_route():
    base = make_pipeline_result()
    mandatory = base.route_plan.tasks[0]
    optional_kept = Task("library", "借书", False, 10, None)
    optional_dropped = Task("gym", "锻炼", False, 20, "10:00")
    result = attach_selection(
        base,
        kept_tasks=(mandatory, optional_kept),
        dropped_tasks=(optional_dropped,),
    )

    text = format_planning_result(result)

    assert text.startswith("为满足截止时间，已调整任务计划。")
    assert "保留任务：" in text
    assert "- canteen：吃饭（必须）" in text
    assert "- library：借书（可选）" in text
    assert "暂时放弃的可选任务：" in text
    assert "- gym：锻炼（可选）" in text
    assert "推荐路线" in text
    assert "预计总耗时" in text


def test_dropped_and_kept_task_orders_are_preserved():
    base = make_pipeline_result()
    kept_b = Task("b", "第二个保留", False, 1, None)
    kept_a = Task("a", "第一个保留", True, 1, None)
    dropped_c = Task("c", "第一个放弃", False, 1, None)
    dropped_d = Task("d", "第二个放弃", False, 1, None)
    result = attach_selection(
        base,
        kept_tasks=(kept_b, kept_a),
        dropped_tasks=(dropped_c, dropped_d),
    )

    text = format_planning_result(result)

    assert text.index("第二个保留") < text.index("第一个保留")
    assert text.index("第一个放弃") < text.index("第二个放弃")


def test_infeasible_selection_uses_consistent_schedule_without_dropped_task():
    base = make_pipeline_result(
        status="infeasible", deadline="09:20", deadline_met=False
    )
    mandatory = base.route_plan.tasks[0]
    dropped = Task("gym", "锻炼", False, 10, "09:10")
    schedule = base.schedule_result
    result = PlanningPipelineResult(
        parse_result=base.parse_result,
        route_plan=base.route_plan,
        schedule_result=schedule,
        status="infeasible",
        message=base.message,
        task_selection_result=TaskSelectionResult(
            False,
            (mandatory,),
            (dropped,),
            base.route_plan,
            schedule,
            "不可行",
        ),
    )

    text = format_planning_result(result)
    final_schedule = text.split("推荐路线", 1)[-1]

    assert "已放弃全部可选任务，但必须任务仍无法满足截止时间。" in text
    assert "- canteen：吃饭（必须）" in text
    assert "- gym：锻炼（可选）" in text
    assert "路线仍可生成" in text
    assert "超时任务：" in text
    assert "gym" not in final_schedule


def test_infeasible_without_dropped_tasks_has_no_false_dropped_list():
    base = make_pipeline_result(
        status="infeasible", deadline="09:20", deadline_met=False
    )
    result = attach_selection(
        base,
        kept_tasks=base.route_plan.tasks,
        feasible=False,
    )

    text = format_planning_result(result)

    assert "必须任务仍无法满足截止时间。" in text
    assert "暂时放弃的可选任务" not in text
    assert "已放弃全部可选任务" not in text


def test_clarification_and_error_do_not_show_selection_details():
    base = make_pipeline_result()
    optional = Task("gym", "锻炼", False, 10, None)
    selection = TaskSelectionResult(
        True, (), (optional,), base.route_plan, base.schedule_result, "internal"
    )
    clarification = PlanningPipelineResult(
        make_parse_result("needs_clarification", ["请补充起点"]),
        None,
        None,
        "needs_clarification",
        "需要补充任务信息。",
        selection,
    )
    error = PlanningPipelineResult(
        make_parse_result("rejected"),
        None,
        None,
        "error",
        "地点未知，请检查输入。",
        selection,
    )

    for text in (
        format_planning_result(clarification),
        format_planning_result(error),
    ):
        assert "保留任务：" not in text
        assert "暂时放弃" not in text
        assert "gym" not in text


def test_task_text_is_cleaned_without_modifying_task():
    base = make_pipeline_result()
    task = Task(
        "  图书\n  馆  ",
        "  归还\n\n  图书   ",
        True,
        10,
        None,
    )
    original = vars(task).copy()
    result = attach_selection(base, kept_tasks=(task,), dropped_tasks=(
        Task(" ", "\n", False, 1, None),
    ))

    text = format_planning_result(result)

    assert "- 图书 馆：归还 图书（必须）" in text
    assert "- 未说明：未说明（可选）" in text
    assert vars(task) == original


def test_selection_output_contains_no_python_repr_or_boolean_syntax():
    base = make_pipeline_result()
    dropped = Task("gym", "锻炼", False, 10, None)
    result = attach_selection(
        base,
        kept_tasks=base.route_plan.tasks,
        dropped_tasks=(dropped,),
    )

    text = format_planning_result(result)

    for marker in (
        "Task(",
        "TaskSelectionResult(",
        "RoutePlanResult(",
        "ScheduleCheckResult(",
        "PlanningPipelineResult(",
        "is_mandatory=True",
        "is_mandatory=False",
    ):
        assert marker not in text


def test_empty_selected_task_plan_formats_normally():
    route_plan = RoutePlanResult(("dormitory", "gate"), 20, 0, 20, (), ())
    schedule = ScheduleCheckResult(True, "09:00", "09:20", (), ())
    result = PlanningPipelineResult(
        make_parse_result(),
        route_plan,
        schedule,
        "ok",
        "规划完成",
        TaskSelectionResult(True, (), (), route_plan, schedule, "全部保留"),
    )

    text = format_planning_result(result)

    assert "全部任务均已保留。" in text
    assert "任务安排：无" in text
    assert "暂时放弃" not in text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, "必须"),
        (False, "可选"),
        (None, "任务属性异常"),
        (0, "任务属性异常"),
        (1, "任务属性异常"),
        ("true", "任务属性异常"),
        ("是", "任务属性异常"),
        ("", "任务属性异常"),
        ([], "任务属性异常"),
    ],
)
def test_task_type_label_uses_strict_booleans(value, expected):
    assert _task_type_label(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        123,
        ["secret"],
        Task("library", "借书", True, 10, None),
        make_pipeline_result(),
    ],
)
def test_single_line_text_does_not_stringify_non_strings(value):
    assert _single_line_text(value) == "未说明"


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Task(secret)",
        "TaskSelectionResult(secret)",
        "RoutePlanResult(secret)",
        "ScheduleCheckResult(secret)",
        "PlanningPipelineResult(secret)",
        "is_mandatory=True",
        "tAsKsElEcTiOnReSuLt(secret)",
        "IS_MANDATORY=false",
    ],
)
def test_single_line_text_hides_internal_code_markers(unsafe_text):
    assert _single_line_text(unsafe_text) == "内容已隐藏"


def test_single_line_text_truncates_to_two_hundred_characters():
    original = "长" * 250

    displayed = _single_line_text(original)

    assert len(displayed) == 200
    assert displayed == "长" * 198 + "……"
    assert original == "长" * 250


def test_single_line_text_preserves_normal_chinese():
    assert _single_line_text("  图书馆 归还图书  ") == "图书馆 归还图书"


def test_invalid_task_attribute_is_not_labeled_mandatory_or_optional():
    base = make_pipeline_result()
    invalid = Task("library", "借书", None, 10, None)
    result = attach_selection(
        base,
        kept_tasks=(invalid,),
        dropped_tasks=(Task("gym", "锻炼", False, 10, None),),
    )

    text = format_planning_result(result)

    assert "- library：借书（任务属性异常）" in text
    assert "- library：借书（必须）" not in text
    assert "- library：借书（可选）" not in text
    assert "- gym：锻炼（可选）" in text


def test_task_fields_with_internal_markers_are_hidden_without_mutation():
    base = make_pipeline_result()
    task = Task(
        "TaskSelectionResult(secret)",
        "is_mandatory=True",
        True,
        10,
        None,
    )
    original = vars(task).copy()
    result = attach_selection(
        base,
        kept_tasks=(task,),
        dropped_tasks=(Task("gym", "锻炼", False, 10, None),),
    )

    text = format_planning_result(result)

    assert "- 内容已隐藏：内容已隐藏（必须）" in text
    assert "TaskSelectionResult(" not in text
    assert "is_mandatory=" not in text
    assert vars(task) == original


def test_equal_valued_kept_and_dropped_tasks_are_both_displayed():
    base = make_pipeline_result()
    kept = Task("library", "借书", False, 10, None)
    dropped = Task("library", "借书", False, 10, None)
    assert kept == dropped
    assert kept is not dropped
    result = attach_selection(
        base, kept_tasks=(kept,), dropped_tasks=(dropped,)
    )

    text = format_planning_result(result)

    assert text.count("- library：借书（可选）") == 2


def test_formatter_faithfully_displays_schedule_entries_in_original_order():
    base = make_pipeline_result(
        status="infeasible", deadline="09:20", deadline_met=False
    )
    dropped = Task("gym", "锻炼", False, 10, "09:10")
    second = ScheduledTask(
        dropped, "gym", "09:20", "09:30", "09:10", False
    )
    schedule = ScheduleCheckResult(
        False,
        "09:00",
        "09:47",
        base.schedule_result.scheduled_tasks + (second,),
        base.schedule_result.missed_deadlines + (second,),
    )
    result = PlanningPipelineResult(
        base.parse_result,
        base.route_plan,
        schedule,
        "infeasible",
        base.message,
        TaskSelectionResult(
            False,
            base.route_plan.tasks,
            (dropped,),
            base.route_plan,
            schedule,
            "internal",
        ),
    )

    text = format_planning_result(result)
    schedule_text = text.split("任务安排：", 1)[-1]

    assert schedule_text.index("到达 canteen") < schedule_text.index("到达 gym")
    assert "- gym：超时 20 分钟" in text
