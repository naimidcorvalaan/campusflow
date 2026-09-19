"""P3e Companion Copy Agent 测试（mock，不调用真实 TJU API）。

覆盖：
- 忙碌/轻松/仅下午计划的开场与收尾；
- 多次移动的事实进入文案但不修改真实路线；
- Qwen 文案不能修改结构化计划；
- 调用失败走安全 fallback 且主计划正常；
- 普通 rerun 不重复调用；计划真实变化才重新生成；
- opening/closing 不泄露内部字段。
"""

import json
from datetime import datetime

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_companion_copy import (
    CLOSING_FALLBACK,
    CONTEXT_FEEDBACK,
    CONTEXT_INITIAL,
    CONTEXT_REFRESH,
    FEEDBACK_CLOSING_FALLBACK,
    FEEDBACK_OPENING_FALLBACK,
    OPENING_FALLBACK,
    CompanionCopy,
    build_companion_prompt,
    extract_change_facts,
    extract_plan_facts,
)
from src.p2_main import (
    _COMMITMENT_DELAY,
    _COMMITMENT_NONE,
    _PLAN_EMPTY,
    _REVIEW_ACCEPT,
    _TASK_NONE,
    _TASK_PROGRESS_30,
    _TASK_SKIP,
    build_demo_state,
    render_page_streamlit,
    render_page_text,
)
from src.p2_models import TaskProgress, TaskState
from src.p2_session import P2SessionController, START_TEXT
from src.p2_window_derivation import derive_day_state
from src.p3_map_schema import TransportMode
from src.p3_route_planner import MovementBlock
from src.p3_time_estimator import TimeEstimateMethod


def _router_json(route):
    return '{"schema_version": "p2.feedback-router.v1", "route": "' + route + '", "reason": null}'


def _companion_json(opening, closing):
    payload = {
        "schema_version": "p3.companion-copy.v1",
        "opening": opening,
        "closing": closing,
    }
    return json.dumps(payload, ensure_ascii=False)


def _critic_json(approve=True):
    return '{"schema_version": "p3.plan-critic.v1", "approved": ' + str(bool(approve)).lower() + \
           ', "issues": [], "revision_needed": false}'


def _narrator_json(opening):
    return json.dumps({
        "schema_version": "p3.plan-narrator.v1",
        "opening": opening,
    }, ensure_ascii=False)


def _warm_json(change_summary, closing):
    return json.dumps({
        "schema_version": "p3.warm-companion.v1",
        "change_summary": change_summary,
        "closing": closing,
    }, ensure_ascii=False)


def _lifestyle_json(hint, gap_tip=None):
    return json.dumps({
        "schema_version": "p3.lifestyle-review.v1",
        "notes": [],
        "user_facing_hint": hint,
        "gap_tip": gap_tip,
    }, ensure_ascii=False)


def _copy_review_json():
    return '{"schema_version": "p3.copy-review.v1", "approved": true, "revised": null, "reason": null}'


