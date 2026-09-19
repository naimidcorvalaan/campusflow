"""P2d 页面入口纯函数与 mock 演示测试（不启动真实 Streamlit）。

覆盖：
- build_demo_state / DemoMockCaller 各分支；
- render_page_text 不含内部 ref、无 active window 时不伪造“现在”；
- main(stub) 三个动作（开始 / 应用反馈 / 刷新）触发规划，普通 rerun 不调用 Agent。
"""

from dataclasses import replace
from datetime import datetime

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_agentic_prompt_builder import build_reconciliation_prompt
from src.p2_companion_copy import CompanionCopy
from src.p2_commitment_reconciler import build_commitment_reconciliation_prompt
from src.p2_main import (
    DemoMockCaller,
    build_current_plan_display,
    build_demo_state,
    main,
    render_page_streamlit,
    render_page_text,
    sanitize_user_facing_text,
    PlanDisplayEntry,
    CurrentPlanDisplay,
    _hero_html,
    _provisional_reason,
    _with_visible_slack,
)
from src.p2_models import TaskProgress, TaskState
from src.p2_session import P2SessionController
from src.p2_window_derivation import derive_day_state


def dt(h, m=0):
    return datetime(2026, 9, 1, h, m)


def make_commitment(ref, title, start, end, availability=AvailabilityLevel.UNAVAILABLE):
    return FixedCommitment(ref, title, title, start, end, None, availability, {}, ())


def make_task(
    ref="day_task_001",
    title="计组实验3",
    total=120,
    completed=50,
    state=TaskState.ACTIVE,
    splittable=True,
    min_slice=30,
):
    return TaskProgress(
        ref, title, total, completed,
        SourceKind.AI_EXTRACTED_FROM_USER_TEXT, state, splittable, min_slice,
    )


# ---------------------------------------------------------------------------
# 纯函数 / demo mock
# ---------------------------------------------------------------------------


def test_build_demo_state():
    state = build_demo_state()
    assert len(state.commitments) == 2
    assert len(state.tasks) == 2
    assert state.active_window_ref is not None
    task1 = {t.task_ref: t for t in state.tasks}["day_task_001"]
    assert task1.completed_minutes == 50
    assert task1.remaining_minutes == 70


def test_demo_caller_commitment_dispatch():
    state = build_demo_state()
    caller = DemoMockCaller()
    assert caller.call_count == 0
    system, user = build_commitment_reconciliation_prompt(state, "下课晚了20分钟。")
    out = caller(system, user)
    assert '"action": "delay"' in out and "day_commitment_001" in out
    system, user = build_commitment_reconciliation_prompt(state, "14点临时有个组会，大概1小时。")
    assert '"action": "add"' in caller(system, user)
    system, user = build_commitment_reconciliation_prompt(state, "下午的组会取消了。")
    assert '"action": "cancel"' in caller(system, user)
    system, user = build_commitment_reconciliation_prompt(state, "今天先不背单词了。")
    assert '"updates": []' in caller(system, user)


def test_demo_caller_task_dispatch():
    state = build_demo_state()
    caller = DemoMockCaller()
    system, user = build_reconciliation_prompt(state, "我刚又做了30分钟实验")
    assert '"progress_delta_minutes": 30' in caller(system, user)
    system, user = build_reconciliation_prompt(state, "今天先不背单词了。")
    assert '"lifecycle_action": "skip_today"' in caller(system, user)


def test_sanitize_user_facing_text_replaces_whole_text_on_internal_refs():
    """命中内部 ref 时整条替换为安全人话，不保留任何占位符。"""
    text = "day_task_001 与 day_commitment_003 与 day_window_002 与 allocation_001"
    out = sanitize_user_facing_text(text)
    assert out == "这个反馈暂时无法确定对应哪个任务，请再说明一下。"
    assert "[内部引用]" not in out
    assert "[内部状态]" not in out
    for marker in ("day_task_001", "day_commitment_003", "day_window_002", "allocation_001"):
        assert marker not in out
    assert sanitize_user_facing_text(None) == ""


