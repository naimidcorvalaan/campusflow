"""Required information and progress/segment facts at presentation boundaries."""
from dataclasses import replace
from datetime import datetime
import json

import pytest

from src.p2_day_intake import DayIntakeProposal, IntakeCommitment, IntakeTask, apply_day_intake
from src.p5_agent_context import AgentPlanFact
from src.p5_copy_guard import GroundedNarrative, _deterministic_copy_guard, build_grounded_narrator_prompt, build_copy_check_prompt, generate_grounded_narrative
from tests.test_p5_agent_intelligence import _context, _summary


def _intake(questions, commitments=(), tasks=None, text="今天整理资料35分钟，17:00前完成。"):
    proposal = DayIntakeProposal("p2.day-intake.v1", tuple(commitments),
        tuple(tasks if tasks is not None else [IntakeTask("整理资料", 35)]), "18:00", tuple(questions))
    return apply_day_intake(datetime(2026, 9, 15, 14), proposal, user_text=text)


def test_sufficient_hard_information_does_not_require_optional_task_start_or_preferences():
    result = _intake(("整理资料的具体开始时间未定，需要确认。", "喜欢先休息吗？",
                      "有一处时间关系需要确认后才能准确安排。", "是否现在开始整理资料？",
                      "更喜欢哪项任务？", "优先做哪项任务？"))
    assert result.questions == ()
    assert result.state.tasks[0].total_minutes == 35


def test_known_fixed_times_are_not_requested_again():
    result = _intake(("组会几点开始、几点结束？",), [
        IntakeCommitment("组会", starts_at="16:00", ends_at="17:00"),
    ], text="16:00到17:00组会，整理资料35分钟。")
    assert not result.questions
    course = _intake(("上课几点开始？",), [
        IntakeCommitment("物理课", starts_at="16:00", ends_at="17:00", commitment_kind="class"),
    ], text="16:00到17:00上课。")
    assert not course.questions


def test_missing_fixed_end_and_ambiguous_identity_still_require_clarification():
    result = _intake(("喜欢先休息吗？",), [IntakeCommitment("组会", starts_at="16:00")])
    assert result.questions and all("组会" in question for question in result.questions)
    identity = _intake(("两份报告中，你指的是哪一份？",), text="先整理那份报告。")
    assert identity.questions == ("两份报告中，你指的是哪一份？",)


def test_omitted_fixed_event_with_unknown_start_is_not_silently_dropped():
    result = _intake(("上课几点开始？",), text="今天要上课，还要整理资料35分钟。")
    assert result.questions == ("上课几点开始？",)


def test_a_task_title_containing_a_fixed_event_word_does_not_turn_task_scheduling_into_missing_information():
    result = _intake(("阅读课程文献的具体开始时间未定。",),
        tasks=[IntakeTask("阅读课程文献",45)], text="今天阅读课程文献45分钟，17:00前完成。")
    assert not result.questions