class PipelineCaller:
    """mock：多 Agent 表达层按配置返回，其余管线阶段返回固定 JSON。"""

    def __init__(self, companion_opening=None, companion_closing=None, companion_fail=False,
                 route="unclear", commitment=None, task=None,
                 gap_tip=None, change_summary=None,
                 critic_approve=True, lifestyle_hint=None):
        self.companion_opening = (
            companion_opening if companion_opening is not None else "这是为你整理好的当前安排："
        )
        self.companion_closing = (
            companion_closing if companion_closing is not None else "按自己的节奏来，祝你今天顺利。"
        )
        self.companion_fail = companion_fail
        self.gap_tip = gap_tip
        self.change_summary = change_summary
        self.critic_approve = critic_approve
        self.lifestyle_hint = lifestyle_hint
        self.route = route
        self.commitment = commitment if commitment is not None else _COMMITMENT_NONE
        self.task = task if task is not None else _TASK_NONE
        self.calls = []
        self.companion_calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        if "p3.plan-critic" in system:
            self.companion_calls.append(("critic", system, user))
            if self.companion_fail:
                raise RuntimeError("companion unavailable")
            return _critic_json(self.critic_approve)
        if "p3.lifestyle-review" in system:
            self.companion_calls.append(("lifestyle", system, user))
            if self.companion_fail:
                raise RuntimeError("companion unavailable")
            return _lifestyle_json(self.lifestyle_hint, self.gap_tip)
        if "p3.plan-narrator" in system:
            self.companion_calls.append(("narrator", system, user))
            if self.companion_fail:
                raise RuntimeError("companion unavailable")
            return _narrator_json(self.companion_opening)
        if "p3.warm-companion" in system:
            self.companion_calls.append(("warm", system, user))
            if self.companion_fail:
                raise RuntimeError("companion unavailable")
            return _warm_json(self.change_summary, self.companion_closing)
        if "p3.copy-review" in system:
            self.companion_calls.append(("copy_review", system, user))
            if self.companion_fail:
                raise RuntimeError("companion unavailable")
            return _copy_review_json()
        if "feedback-router" in system:
            return _router_json(self.route)
        if "commitment-reconciliation" in system:
            return self.commitment
        if "task-reconciliation" in system:
            return self.task
        if "day-plan-intent" in system:
            return _PLAN_EMPTY
        if "day-review" in system:
            return _REVIEW_ACCEPT
        return _TASK_NONE

    def _agent_calls(self, name):
        return [entry[1:] for entry in self.companion_calls if entry[0] == name]

    @property
    def narrator_calls(self):
        return self._agent_calls("narrator")

    @property
    def warm_calls(self):
        return self._agent_calls("warm")


def make_task(ref="day_task_001", title="计组实验", total=120, completed=50,
              state=TaskState.ACTIVE, splittable=True, min_slice=30):
    return TaskProgress(
        ref, title, total, completed,
        SourceKind.AI_EXTRACTED_FROM_USER_TEXT, state, splittable, min_slice,
    )


def make_commitment(ref, title, start, end, availability=AvailabilityLevel.UNAVAILABLE):
    return FixedCommitment(ref, title, title, start, end, None, availability, {}, ())


def relaxed_state():
    now = datetime(2026, 9, 1, 10, 0)
    tasks = (make_task("day_task_001", "背单词", 30, 0),)
    return derive_day_state(now, datetime(2026, 9, 1, 18, 0), (), tasks, 10, {}, (), now)


def afternoon_state():
    now = datetime(2026, 9, 1, 14, 0)
    commitments = (make_commitment("c1", "上课", datetime(2026, 9, 1, 15, 0), datetime(2026, 9, 1, 16, 30)),)
    tasks = (make_task("day_task_001", "计组实验", 60, 0),)
    return derive_day_state(now, datetime(2026, 9, 1, 22, 0), commitments, tasks, 10, {}, (), now)


def make_block(window_ref, window_start, origin, destination, mode=TransportMode.BIKE,
               minutes=5):
    return MovementBlock(
        window_ref=window_ref,
        window_start=window_start,
        origin_text=origin,
        destination_text=destination,
        origin_node_id="node_" + origin,
        destination_node_id="node_" + destination,
        origin_name=origin,
        destination_name=destination,
        mode=mode,
        distance_m=800,
        estimated_minutes=minutes,
        low_minutes=max(1, minutes - 1),
        high_minutes=minutes + 1,
        method=TimeEstimateMethod.PROGRAM_FALLBACK,
        approximate=False,
        destination_short_name=destination,
    )


def _make_session(caller, state, movement_blocks=()):
    controller = P2SessionController(
        {}, caller, companion_caller=caller, companion_enabled=True
    )
    turn = controller.start_day(state, movement_blocks=movement_blocks)
    return controller, turn


def _plan_body_lines(text):
    lines = text.splitlines()
    assert lines[0] == "# 当前方案"
    # 去掉首行与 opening/closing 后即为计划行
    body = lines[1:]
    if body and body[0] == OPENING_FALLBACK:
        body = body[1:]
    if body and body[-1] == CLOSING_FALLBACK:
        body = body[:-1]
    return body