def test_final_timeline_gap_after_current_activity_is_rendered_as_secondary_slack():
    entries = _with_visible_slack((
        PlanDisplayEntry("现在", "写实验报告，做到13:46", True, True, "task"),
        PlanDisplayEntry("15:58–16:06", "步行去食堂", True, False, "movement"),
    ))
    slack = entries[1]
    assert slack.time_text == "13:46–15:58"
    assert slack.body == "修整一下"
    assert slack.is_primary is False
    assert slack.kind == "helper"


def test_unknown_workload_without_current_action_is_presented_as_provisional():
    reason = "整理今天的笔记的任务时间不确定；可以先估时。"
    display = CurrentPlanDisplay(
        opening="我按最终确认的时间线把当前方案整理好了。",
        intro_notes=(reason,), entries=(), closing="",
        side_notes=(),
    )
    assert _provisional_reason(display, ()) == reason
    assert _hero_html(display, provisional=True, question=reason).count(reason) == 1


# ---------------------------------------------------------------------------
# render_page_text
# ---------------------------------------------------------------------------




def test_sanitize_user_facing_text_replaces_whole_text_on_internal_state_terms():
    """命中内部状态术语时整条替换，前端绝不能出现占位符。"""
    text = ("task_ref=day_task_001 状态=SKIPPED_TODAY，恢复后为 ACTIVE，"
            "lifecycle_action=resume_today；day_task_xyz 占位")
    out = sanitize_user_facing_text(text)
    assert out == "这个反馈暂时无法确定对应哪个任务，请再说明一下。"
    assert "[内部引用]" not in out
    assert "[内部状态]" not in out
    for marker in ("task_ref", "day_task_001", "SKIPPED_TODAY", "ACTIVE",
                   "resume_today", "day_task_xyz"):
        assert marker not in out


def test_render_page_text_sanitizes_question_internal_terms():
    """“待确认问题”绝不能泄露 task_ref / enum / 内部引用：整条替换为安全人话。"""
    controller = P2SessionController({}, DemoMockCaller())
    controller.start_day(build_demo_state())
    text = render_page_text(
        controller.last_turn(),
        extra_questions=("你说的任务 task_ref=day_task_001 状态=SKIPPED_TODAY 是否恢复 ACTIVE？",),
    )
    assert "## 待确认问题" in text
    assert "这个反馈暂时无法确定对应哪个任务，请再说明一下。" in text
    assert "你说的任务" not in text
    for marker in ("task_ref", "day_task_001", "SKIPPED_TODAY", "ACTIVE"):
        assert marker not in text


def test_sanitize_user_facing_text_keeps_clean_text_unchanged():
    """干净的人话原样返回；空 / 非字符串返回空。"""
    clean = "你是想把“背单词”重新加入今天的计划吗？"
    assert sanitize_user_facing_text(clean) == clean
    assert sanitize_user_facing_text("  ") == ""
    assert sanitize_user_facing_text(None) == ""
    assert sanitize_user_facing_text("") == ""


def test_sanitize_user_facing_text_blocks_internal_terms():
    """出现内部 ref / enum / schema 字段 / JSON / traceback / repr 时整条替换为安全提示。"""
    cases = (
        "这个任务 task_ref=day_task_001 已完成",
        "状态=SKIPPED_TODAY 是否要恢复为 ACTIVE？",
        "day_commitment_003 已取消，window_ref=day_window_002 释放",
        "schema_version=p2.task-reconciliation.v1 无法解析",
        "模型返回的 JSON 无法解析",
        "Traceback (most recent call last)",
        "<TaskProgress object at 0x7f8a2b3c4d5e>",
        "TaskProgress(task_ref='day_task_001', title='背单词')",
        "lifecycle_action=resume_today 已应用",
        "reference_datetime=2026-09-01 09:00 与 day_end 冲突",
    )
    for text in cases:
        out = sanitize_user_facing_text(text)
        assert out == "这个反馈暂时无法确定对应哪个任务，请再说明一下。", text