def _copy_context(completed=0, segment_minutes=(30,30), finished=False):
    context = _context()
    task = replace(context.active_tasks[0], title="整理资料", total_minutes=60,
                   completed_minutes=completed, remaining_minutes=max(0,60-completed),
                   state="completed" if finished else "active")
    left = max(0,60-completed)
    segments = []
    timeline = []
    windows = []
    for index, minutes in enumerate(segment_minutes):
        left = max(0,left-minutes)
        ref = "w{}".format(index)
        start = "{:02d}:00".format(14+index*2)
        end = "{:02d}:{:02d}".format(14+index*2+minutes//60, minutes%60)
        windows.append((ref,"2026-09-15T"+start+":00","2026-09-15T"+end+":00",minutes))
        segments.append(AgentPlanFact(task.task_ref,"a{}".format(index),ref,minutes,left))
        timeline.append("{}–{}：整理资料 {} 分钟".format(start,end,minutes))
    context = replace(context, current_time="2026-09-15T14:00:00", active_tasks=(task,),
                      selected_plan=tuple(segments), selected_timeline=tuple(timeline),
                      available_windows=tuple(windows), fixed_commitments=(), movements=(),
                      ordering_constraints=(), preferred_next_task_ref=None)
    candidate = replace(_summary(), timeline=tuple(timeline), task_sequence=(task.task_ref,),
                        task_completion=((task.task_ref,sum(segment_minutes)),),
                        remaining_work=((task.task_ref,left),))
    return context, candidate


@pytest.mark.parametrize("opening,closing", [
    ("整理资料正在进行中，计划14:30结束。", "请继续整理资料。"),
    ("你已经开始整理资料。", "接着完成整理资料。"),
    ("现在先做整理资料。", "请继续练习。"),
    ("整理资料已经完成了。", "今天的任务都已完成。"),
    ("整理资料进行中。", "按计划安排。"),
    ("整理资料已经做了20分钟。", "按计划安排。"),
])
def test_planned_now_does_not_turn_zero_progress_into_started_or_completed(opening,closing):
    context,candidate = _copy_context()
    copy = GroundedNarrative(opening,None,closing)
    assert not _deterministic_copy_guard(context,candidate,copy).generated


@pytest.mark.parametrize("opening", [
    "先安排30分钟完成整理资料。", "现在把整理资料做完。", "整理资料将在14:30全部完成。",
    "先完成前30分钟，就能全部完成整理资料。",
])
def test_first_partial_segment_cannot_claim_whole_task_completion_even_when_later_segment_finishes_it(opening):
    context,candidate = _copy_context()
    assert candidate.remaining_work[0][1] == 0
    assert not _deterministic_copy_guard(context,candidate,GroundedNarrative(opening,None,"按计划安排。")).generated


@pytest.mark.parametrize("completed,minutes,opening,closing", [
    (0,(30,30),"现在先做30分钟整理资料。","完成前30分钟后，之后再安排其余部分。"),
    (20,(20,),"整理资料已部分完成，现在继续做20分钟。","还有一部分留待之后安排。"),
    (20,(40,),"整理资料正在进行中，接下来继续做40分钟。","按计划推进。"),
    (0,(60,),"现在安排60分钟完成整理资料。","计划15:00做完。"),
    (0,(30,30),"先做30分钟整理资料，之后再做30分钟。","计划16:30完成整理资料。"),
    (0,(30,30),"先做30分钟整理资料，之后再做30分钟。","两个片段合计60分钟，计划完成整理资料。"),
    (60,(),"整理资料已经全部完成。","这项任务已完成，无需再安排。"),
])
def test_supported_progress_and_segment_completion_wording_remains_legal(completed,minutes,opening,closing):
    context,candidate = _copy_context(completed,minutes,finished=completed==60)
    if "正在" in opening:
        context = replace(context,latest_feedback_text="整理资料正在进行中，已做20分钟。")
    copy=GroundedNarrative(opening,None,closing)
    result=_deterministic_copy_guard(context,candidate,copy)
    assert result.generated
    assert result.closing == closing


def test_partial_progress_and_current_plan_are_not_evidence_of_working_right_now():
    context,candidate = _copy_context(20,(40,))
    copy = GroundedNarrative("整理资料正在进行中。",None,"按计划安排。")
    assert not _deterministic_copy_guard(context,candidate,copy).generated
    current = replace(context,latest_feedback_text="整理资料正在进行中，已做20分钟。")
    assert _deterministic_copy_guard(current,candidate,copy).generated


def test_narrator_and_checker_receive_the_same_read_only_progress_and_segment_contract():
    context,candidate = _copy_context()
    narrative = GroundedNarrative("先做30分钟整理资料。",None,"之后再做其余部分。")
    _, narrator = build_grounded_narrator_prompt(context,candidate,"",None)
    _, checker = build_copy_check_prompt(context,candidate,narrative)
    for prompt in (narrator,checker):
        assert "segment_covers_remaining" in prompt
        assert "not_started" in prompt
        assert '"remaining_before": 60' in prompt


@pytest.mark.parametrize("checker_corrects", [False, True])
def test_final_boundary_rejects_bad_semantics_even_when_checker_approves_or_introduces_them(checker_corrects):
    context,candidate = _copy_context()
    good = dict(schema_version="p5.grounded-narrator.v1", opening="现在先做30分钟整理资料。",
                why_this_plan=None, closing="之后再做其余部分。", proactive_suggestion=None, risk_note=None)
    bad = dict(good, opening="整理资料正在进行中，30分钟就能全部完成整理资料。")
    def caller(system,user):
        if "Copy Fact Checker" in system:
            return json.dumps(dict(schema_version="p5.copy-fact-check.v1", safe=not checker_corrects,
                                   reason=None, corrected_copy=bad if checker_corrects else None))
        return json.dumps(good if checker_corrects else bad)
    result,trace = generate_grounded_narrative(context,candidate,"",caller)
    assert not result.generated and trace.call_count == 2


def test_completed_task_progress_is_available_even_when_it_is_not_an_active_planning_task():
    from src.p2_models import TaskProgress, TaskState
    from src.p1_models import SourceKind
    context,candidate = _copy_context()
    finished = TaskProgress("finished_ref", "已交实验报告", 45, 45,
                            SourceKind.USER_STATED, TaskState.COMPLETED, True, 10)
    copy = GroundedNarrative("已交实验报告已经完成。", None, "接下来先做整理资料。")
    result = _deterministic_copy_guard(context,candidate,copy,actual_tasks=(finished,))
    assert result.generated and result.opening == copy.opening


@pytest.mark.parametrize("progress,prefix", [(0,"再做"),(20,"继续")])
def test_later_rendered_slice_uses_real_progress_instead_of_planned_progress(progress,prefix):
    from src.p2_allocator import allocate_tasks_across_windows
    from src.p2_day_plan import compact_plan_lines
    applied = _intake((), [IntakeCommitment("组会", starts_at="15:00", ends_at="16:00")],
                      tasks=[IntakeTask("整理资料",100,is_splittable=True,minimum_slice_minutes=10)])
    state = replace(applied.state,tasks=(replace(applied.state.tasks[0],completed_minutes=progress),))
    plan=allocate_tasks_across_windows(state)
    assert prefix+"整理资料" in "\n".join(compact_plan_lines(plan,state))
    assert state.tasks[0].completed_minutes == progress