def test_busy_day_opening_and_closing_rendered():
    caller = PipelineCaller(
        companion_opening="同学你好，今天的安排比较充实，CampusFlow 已经帮你把移动和碎片时间安排好了。",
        companion_closing="今天的节奏比较紧凑，记得吃饭喝水，也给自己几分钟休息。",
    )
    _, turn = _make_session(caller, build_demo_state())
    assert turn.companion_copy is not None
    assert turn.companion_copy.generated is True
    text = render_page_text(turn)
    lines = text.splitlines()
    assert lines[0] == "# 当前方案"
    assert lines[1] == caller.companion_opening
    assert lines[-1] == caller.companion_closing
    # opening 在计划行之前，closing 在计划行之后
    assert lines.index(caller.companion_opening) == 1
    assert lines.index(caller.companion_closing) == len(lines) - 1
    # narrator prompt 收到真实计划事实
    system, user = caller.narrator_calls[-1]
    assert "当前时间" in user
    assert "计组实验" in user
    assert "上课" in user


def test_relaxed_plan_closing_not_busy_boilerplate():
    caller = PipelineCaller(
        companion_opening="今天的安排比较轻松。",
        companion_closing="今天节奏比较从容，按自己的节奏来就好。",
    )
    _, turn = _make_session(caller, relaxed_state())
    text = render_page_text(turn)
    assert "节奏比较从容" in text
    system, user = caller.warm_calls[-1]
    assert "机械说" in system
    assert "繁忙" in system


def test_afternoon_only_opening_uses_afternoon_not_full_day():
    caller = PipelineCaller(
        companion_opening="今天下午的安排已经整理好了。",
        companion_closing="下午加油，祝你顺利。",
    )
    _, turn = _make_session(caller, afternoon_state())
    text = render_page_text(turn)
    assert "今天下午" in text
    # 没有编造全天 8:00 之类的时间段
    assert "8:00" not in text
    system, user = caller.narrator_calls[-1]
    assert "当前时间：2026-09-01 14:00" in user


def test_multiple_movements_flow_into_copy_without_changing_routes():
    state = build_demo_state()
    blocks = (
        make_block("day_window_002", datetime(2026, 9, 1, 11, 40), "9斋", "31教"),
        make_block("day_window_003", datetime(2026, 9, 1, 16, 0), "31教", "图书馆"),
    )
    caller = PipelineCaller(
        companion_opening="今天有好几个地点要切换，CampusFlow 已经帮你预留了移动时间。",
        companion_closing="跨地点较多，记得给移动留点余量。",
    )
    controller, turn = _make_session(caller, state, movement_blocks=blocks)
    assert turn.movement_blocks
    # narrator prompt 收到真实移动事实
    system, user = caller.narrator_calls[-1]
    assert "31教" in user
    assert "图书馆" in user
    # 对比 companion 关闭时的纯计划行，移动/计划事实完全一致
    plain_controller = P2SessionController({}, caller)
    plain_turn = plain_controller.start_day(state, movement_blocks=blocks)
    plain_lines = render_page_text(plain_turn).splitlines()[1:]
    with_companion = render_page_text(turn).splitlines()
    body = with_companion[2:-1]  # 去掉标题/opening/closing
    assert body == list(plain_lines)


def test_companion_text_cannot_modify_structured_plan():
    caller = PipelineCaller(
        companion_opening="顺便说一句，去健身房2小时吧。",
        companion_closing="再见。",
    )
    _, turn = _make_session(caller, build_demo_state())
    text = render_page_text(turn)
    lines = text.splitlines()
    # 结构化计划行之间不出现 Qwen 伪造的任务
    body = lines[2:-1]
    assert not any("健身房" in line for line in body)
    assert "健身房" in lines[1]
    # 结构化计划本身不变
    assert turn.result.allocation_plan.total_planned_minutes > 0


