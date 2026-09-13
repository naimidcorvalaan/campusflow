from types import SimpleNamespace

from src.p2_expression_agents import validate_expression_facts
from src.p2_question_filter import filter_pending_questions


def _facts():
    return {
        "movement_facts": ({
            "transition_start": "14:50", "movement_start": "14:55", "start": "14:55",
            "end": "15:00", "mode": "骑行",
        },),
        "commitment_facts": ({"title": "上课", "starts_at": "15:00"},),
        "current_segment": {"kind": "task", "title": "计组实验", "start": "13:35", "end": "14:50"},
        "next_segment": {"kind": "transition", "title": "收拾东西", "start": "14:50", "end": "14:55"},
        "plan_start": "13:35", "plan_end": "15:00", "commitments": ("上课 15:00",),
        "tasks": (), "idle_gaps": (), "location_lexicon": (), "trusted_locations": (),
    }


def test_current_and_event_time_binding_guards():
    facts = _facts()
    assert not validate_expression_facts("现在准备出发去31教", facts)
    assert validate_expression_facts("先做实验，到点再出发", facts)
    assert not validate_expression_facts("14:55开始收拾东西", facts)
    assert validate_expression_facts("14:50开始收拾东西", facts)
    assert validate_expression_facts("14:55骑车出发", facts)


def test_class_prep_time_is_not_class_start():
    facts = _facts()
    facts["commitment_facts"] = ({"title": "上课", "starts_at": "15:00", "class_prep_start": "14:50"},)
    assert validate_expression_facts("14:50左右到楼后先签到、找教室", facts)
    assert not validate_expression_facts("14:50开始上课", facts)
    assert validate_expression_facts("15:00开始上课", facts)


def test_class_building_and_classroom_times_are_guarded_separately():
    facts = _facts()
    facts["commitment_facts"] = ({
        "title": "上课",
        "starts_at": "15:00",
        "class_prep_start": "14:50",
        "building_arrival": "14:50",
        "classroom_arrival": "14:55",
    },)
    assert validate_expression_facts("14:50到教学楼，留5分钟找教室。", facts)
    assert validate_expression_facts("14:55到教室后签到，15:00开始上课。", facts)
    assert not validate_expression_facts("14:50到教室后签到。", facts)


def _state(ends_at=None):
    return SimpleNamespace(commitments=(SimpleNamespace(
        location_text="31教", starts_at="15:00", ends_at=ends_at,
    ),))


def test_state_aware_question_filter_keeps_only_unknown_facts():
    blocks = (object(),)
    questions = (
        "请问您具体是要去哪里？", "你现在在哪里？", "你打算怎么过去？",
        "几点上课？", "上课大约几点结束？", "上课什么时候结束？", "需要带电脑吗？",
    )
    filtered = filter_pending_questions(questions, _state(), blocks)
    assert filtered == ("上课大约几点结束？", "需要带电脑吗？")
    assert filter_pending_questions(questions, _state("17:00"), blocks) == ("需要带电脑吗？",)
