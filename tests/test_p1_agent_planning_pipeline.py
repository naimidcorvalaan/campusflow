import json

import pytest

from src.p1_agent_planning_models import AgentPlanStatus
from src.p1_agent_planning_pipeline import (
    AgentOutputError,
    parse_agent_output,
    parse_agent_plan,
    run_p1_agent_planning,
)


def plan_output(title="背单词", minutes=30, walk=(10, 20), extra=None):
    value = {
        "status": "proposed",
        "headline": "先完成当前最合适的任务。",
        "steps": [{"title": title, "minutes": minutes,
                   "timing_note": "完成后预留时间出发"}],
        "estimated_walking_minutes_min": walk[0] if walk else None,
        "estimated_walking_minutes_max": walk[1] if walk else None,
        "assumptions": ["步行时间为 AI 暂估"],
        "warnings": ["尚未使用真实天津大学路线数据"],
        "question": None,
        "quick_options": [],
    }
    if extra:
        value.update(extra)
    return json.dumps(value, ensure_ascii=False)


class SequenceCaller(object):
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def test_tolerant_plan_accepts_markdown_surrounding_text_and_unknown_key():
    raw = "说明如下：\n```json\n%s\n```" % plan_output(extra={"harmless_note": "ignored"})
    draft = parse_agent_plan(raw)
    assert draft.status is AgentPlanStatus.PROPOSED
    assert draft.steps[0].title == "背单词"


@pytest.mark.parametrize("minutes", [True, 0, -1, 1441])
def test_tolerant_plan_still_rejects_invalid_minutes(minutes):
    with pytest.raises(AgentOutputError):
        parse_agent_plan(plan_output(minutes=minutes))


def test_reviewer_accepts_and_normal_fallback_uses_three_calls():
    caller = SequenceCaller([
        plan_output(),
        '{"decision":"accept","feedback":null}',
        "建议先背 30 分钟单词，再预留时间出发。",
    ])
    result = run_p1_agent_planning(
        {"original_user_input": "想背单词"}, caller, caller, caller, caller)
    assert result.call_count == 3 and not result.revised
    assert result.view.status_title == "当前方案"
    assert "背" in result.view.headline


def test_reviewer_requests_exactly_one_revision_then_presenter():
    caller = SequenceCaller([
        plan_output(minutes=120),
        '{"decision":"revise","feedback":"长任务先安排一个片段"}',
        plan_output(title="推进计组实验3", minutes=60),
        "建议先推进 60 分钟计组实验。",
    ])
    result = run_p1_agent_planning(
        {"original_user_input": "完成计组实验3"}, caller, caller, caller, caller)
    assert result.call_count == 4 and result.revised
    assert result.draft.steps[0].minutes == 60
    assert len(caller.calls) == 4


def test_presenter_failure_uses_safe_deterministic_headline():
    caller = SequenceCaller([
        plan_output(), '{"decision":"accept"}',
        RuntimeError("Authorization Bearer https://invalid"),
    ])
    result = run_p1_agent_planning({}, caller, caller, caller, caller)
    assert result.call_count == 3
    assert result.view is not None and result.view.headline == "先完成当前最合适的任务。"
    visible = result.view.headline + result.safe_summary
    assert "Authorization" not in visible and "https://" not in visible


def test_unknown_route_is_displayed_as_ai_estimated_range():
    caller = SequenceCaller([
        plan_output(walk=(12, 20)), '{"decision":"accept"}', "先背单词。",
    ])
    result = run_p1_agent_planning({}, caller, caller, caller, caller)
    assert result.view.route_data_notice.level == "warning"
    assert "AI 暂估步行约 12～20 分钟" in result.view.route_data_notice.message
    assert "真实天津大学路线数据" in result.view.route_data_notice.message


def test_generator_failure_is_the_only_case_without_fallback_plan():
    caller = SequenceCaller([RuntimeError("API_KEY raw response")])
    result = run_p1_agent_planning({}, caller, caller, caller, caller)
    assert result.status is AgentPlanStatus.FAILED
    # Generator, rescue and final direct answer are each allowed once.
    assert result.view is None and result.call_count == 3