def test_streamlit_plan_shows_final_summary_separately_from_formal_facts():
    """Streamlit 渲染：opening/closing 保持普通文本，实际计划区用 divider + 加粗时间突出。"""
    caller = PipelineCaller(
        companion_opening="同学你好呀～今天的安排已经帮你整理好啦！",
        companion_closing=("今天下午事情不少，不过已经帮你把收拾和通勤的时间留出来啦，"
                           "不用卡着最后一分钟赶。实验能推进多少就推进多少，"
                           "没做完的之后再接着来就好。路上注意安全，也别忘了喝点水～"
                           "祝你上课顺利！"),
    )
    _, turn = _make_session(caller, build_demo_state())

    class _MarkdownStub:
        def __init__(self):
            self.calls = []

        def markdown(self, text, **kwargs):
            self.calls.append(text)

    stub = _MarkdownStub()
    render_page_streamlit(stub, turn)
    calls = stub.calls
    page = "\n".join(calls)
    assert caller.companion_opening in page
    assert page.index('class="cf-plan-summary"') < page.index('<section class="cf-current-plan cf-plan-hero')
    assert turn.companion_copy.opening == caller.companion_opening
    assert caller.companion_closing not in page
    assert "cf-plan-hero" in page
    assert "cf-timeline-card" in page
    # Companion wording remains outside the formal task/movement facts.
    assert "cf-plan-opening" in page
    assert 'class="cf-plan-closing"' not in page


def test_companion_failure_uses_fallback_and_plan_ok():
    caller = PipelineCaller(companion_fail=True)
    _, turn = _make_session(caller, build_demo_state())
    assert isinstance(turn.companion_copy, CompanionCopy)
    assert turn.companion_copy.generated is False
    assert turn.companion_copy.opening == OPENING_FALLBACK
    assert turn.companion_copy.closing == CLOSING_FALLBACK
    text = render_page_text(turn)
    assert OPENING_FALLBACK in text
    assert CLOSING_FALLBACK in text
    assert turn.result.allocation_plan.total_planned_minutes > 0


def test_rerun_same_turn_no_extra_companion_calls():
    caller = PipelineCaller()
    controller, turn1 = _make_session(caller, build_demo_state())
    assert len(caller.companion_calls) == 5
    # 相同初始 state 再次 start_day：命中 turn 缓存，不重复调用
    turn2 = controller.start_day(build_demo_state())
    assert turn2 is turn1
    assert len(caller.companion_calls) == 5
    # 相同反馈文本再次提交：命中缓存
    t1 = controller.apply_feedback("我刚又做了30分钟实验")
    n_after_feedback = len(caller.companion_calls)
    assert n_after_feedback == 10
    t2 = controller.apply_feedback("我刚又做了30分钟实验")
    assert t2 is t1
    assert len(caller.companion_calls) == n_after_feedback


def test_plan_change_regenerates_companion():
    caller = PipelineCaller(route="commitment", commitment=_COMMITMENT_DELAY)
    controller, _ = _make_session(caller, build_demo_state())
    assert len(caller.companion_calls) == 5
    turn = controller.apply_feedback("下课晚了20分钟")
    assert len(caller.companion_calls) == 10
    assert turn.companion_copy is not None


def test_sanitizer_blocks_internal_fields():
    caller = PipelineCaller(
        companion_opening="task_ref day_task_001 schema_version",
        companion_closing="按自己的节奏来，祝你今天一切顺利。",
    )
    _, turn = _make_session(caller, build_demo_state())
    text = render_page_text(turn)
    # 内部字段被替换为固定 fallback，不泄露
    for marker in ("task_ref", "day_task_001", "schema_version"):
        assert marker not in text
    assert OPENING_FALLBACK in text
    assert "一切顺利" in text


def test_disabled_by_default_no_companion_rendering():
    caller = PipelineCaller()
    controller = P2SessionController({}, caller)
    turn = controller.start_day(build_demo_state())
    assert turn.companion_copy is None
    text = render_page_text(turn)
    assert "这是为你整理好的当前安排：" not in text
    assert len(caller.companion_calls) == 0


# ---------------------------------------------------------------------------
# P3e：反馈后更新文案（feedback_update）
# ---------------------------------------------------------------------------