def test_render_page_streamlit_never_leaks_internal_params():
    """即使 mock Agent 故意返回含 task_ref / enum 的 question，最终页面也不泄露内部参数。"""
    controller = P2SessionController({}, DemoMockCaller())
    controller.start_day(build_demo_state())

    class _MarkdownStub:
        def __init__(self):
            self.calls = []

        def markdown(self, text, **kwargs):
            self.calls.append(text)

    stub = _MarkdownStub()
    render_page_streamlit(
        stub,
        controller.last_turn(),
        extra_questions=(
            "需要确认 task_ref=day_task_001 是否从 SKIPPED_TODAY 恢复为 ACTIVE？",
            "window_ref=day_window_002 的 schema_version 已过期",
        ),
    )
    joined = "\n".join(stub.calls)
    assert "这个反馈暂时无法确定对应哪个任务，请再说明一下。" in joined
    for marker in (
        "task_ref", "day_task_001", "SKIPPED_TODAY", "ACTIVE",
        "window_ref", "day_window_002", "schema_version",
    ):
        assert marker not in joined
    assert "[内部引用]" not in joined
    assert "[内部状态]" not in joined

def test_render_no_internal_refs():
    controller = P2SessionController({}, DemoMockCaller())
    controller.start_day(build_demo_state())
    text = render_page_text(controller.last_turn())
    assert "# 当前方案" in text
    assert "现在：计组实验3，做到09:50" in text
    assert "准备去上课" not in text
    assert "## 今天接下来" not in text
    assert "## AI 暂估" not in text
    assert "## 提示" not in text
    for marker in ("day_task_", "day_window_", "day_commitment_", "allocation_"):
        assert marker not in text
    assert "TaskProgress(" not in text
    assert "object at" not in text


def test_render_no_active_window_does_not_fake_now():
    commitments = (
        make_commitment("day_commitment_001", "上课", dt(10), dt(11, 30)),
        make_commitment("day_commitment_002", "实验", dt(14), dt(15, 30)),
    )
    state = derive_day_state(dt(10, 30), dt(22), commitments, (make_task(),), 10, {}, (), dt(10, 30))
    assert state.active_window_ref is None
    controller = P2SessionController({}, DemoMockCaller())
    controller.start_day(state)
    text = render_page_text(controller.last_turn())
    # 当前处于固定安排：显示“正在上课；下一步…”，不把未来任务冒充“现在”
    assert "现在：正在上课；下一步11:30开始计组实验3" in text
    assert "现在：计组实验3，" not in text
    assert "当前没有进行中的可用窗口。" not in text
    assert "后：" not in text


# ---------------------------------------------------------------------------
# main(stub)：普通 rerun 不调用 Agent，三个动作才触发
# ---------------------------------------------------------------------------


class _Form:
    def __init__(self, text, submitted):
        self._text = text
        self._submitted = submitted

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def text_input(self, label, key=None):
        return self._text

    def form_submit_button(self, label):
        return self._submitted