def test_reviser_with_empty_steps_cannot_replace_generator_actionable_plan():
    empty_revision = '{"status":"proposed","headline":"已审查方案整理完成","steps":[]}'
    caller = SequenceCaller([
        plan_output(title="背单词", minutes=30),
        '{"decision":"revise","feedback":"补充缓冲"}',
        empty_revision,
        "已审查方案整理完成",
    ])
    result = run_p1_agent_planning({}, caller, caller, caller, caller)
    assert result.revised is False
    assert result.draft.steps[0].title == "背单词"
    assert result.view.primary_card.steps[0].planned_minutes == 30


def test_plain_chinese_generator_plan_is_actionable_and_rendered_without_json():
    caller = SequenceCaller([
        "现在先背单词 30 分钟，之后预留 20 分钟步行和缓冲再出发。",
        '{"decision":"accept"}',
        "先背 30 分钟单词。",
    ])
    result = run_p1_agent_planning({}, caller, caller, caller, caller)
    assert result.view is not None
    assert result.draft.steps == ()
    assert "背单词 30 分钟" in result.view.primary_card.rationale
    assert result.view.primary_card.total_task_minutes is None


def test_empty_generator_uses_one_rescue_generation_within_budget():
    caller = SequenceCaller([
        '{"status":"needs_information","headline":"方案待补充信息"}',
        plan_output(title="整理实验报告", minutes=20),
        '{"decision":"accept"}',
        "先整理 20 分钟实验报告。",
    ])
    result = run_p1_agent_planning({}, caller, caller, caller, caller)
    assert result.call_count == 4
    assert result.view.primary_card.steps[0].title == "整理实验报告"
    assert "直接说现在做什么" in caller.calls[1][0]


def test_empty_title_or_notices_never_become_an_actionable_card():
    caller = SequenceCaller([
        '{"status":"proposed","headline":"方案待补充信息","warnings":["路线未知"]}',
        '{"status":"needs_information","headline":"仍需信息"}',
    ])
    result = run_p1_agent_planning({}, caller, caller, caller, caller)
    assert result.view is None


@pytest.mark.parametrize("text", [
    "现在先背单词半小时，之后预留 20 分钟准备出发。",
    "# 临时安排\n1. 先整理实验报告 20 分钟\n2. 再预留 15 分钟出发。",
    "立即开始计组实验 60～90分钟，提前出发。",
])
def test_natural_chinese_and_markdown_plans_are_actionable_without_json(text):
    draft = parse_agent_output(text)
    assert draft.plan_text == text
    assert draft.steps == ()


def test_direct_answer_rescues_two_non_actionable_agent_responses():
    caller = SequenceCaller([
        "方案待补充信息", "已审查方案整理完成",
        "现在先背单词 30 分钟，再整理实验报告 20 分钟；AI 暂估步行 15～25 分钟，提前出发。",
        "可以继续",
    ])
    result = run_p1_agent_planning(
        {"original_user_input": "我有90分钟，想背单词并整理实验报告",
         "reference_datetime": "2026-08-17T10:00:00"},
        caller, caller, caller, caller, max_calls=4)
    assert result.view is not None
    assert "背单词 30 分钟" in result.view.primary_card.rationale
    assert result.call_count == 4
    assert any(item.stage.value == "agent_direct_answer" and item.status.value == "success"
               for item in result.diagnostics)
    assert "不要返回 JSON" in caller.calls[0][0]
    assert "不要返回 JSON" in caller.calls[2][0]


def test_reviewer_and_empty_presenter_cannot_discard_natural_plan_text():
    plan_text = "现在先背单词 30 分钟，提前出发。"
    caller = SequenceCaller([plan_text, "可以继续", ""])
    result = run_p1_agent_planning({}, caller, caller, caller, caller)
    assert result.draft.plan_text == plan_text
    assert result.view.primary_card.rationale == plan_text
    assert result.review_status.value == "accept"