class _UnifiedStub:
    """统一反馈 mock：result=None 时由会话自行跑 pipeline，仅提供 movement_blocks。"""

    def __init__(self, movement_blocks):
        self.movement_blocks = tuple(movement_blocks)
        self.result = None
        self.movement_plan = None
        self.commitment_questions = ()
        self.commitment_warnings = ()
        self.movement_questions = ()
        self.movement_warnings = ()

    def __call__(self, user_text, state, movement_data):
        return self


def _min_facts():
    return {
        "now": "2026-09-01 09:00",
        "day_end": "22:00",
        "tasks": ("09:10-10:00：计组实验 50分钟",),
        "commitments": ("上课 10:00-11:30",),
        "movements": (),
        "unallocated": (),
    }


def test_feedback_task_progress_opening_mentions_progress():
    caller = PipelineCaller(
        route="task",
        task=_TASK_PROGRESS_30,
        companion_opening="实验又有新进展啦，继续保持～",
        companion_closing="按新的节奏继续就好。",
    )
    controller, _ = _make_session(caller, build_demo_state())
    turn = controller.apply_feedback("我刚又做了30分钟实验")
    assert turn.companion_copy.context_type == CONTEXT_FEEDBACK
    system, user = caller.narrator_calls[-1]
    assert "文案场景：feedback_update" in system
    assert "进度增加30分钟" in user
    text = render_page_text(turn)
    assert "实验又有新进展啦" in text
    # 真实 progress 已应用，文案不改动它
    updated = {x.task_ref: x for x in turn.result.updated_state.tasks}["day_task_001"]
    assert updated.completed_minutes == 80


def test_feedback_skip_today_gentle_wording():
    caller = PipelineCaller(
        route="task",
        task=_TASK_SKIP,
        companion_opening="单词今天先放一放吧，之后有合适的时间再继续。",
        companion_closing="接下来按新的安排慢慢来～",
    )
    controller, _ = _make_session(caller, build_demo_state())
    turn = controller.apply_feedback("今天先不背单词了")
    system, user = caller.narrator_calls[-1]
    assert "今天先放一放" in user
    text = render_page_text(turn)
    assert "今天先放一放" in text
    assert "失败" not in text
    assert "没完成" not in text
    updated = {x.task_ref: x for x in turn.result.updated_state.tasks}["day_task_002"]
    assert updated.state == TaskState.SKIPPED_TODAY


def test_feedback_bike_to_walk_adjusted_no_late_fabrication():
    bike = make_block("day_window_002", datetime(2026, 9, 1, 11, 40), "9斋", "31教",
                      mode=TransportMode.BIKE, minutes=5)
    walk = make_block("day_window_002", datetime(2026, 9, 1, 11, 40), "9斋", "31教",
                      mode=TransportMode.WALK, minutes=12)
    caller = PipelineCaller(
        companion_opening="已经帮你把交通方式调整好啦，不用赶，安全第一～",
        companion_closing="接下来按新的安排慢慢来。",
    )
    store = {__import__("src.p2_session", fromlist=["MOVEMENT_DATA_KEY"]).MOVEMENT_DATA_KEY: {"active_blocks": (bike,)}}
    controller = P2SessionController(
        store, caller, companion_caller=caller, companion_enabled=True,
        unified_handler=_UnifiedStub((walk,)),
    )
    controller.start_day(build_demo_state(), movement_blocks=(bike,))
    turn = controller.apply_feedback("还是走过去吧")
    assert turn.companion_copy.context_type == CONTEXT_FEEDBACK
    system, user = caller.narrator_calls[-1]
    assert "文案场景：feedback_update" in system
    assert "从骑行改为步行" in user
    warm_system, _ = caller.warm_calls[-1]
    assert "快到了/快迟到" in warm_system  # 红线约束存在
    text = render_page_text(turn)
    assert "交通方式调整好" in text
    assert "快迟到" not in text
    assert "快到了" not in text