class _StubSt:
    def __init__(self):
        self.session_state = {}
        self.feedback = ""
        self.submitted = False
        self.start = False
        self.refresh = False
        self.rerun_called = 0
        self.writes = []
        self.markdown_calls = []

    def set_inputs(self, feedback="", submitted=False, start=False, refresh=False):
        self.feedback = feedback
        self.submitted = submitted
        self.start = start
        self.refresh = refresh
        return self

    def set_page_config(self, **kwargs):
        return None

    def title(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def form(self, *args, **kwargs):
        return _Form(self.feedback, self.submitted)

    def text_input(self, label, key=None):
        return self.feedback

    def form_submit_button(self, label):
        return self.submitted

    def button(self, label, key=None):
        if key == "p2_start":
            return self.start
        if key == "p2_refresh":
            return self.refresh
        return False

    def write(self, *args, **kwargs):
        self.writes.append(args)

    def markdown(self, text, **kwargs):
        self.markdown_calls.append(text)

    def rerun(self):
        self.rerun_called += 1

    def experimental_rerun(self):
        self.rerun_called += 1


def test_main_start_button_then_rerun_no_agent_calls():
    stub = _StubSt().set_inputs(start=True)
    main(stub)
    assert stub.rerun_called == 1
    session = stub.session_state["p2_session"]
    assert session.current_state() is not None
    caller = session.caller
    assert caller.call_count == 2  # Day Plan + Review（首次规划不送 reconciliation）
    # 普通 rerun：无按钮、无提交，只渲染，不调用 Agent
    stub.set_inputs()
    main(stub)
    assert caller.call_count == 2
    assert stub.markdown_calls
    assert "cf-plan-hero" in "\n".join(stub.markdown_calls)


def test_main_feedback_submit_replans():
    stub = _StubSt().set_inputs(start=True)
    main(stub)
    session = stub.session_state["p2_session"]
    before = session.caller.call_count
    stub.set_inputs(feedback="我刚又做了30分钟实验", submitted=True)
    main(stub)
    assert stub.rerun_called == 2
    assert session.caller.call_count == before + 4
    state = session.current_state()
    task1 = {t.task_ref: t for t in state.tasks}["day_task_001"]
    assert task1.completed_minutes == 80
    assert task1.remaining_minutes == 40


def test_main_refresh_button_replans():
    stub = _StubSt().set_inputs(start=True)
    main(stub)
    session = stub.session_state["p2_session"]
    before = session.caller.call_count
    stub.set_inputs(refresh=True)
    main(stub)
    assert session.caller.call_count > before
    assert stub.rerun_called == 2


def test_render_page_streamlit_renders_card_timeline_from_final_plan_lines():
    """Card timeline remains a display projection of the final plan lines."""
    controller = P2SessionController({}, DemoMockCaller())
    controller.start_day(build_demo_state())

    class _MarkdownStub:
        def __init__(self):
            self.calls = []

        def markdown(self, text, **kwargs):
            self.calls.append(text)

    stub = _MarkdownStub()
    render_page_streamlit(stub, controller.last_turn())
    page = "\n".join(stub.calls)
    assert "cf-plan-hero" in page
    assert "cf-timeline-card" in page
    assert "cf-focus-action" in page and "计组实验3" in page and "做到09:50" in page
    assert "11:30–11:50" in page and "计组实验3 20 分钟" in page
    assert "11:50–12:20" in page and "背单词 30 分钟" in page
    assert "准备去" not in page
    assert '<section class="cf-confirm-card">' not in page
    assert 'grid-template-columns: minmax(0,3.15fr) minmax(230px,.95fr)' in page
    assert '下一固定安排' in page


def test_render_page_streamlit_uses_one_responsive_grid_for_timeline_and_tip():
    """The real render path keeps the only side card in the timeline grid."""
    controller = P2SessionController({}, DemoMockCaller())
    controller.start_day(build_demo_state())
    turn = controller.last_turn()
    companion = CompanionCopy(
        opening="下面的计划已经排好。",
        closing="按这个节奏继续就好。",
        lifestyle_hint="学习任务连续安排，固定活动前仍有一段余量。",
    )
    turn = replace(turn, companion_copy=companion)

    class _MarkdownStub:
        def __init__(self):
            self.calls = []

        def markdown(self, text, **kwargs):
            self.calls.append(text)

    stub = _MarkdownStub()
    render_page_streamlit(stub, turn)
    page = "\n".join(stub.calls)
    assert page.count('class="cf-action-workspace"') == 1
    assert page.count('class="cf-side-card"') == 1
    assert "安排提示" not in page
    assert companion.lifestyle_hint not in page
    assert turn.companion_copy is companion
    assert "温馨提示" not in page
    assert 'grid-template-columns: minmax(0,1fr)' in page


def test_display_projection_marks_helper_steps_without_timeline_nodes():
    """Helper classification is visual-only and current helper remains primary."""
    controller = P2SessionController({}, DemoMockCaller())
    controller.start_day(build_demo_state())
    turn = controller.last_turn()
    display = build_current_plan_display(turn)

    # Synthetic helper text exercises the display adapter only; it does not
    # modify allocator or planning facts.
    from src.p2_main import _display_entry_for_line, _timeline_entry_html
    helpers = (
        _display_entry_for_line(turn, "10:00–10:05：收拾东西"),
        _display_entry_for_line(turn, "10:05–10:10：进楼 / 找教室"),
        _display_entry_for_line(turn, "10:10–10:15：到教室后签到 / 课前准备"),
    )
    assert display.entries
    assert all(not item.is_primary and item.kind == "helper" for item in helpers)
    assert helpers[1].body == "进楼找教室"
    assert helpers[2].body == "课前准备"
    assert "cf-node" not in _timeline_entry_html(helpers[0])
    current = _display_entry_for_line(turn, "现在：收拾东西")
    assert current.is_primary and current.is_current
    assert "cf-node" in _timeline_entry_html(current)
