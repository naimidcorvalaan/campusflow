"""P2d 第二阶段：Day Intake 测试（首次自然语言 -> DayPlanningState）。

覆盖：解析校验、程序构造 state、相对时间、来源标记、repair 边界、
未知时长交给 P2c 暂估、输入不可变与稳定 ref。
"""

from datetime import datetime

import pytest

from src.p1_models import SourceKind
from src.p2_agentic_parser import AgenticParseError
from src.p2_agentic_pipeline import run_p2_agentic_day_planning
from src.p2_day_intake import (
    DEFAULT_DAY_END,
    INTAKE_SCHEMA_VERSION,
    apply_day_intake,
    build_day_intake_prompt,
    parse_day_intake,
    run_day_intake,
)
from src.p2_models import TaskState


def dt(h, m=0):
    return datetime(2026, 9, 1, h, m)


REFERENCE = dt(9)


def _v(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return '"{}"'.format(value)


def _json_list(items):
    return "[" + ", ".join('"{}"'.format(item) for item in items) + "]"


def c_json(**kw):
    base = {
        "title": None, "starts_at": None, "ends_at": None,
        "starts_in_minutes": None, "duration_minutes": None,
        "relative_end_minutes": None, "location_text": None,
    }
    base.update(kw)
    return "{" + ", ".join(
        '"{}": {}'.format(key, _v(value)) for key, value in base.items()
    ) + "}"


def t_json(**kw):
    base = {
        "title": None, "total_minutes": None, "is_splittable": None,
        "minimum_slice_minutes": None, "location_text": None, "activity_kind": None,
        "duration_source": None, "after_commitment_index": None,
        "meal_period": None, "meal_before_commitment_index": None, "meal_time": None,
    }
    base.update(kw)
    return "{" + ", ".join(
        '"{}": {}'.format(key, _v(value)) for key, value in base.items()
    ) + "}"


def intake_json(commitments, tasks, day_end="22:00", questions=()):
    return (
        '{"schema_version": "p2.day-intake.v1", "day_end": ' + _v(day_end)
        + ', "commitments": [' + ", ".join(commitments)
        + '], "tasks": [' + ", ".join(tasks)
        + '], "questions": ' + _json_list(questions) + "}"
    )


def make_proposal(commitments, tasks, day_end="22:00", questions=()):
    return parse_day_intake(intake_json(commitments, tasks, day_end, questions))


class ScriptedCaller:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        if not self.outputs:
            raise AssertionError("unexpected extra model call")
        return self.outputs.pop(0)


# ---------------------------------------------------------------------------
# 解析校验
# ---------------------------------------------------------------------------


def test_parse_basic_proposal():
    proposal = make_proposal(
        [c_json(title="上课", starts_at="10:00", ends_at="11:30")],
        [t_json(title="背单词", total_minutes=30)],
    )
    assert proposal.schema_version == INTAKE_SCHEMA_VERSION
    assert proposal.commitments[0].title == "上课"
    assert proposal.commitments[0].starts_at == "10:00"
    assert proposal.tasks[0].total_minutes == 30
    assert proposal.day_end == "22:00"


def test_parse_rejects_bool_as_int():
    with pytest.raises(AgenticParseError):
        make_proposal(
            [c_json(title="上课", starts_at="10:00", ends_at="11:30")],
            [t_json(title="背单词", total_minutes=True)],
        )


def test_parse_rejects_bad_time_format():
    with pytest.raises(AgenticParseError):
        make_proposal(
            [c_json(title="上课", starts_at="25:00", ends_at="11:30")],
            [t_json(title="背单词")],
        )


def test_parse_rejects_commitment_without_start():
    with pytest.raises(AgenticParseError):
        make_proposal([c_json(title="上课", ends_at="11:30")], [t_json(title="背单词")])


def test_parse_rejects_both_absolute_and_relative():
    with pytest.raises(AgenticParseError):
        make_proposal(
            [c_json(title="上课", starts_at="10:00", starts_in_minutes=90, ends_at="11:30")],
            [t_json(title="背单词")],
        )


def test_parse_rejects_ends_and_duration_together():
    with pytest.raises(AgenticParseError):
        make_proposal(
            [c_json(title="上课", starts_at="10:00", ends_at="11:30", duration_minutes=60)],
            [t_json(title="背单词")],
        )


def test_parse_rejects_multiple_end_forms():
    with pytest.raises(AgenticParseError):
        make_proposal(
            [c_json(title="上课", starts_at="21:00", duration_minutes=90,
                    relative_end_minutes=90)],
            [],
        )


def test_prompt_defines_relative_end_without_model_time_arithmetic():
    system, _ = build_day_intake_prompt(REFERENCE, "21点上课，一个半小时后下课")
    assert "relative_end_minutes" in system
    assert "不要自行计算22:30" in system


def test_parse_meal_temporal_semantics_uses_proposal_local_commitment_indexes():
    proposal = make_proposal(
        [c_json(title="上课", starts_at="17:00", ends_at="18:30")],
        [t_json(
            title="吃饭", activity_kind="meal", meal_period="unspecified",
            meal_before_commitment_index=1,
        )],
    )
    meal = proposal.tasks[0]
    assert meal.meal_period == "unspecified"
    assert meal.meal_before_commitment_index == 1
    assert meal.after_commitment_index is None


def test_parse_rejects_meal_before_and_after_the_same_commitment():
    with pytest.raises(AgenticParseError):
        make_proposal(
            [c_json(title="上课", starts_at="17:00", ends_at="18:30")],
            [t_json(
                title="吃饭", activity_kind="meal",
                meal_before_commitment_index=1, after_commitment_index=1,
            )],
        )


def test_parse_rejects_missing_task_title():
    with pytest.raises(AgenticParseError):
        make_proposal(
            [c_json(title="上课", starts_at="10:00", ends_at="11:30")],
            [t_json(title=None)],
        )


def test_parse_rejects_bad_schema_version():
    text = intake_json(
        [c_json(title="上课", starts_at="10:00", ends_at="11:30")], [t_json(title="背单词")]
    ).replace("p2.day-intake.v1", "p2.day-intake.v0")
    with pytest.raises(AgenticParseError):
        parse_day_intake(text)


def test_parse_rejects_splittable_not_bool():
    with pytest.raises(AgenticParseError):
        make_proposal(
            [c_json(title="上课", starts_at="10:00", ends_at="11:30")],
            [t_json(title="背单词", is_splittable=1)],
        )


# ---------------------------------------------------------------------------
# 程序构造 state
# ---------------------------------------------------------------------------


def test_apply_scenario_a_windows_and_refs():
    applied = apply_day_intake(
        REFERENCE,
        make_proposal(
            [c_json(title="上课", starts_at="10:00", ends_at="11:30"),
             c_json(title="组会", starts_at="14:00", ends_at="15:00")],
            [t_json(title="计组实验", total_minutes=120),
             t_json(title="背单词", total_minutes=30)],
        ),
    )
    state = applied.state
    assert [c.commitment_ref for c in state.commitments] == [
        "day_commitment_001", "day_commitment_002"
    ]
    assert [t.task_ref for t in state.tasks] == ["day_task_001", "day_task_002"]
    assert applied.new_commitment_refs == ("day_commitment_001", "day_commitment_002")
    assert applied.new_task_refs == ("day_task_001", "day_task_002")
    assert len(state.windows) >= 2
    assert state.windows[0].starts_at == REFERENCE
    assert state.day_end == dt(22)


def test_apply_scenario_b_user_duration_source():
    applied = apply_day_intake(
        REFERENCE,
        make_proposal(
            [c_json(title="上课", starts_at="10:00", ends_at="11:30")],
            [t_json(title="计组实验", total_minutes=120)],
        ),
    )
    task = applied.state.tasks[0]
    assert task.total_minutes == 120
    assert task.total_source == SourceKind.AI_EXTRACTED_FROM_USER_TEXT
    assert task.total_source != SourceKind.AI_ESTIMATED
    assert task.completed_minutes == 0
    assert task.state == TaskState.ACTIVE


def test_apply_scenario_c_unknown_duration_stays_none():
    applied = apply_day_intake(
        REFERENCE,
        make_proposal(
            [c_json(title="上课", starts_at="10:00", ends_at="11:30")],
            [t_json(title="整理数据库实验")],
        ),
    )
    task = applied.state.tasks[0]
    assert task.total_minutes is None
    assert task.total_source is None
    assert task.remaining_minutes is None


def test_apply_scenario_d_relative_time():
    applied = apply_day_intake(
        REFERENCE,
        make_proposal(
            [c_json(title="上课", starts_in_minutes=90, duration_minutes=60)],
            [t_json(title="写实验报告")],
        ),
    )
    commitment = applied.state.commitments[0]
    assert commitment.starts_at == dt(10, 30)
    assert commitment.ends_at == dt(11, 30)


def test_apply_missing_end_keeps_partial_commitment_and_asks_only_end():
    # P3e 真实模型容错：只有 starts_at（无 ends_at）也必须保留已解析事实
    applied = apply_day_intake(
        REFERENCE,
        make_proposal(
            [c_json(title="上课", starts_at="10:00")],
            [t_json(title="背单词")],
        ),
    )
    assert len(applied.state.commitments) == 1
    commitment = applied.state.commitments[0]
    assert commitment.starts_at == dt(10)
    assert commitment.ends_at is None
    assert applied.warnings
    assert applied.questions
    assert "上课" in applied.questions[0]
    # 只问真正缺失的结束时间，绝不再问“开始与结束时间”
    assert "开始" not in applied.questions[0]
    assert "结束" in applied.questions[0]


def test_apply_start_after_end_rejected():
    applied = apply_day_intake(
        REFERENCE,
        make_proposal(
            [c_json(title="上课", starts_at="15:00", ends_at="14:00")],
            [t_json(title="背单词")],
        ),
    )
    assert applied.state.commitments == ()
    assert applied.warnings
    assert applied.questions


def test_apply_location_preserved():
    # 承诺地点进入 FixedCommitment；任务地点保留在解析层提案中
    # （P2a TaskProgress 暂无地点字段，不伪造存储）。
    proposal = make_proposal(
        [c_json(title="上课", starts_at="10:00", ends_at="11:30", location_text="图书馆")],
        [t_json(title="整理实验报告", location_text="宿舍")],
    )
    applied = apply_day_intake(REFERENCE, proposal)
    assert applied.state.commitments[0].location_text == "图书馆"
    assert proposal.tasks[0].location_text == "宿舍"


def test_apply_stable_refs_across_runs():
    proposal = make_proposal(
        [c_json(title="上课", starts_at="10:00", ends_at="11:30")],
        [t_json(title="背单词", total_minutes=30)],
    )
    first = apply_day_intake(REFERENCE, proposal)
    second = apply_day_intake(REFERENCE, proposal)
    assert first.new_commitment_refs == second.new_commitment_refs == ("day_commitment_001",)
    assert first.new_task_refs == second.new_task_refs == ("day_task_001",)
    assert first.state == second.state


def test_apply_input_immutable():
    proposal = make_proposal(
        [c_json(title="上课", starts_at="10:00", ends_at="11:30")],
        [t_json(title="背单词", total_minutes=30)],
    )
    before = proposal
    reference_before = REFERENCE
    apply_day_intake(REFERENCE, proposal)
    assert proposal is before
    assert REFERENCE == reference_before
    assert proposal.commitments[0].starts_at == "10:00"


def test_apply_custom_day_end():
    applied = apply_day_intake(
        REFERENCE,
        make_proposal(
            [c_json(title="上课", starts_at="10:00", ends_at="11:30")],
            [t_json(title="背单词")],
            day_end="23:00",
        ),
    )
    assert applied.state.day_end == dt(23)


# ---------------------------------------------------------------------------
# 编排：run_day_intake
# ---------------------------------------------------------------------------


INTAKE_OK = intake_json(
    [c_json(title="上课", starts_at="10:00", ends_at="11:30")],
    [t_json(title="计组实验", total_minutes=120)],
)


def test_run_intake_success():
    outcome = run_day_intake(REFERENCE, "10点上课，今天做计组实验。", ScriptedCaller([INTAKE_OK]))
    assert outcome.applied is not None
    assert outcome.call_count == 1
    assert not outcome.repair_used
    assert outcome.applied.state.tasks[0].total_minutes == 120


def test_run_intake_repair_recovers():
    outcome = run_day_intake(
        REFERENCE,
        "10点上课，今天做计组实验。",
        ScriptedCaller(["{not json", INTAKE_OK]),
    )
    assert outcome.applied is not None
    assert outcome.call_count == 2
    assert outcome.repair_used
    assert len(outcome.applied.state.commitments) == 1


def test_intake_repair_retains_one_contract_and_original_time_facts():
    import json
    caller=ScriptedCaller(['{not json',INTAKE_OK])
    source='10:00到11:30上课，计组实验120分钟。'
    outcome=run_day_intake(REFERENCE,source,caller)
    assert outcome.applied and outcome.call_count==2
    assert caller.calls[0][0]==caller.calls[1][0]
    context=json.loads(caller.calls[1][1])
    assert source in context['original_context']
    assert context['previous_response']=='{not json' and context['validation_error']
    assert outcome.applied.state.commitments[0].ends_at==dt(11,30)
    from src.p2_day_intake import commitment_end_contract
    assert commitment_end_contract() in caller.calls[1][0]


def test_run_intake_repair_fails_fallback():
    outcome = run_day_intake(
        REFERENCE,
        "10点上课，今天做计组实验。",
        ScriptedCaller(["{not json", "也 不是 json"]),
    )
    assert outcome.applied is None
    assert outcome.warnings
    assert outcome.call_count == 2
    assert outcome.repair_used


def test_run_intake_requires_reference_and_text():
    with pytest.raises(TypeError):
        run_day_intake("2026-09-01 09:00", "今天做实验", ScriptedCaller([INTAKE_OK]))
    with pytest.raises(ValueError):
        run_day_intake(REFERENCE, "   ", ScriptedCaller([INTAKE_OK]))


def test_prompt_contains_reference_and_examples():
    system, user = build_day_intake_prompt(REFERENCE, "90分钟后上课。")
    assert "2026-09-01 09:00" in user
    assert DEFAULT_DAY_END in system
    assert "starts_in_minutes" in system
    assert "参考时间由程序提供" in system
    assert "DayPlanningState(" not in system
    assert "object at" not in system
    assert "FixedCommitment(" not in system


# ---------------------------------------------------------------------------
# 场景 C 集成：未知时长交给 P2c Day Plan Agent 暂估
# ---------------------------------------------------------------------------

REC_NONE = (
    '{"schema_version": "p2.task-reconciliation.v1", "updates": [], "questions": []}'
)
PLAN_ESTIMATE = (
    '{"schema_version": "p2.day-plan-intent.v1", "task_order": [], '
    '"include_low_attention": false, "task_estimates": ['
    '{"task_ref": "day_task_001", "estimated_total_minutes": 100, '
    '"is_splittable": true, "minimum_slice_minutes": 30}], "rationale": null}'
)
REVIEW_ACCEPT = (
    '{"schema_version": "p2.day-review.v1", "decision": "accept", '
    '"reason": "ok", "suggested_task_order": null, "include_low_attention": null}'
)


def _pipeline_caller(rec, plan, review):
    def caller(system, user):
        if "task-reconciliation" in system:
            return rec
        if "day-plan-intent" in system:
            return plan
        if "day-review" in system:
            return review
        return rec

    return caller


def test_unknown_duration_estimated_by_p2c():
    applied = apply_day_intake(
        REFERENCE,
        make_proposal(
            [c_json(title="上课", starts_at="10:00", ends_at="11:30")],
            [t_json(title="整理数据库实验")],
        ),
    )
    assert applied.state.tasks[0].total_minutes is None
    result = run_p2_agentic_day_planning(
        applied.state,
        "开始全天计划",
        _pipeline_caller(REC_NONE, PLAN_ESTIMATE, REVIEW_ACCEPT),
    )
    task = result.updated_state.tasks[0]
    assert task.total_minutes == 100
    assert task.total_source == SourceKind.AI_ESTIMATED
    assert task.completed_minutes == 0
    assert result.allocation_plan.total_planned_minutes == 100


@pytest.mark.parametrize("title,kind,location", [
    ("吃饭", "meal", None), ("写报告", "generic", "图书馆"),
    ("吃饭", "meal", "学三食堂"), ("背单词", "generic", None),
])
def test_task_semantic_fields_parse_with_backward_compatible_optionality(title, kind, location):
    proposal = parse_day_intake(intake_json([], [t_json(title=title, activity_kind=kind, location_text=location)]))
    task = proposal.tasks[0]
    assert task.activity_kind == kind
    assert task.location_text == location


def test_task_semantic_fields_are_backward_compatible_when_absent_from_old_json():
    text = intake_json(
        [],
        [
            '{"title": "背单词", "total_minutes": null, "is_splittable": null, '
            '"minimum_slice_minutes": null, "location_text": null}'
        ],
    )
    task = parse_day_intake(text).tasks[0]
    assert task.activity_kind is None
    assert task.location_text is None


def test_task_semantic_kind_rejects_unknown_value():
    with pytest.raises(AgenticParseError):
        parse_day_intake(intake_json([], [t_json(title="写报告", activity_kind="study")]))


def test_task_duration_source_preserves_explicit_and_semantic_intake_facts():
    proposal = parse_day_intake(intake_json([], [
        t_json(title="吃20分钟饭", total_minutes=20, activity_kind="meal", duration_source="user_explicit"),
        t_json(title="快速吃点", total_minutes=15, activity_kind="meal", duration_source="semantic_estimate"),
    ]))
    assert proposal.tasks[0].duration_source == "user_explicit"
    assert proposal.tasks[1].duration_source == "semantic_estimate"