def test_feedback_commitment_time_change_reflected():
    caller = PipelineCaller(
        route="commitment",
        commitment=_COMMITMENT_DELAY,
        companion_opening="已经按新时间重新排好啦，别担心。",
        companion_closing="按新的节奏继续～",
    )
    controller, _ = _make_session(caller, build_demo_state())
    turn = controller.apply_feedback("下课晚了20分钟")
    assert turn.companion_copy.context_type == CONTEXT_FEEDBACK
    system, user = caller.narrator_calls[-1]
    assert "文案场景：feedback_update" in system
    assert "结束时间改为11:50" in user
    text = render_page_text(turn)
    assert "重新排好" in text


def test_combined_changes_not_all_mechanically_repeated():
    now = datetime(2026, 9, 1, 9, 0)
    before_tasks = (
        TaskProgress("day_task_001", "计组实验3", 120, 50,
                     SourceKind.AI_EXTRACTED_FROM_USER_TEXT, TaskState.ACTIVE, True, 30),
        TaskProgress("day_task_002", "背单词", 30, 0,
                     SourceKind.AI_EXTRACTED_FROM_USER_TEXT, TaskState.ACTIVE, False, None),
    )
    before_commitments = (
        make_commitment("day_commitment_001", "上课", datetime(2026, 9, 1, 10, 0), datetime(2026, 9, 1, 11, 30)),
        make_commitment("day_commitment_002", "实验", datetime(2026, 9, 1, 14, 0), datetime(2026, 9, 1, 15, 30)),
    )
    before = derive_day_state(now, datetime(2026, 9, 1, 22, 0), before_commitments, before_tasks, 10, {}, (), now)
    after_tasks = (
        TaskProgress("day_task_001", "计组实验3", 120, 80,
                     SourceKind.AI_EXTRACTED_FROM_USER_TEXT, TaskState.ACTIVE, True, 30),
        TaskProgress("day_task_002", "背单词", 30, 0,
                     SourceKind.AI_EXTRACTED_FROM_USER_TEXT, TaskState.SKIPPED_TODAY, False, None),
    )
    after_commitments = (
        make_commitment("day_commitment_001", "上课", datetime(2026, 9, 1, 10, 30), datetime(2026, 9, 1, 12, 0)),
        make_commitment("day_commitment_002", "实验", datetime(2026, 9, 1, 14, 0), datetime(2026, 9, 1, 15, 30)),
    )
    after = derive_day_state(now, datetime(2026, 9, 1, 22, 0), after_commitments, after_tasks, 10, {}, (), now)
    bike = make_block("day_window_002", datetime(2026, 9, 1, 11, 40), "9斋", "31教", mode=TransportMode.BIKE)
    walk = make_block("day_window_002", datetime(2026, 9, 1, 11, 40), "9斋", "31教", mode=TransportMode.WALK)
    change = extract_change_facts(
        before, after, before_blocks=(bike,), after_blocks=(walk,),
        before_location="9斋", after_location="9斋",
    )
    assert change["changed"] is True
    assert any("进度增加30分钟" in line for line in change["lines"])
    assert any("今天先放一放" in line for line in change["lines"])
    assert any("开始时间改为10:30" in line for line in change["lines"])
    assert any("从骑行改为步行" in line for line in change["lines"])
    system, user = build_companion_prompt(
        _min_facts(), context_type=CONTEXT_FEEDBACK, change_facts=change
    )
    assert "不需要把所有变化机械复述一遍" in system
    assert "本轮已确认变化" in user


def test_no_evening_window_blocks_night_claim():
    now = datetime(2026, 9, 1, 9, 0)
    commitments = (make_commitment("c1", "上课", datetime(2026, 9, 1, 10, 0), datetime(2026, 9, 1, 11, 30)),)
    before_tasks = (make_task("day_task_001", "计组实验", 120, 50),)
    before = derive_day_state(now, datetime(2026, 9, 1, 17, 0), commitments, before_tasks, 10, {}, (), now)
    after_tasks = (make_task("day_task_001", "计组实验", 120, 55),)
    after = derive_day_state(now, datetime(2026, 9, 1, 17, 0), commitments, after_tasks, 10, {}, (), now)
    change = extract_change_facts(before, after)
    assert change["evening_free"] is False
    assert "进度增加5分钟" in "|".join(change["lines"])
    system, user = build_companion_prompt(
        _min_facts(), context_type=CONTEXT_FEEDBACK, change_facts=change
    )
    assert "晚间可用时间：无" in user
    assert "只有计划中确实存在晚间可用时间" in system


def test_far_from_next_commitment_blocks_urgent_claim():
    now = datetime(2026, 9, 1, 9, 0)
    commitments = (make_commitment("c1", "上课", datetime(2026, 9, 1, 12, 0), datetime(2026, 9, 1, 13, 30)),)
    before_tasks = (make_task("day_task_001", "计组实验", 120, 50),)
    before = derive_day_state(now, datetime(2026, 9, 1, 22, 0), commitments, before_tasks, 10, {}, (), now)
    after_tasks = (make_task("day_task_001", "计组实验", 120, 55),)
    after = derive_day_state(now, datetime(2026, 9, 1, 22, 0), commitments, after_tasks, 10, {}, (), now)
    change = extract_change_facts(before, after)
    assert change["next_commitment"] == "12:00 上课"
    assert change["minutes_to_next_commitment"] == 180
    system, user = build_companion_prompt(
        _min_facts(), context_type=CONTEXT_FEEDBACK, change_facts=change
    )
    assert "只有当前时间距下一固定安排很近" in system
    assert "（约180分钟后）" in user
    # 红线文本存在，确保 Qwen 不会编造“快到了”
    assert "快到了/快迟到" in system


def test_feedback_without_real_change_uses_plain_overview():
    caller = PipelineCaller(
        companion_opening="这是整理好的当前安排。",
        companion_closing="按自己的节奏来。",
    )
    controller, _ = _make_session(caller, build_demo_state())
    before_calls = len(caller.companion_calls)
    turn = controller.apply_feedback("随便问问")
    assert len(caller.companion_calls) == before_calls + 5
    assert turn.companion_copy.context_type == CONTEXT_REFRESH
    system, user = caller.narrator_calls[-1]
    assert "文案场景：refresh" in system
    assert "本轮已确认变化" not in user
    assert "已为你更新计划" not in turn.companion_copy.opening


def test_feedback_companion_failure_uses_feedback_fallback():
    caller = PipelineCaller(route="task", task=_TASK_PROGRESS_30, companion_fail=True)
    controller, _ = _make_session(caller, build_demo_state())
    turn = controller.apply_feedback("我刚又做了30分钟实验")
    assert turn.companion_copy.generated is False
    assert turn.companion_copy.opening == FEEDBACK_OPENING_FALLBACK
    assert turn.companion_copy.closing == FEEDBACK_CLOSING_FALLBACK
    text = render_page_text(turn)
    assert FEEDBACK_OPENING_FALLBACK in text
    assert turn.result.allocation_plan.total_planned_minutes > 0


def test_feedback_sanitizer_blocks_internal_fields():
    caller = PipelineCaller(
        route="task",
        task=_TASK_PROGRESS_30,
        companion_opening="task_ref schema_version day_task_001",
        companion_closing="按新的安排慢慢来。",
    )
    controller, _ = _make_session(caller, build_demo_state())
    turn = controller.apply_feedback("我刚又做了30分钟实验")
    text = render_page_text(turn)
    for marker in ("task_ref", "schema_version", "day_task_001"):
        assert marker not in text
    assert FEEDBACK_OPENING_FALLBACK in text


def test_feedback_rerun_same_turn_no_extra_companion_calls():
    caller = PipelineCaller(
        route="task",
        task=_TASK_PROGRESS_30,
        companion_opening="实验有新进展啦。",
        companion_closing="继续加油。",
    )
    controller, _ = _make_session(caller, build_demo_state())
    turn1 = controller.apply_feedback("我刚又做了30分钟实验")
    n = len(caller.companion_calls)
    turn2 = controller.apply_feedback("我刚又做了30分钟实验")
    assert turn2 is turn1
    assert len(caller.companion_calls) == n
