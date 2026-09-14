"""What-if failures reproduced through actual Streamlit submit/button handlers."""
import json

import pytest

from src.p2_live_main import LIVE_CAMPUS_SELECT_KEY, LIVE_SESSION_KEY
from src.p2_session import load_live_final_turn
from src.p4_feedback_decision import load_feedback_decision
from src.p5_what_if import load_what_if_preview
from tests.test_p2_live_main import _StubSt, _run_main, dt
from tests.test_p5_live_integration import _P5LiveCaller, _feedback_payload


REQUEST = "如果改成先背单词，再写作业，会怎么安排？先给我看看，暂时不要改。"


class Caller(_P5LiveCaller):
    def __init__(self, response="valid"):
        super().__init__()
        payload = json.loads(self.intake)
        payload["tasks"] = [payload["tasks"][2], payload["tasks"][3], payload["tasks"][1]]
        payload["tasks"][0]["total_minutes"] = 90
        payload["tasks"][1]["total_minutes"] = 60
        if response == "infeasible":
            payload["tasks"][1].update(total_minutes=500, is_splittable=False)
        payload["tasks"][2]["title"] = "吃晚饭"
        payload["tasks"][2]["meal_period"] = "dinner"
        payload["commitments"][0].update(starts_at="19:00", ends_at="20:30")
        self.intake = json.dumps(payload, ensure_ascii=False)
        plan = json.loads(self.plan)
        plan["task_order"] = ["day_task_001", "day_task_002", "day_task_003"]
        self.plan = json.dumps(plan)
        self.response = response

    def __call__(self, system, user):
        if "Feedback Interpreter" in system:
            self.calls.append((system, user))
            if REQUEST in user:
                return _feedback_payload("what_if", ("day_task_002", "day_task_001"))
            value = json.loads(_feedback_payload("reorder", ("day_task_001", "day_task_002")))
            value["ordering_constraints"] = [
                {"before_task_ref": "day_task_001", "after_task_ref": "day_task_002"},
                {"before_task_ref": "day_task_001", "after_task_ref": "day_task_003"},
            ]
            return json.dumps(value)
        if "What-if Interpreter" in system or "p5.what-if-intent.v1" in system:
            self.calls.append((system, user))
            if self.response == "malformed":
                return '{"schema_version":"p5.what-if-intent.v1","changes":[]}'
            value = {
                "schema_version": "p5.what-if-intent.v1",
                "changes": [{"kind": "ordering", "task_ref": "day_task_002", "related_task_ref": "day_task_001"}],
                "clarification_needed": False, "clarification_question": None,
            }
            if self.response == "clarify":
                value.update(changes=[], clarification_needed=True, clarification_question="你指的是哪一项作业？")
            if self.response == "conflict":
                value["changes"].append({"kind": "ordering", "task_ref": "day_task_001", "related_task_ref": "day_task_002"})
            if self.response == "same":
                value["changes"] = [{"kind": "ordering", "task_ref": "day_task_001", "related_task_ref": "day_task_002"}]
            return json.dumps(value, ensure_ascii=False)
        if "Plan Strategist" in system:
            value = json.loads(super().__call__(system, user))
            for strategy in value["strategies"]:
                strategy["priority_order"] = ["day_task_001", "day_task_002", "day_task_003"]
            return json.dumps(value)
        return super().__call__(system, user)


class BadTimingCaller(Caller):
    """Model copy is parseable but contradicts the final deterministic facts."""

    def __call__(self, system, user):
        if "Grounded Narrator" in system:
            self.calls.append((system, user))
            return json.dumps({
                "schema_version": "p5.grounded-narrator.v1",
                "opening": "安排已经整理好了。",
                "why_this_plan": (
                    "写作业和背单词连续安排；请在17:00前吃完晚饭，"
                    "并在18:50前到达教室。"
                ),
                "closing": "照着计划来就好。",
                "proactive_suggestion": None, "risk_note": None,
            }, ensure_ascii=False)
        return super().__call__(system, user)


class Page(_StubSt):
    clicked = None

    def button(self, label, key=None, **kwargs):
        self.buttons.append((key, kwargs.get("disabled", False)))
        return key == self.clicked and not kwargs.get("disabled", False)

    def render(self, caller, feedback=None, clicked=None):
        self.clicked = clicked
        self.buttons = []
        self.markdown_calls = []
        self.set_inputs(reference_hour=14, reference_minute=0,
                        feedback=feedback or "", feedback_submitted=feedback is not None)
        _run_main(self, caller, now=dt(14))


def start(response="valid", caller=None, apply_baseline_feedback=True):
    caller = caller or Caller(response)
    page = Page()
    page.buttons = []
    page.session_state[LIVE_CAMPUS_SELECT_KEY] = "卫津路校区"
    page.set_inputs(reference_hour=14, reference_minute=0, intake_submitted=True,
                    intake="先写90分钟作业，再背60分钟单词，然后吃晚饭，19:00去9教上课，20:30下课")
    _run_main(page, caller, now=dt(14))
    assert not page.errors
    if apply_baseline_feedback:
        page.render(caller, feedback="先写作业再背单词，作业也要在晚饭之前")
        assert not page.errors
    return caller, page


def sequence(bundle):
    return list(dict.fromkeys(item.task_ref for item in bundle.result.allocation_plan.allocations))


def test_reverse_preview_then_apply_uses_exact_candidate_once():
    caller, page = start()
    baseline = load_live_final_turn(page.session_state)
    previous_decision = load_feedback_decision(page.session_state)
    assert sequence(baseline)[:2] == ["day_task_001", "day_task_002"]
    page.render(caller, feedback=REQUEST)
    preview = load_what_if_preview(page.session_state)
    assert preview.status == "ready"
    assert load_live_final_turn(page.session_state) is baseline
    assert load_feedback_decision(page.session_state) is previous_decision
    assert page.session_state[LIVE_SESSION_KEY].current_state() is baseline.state
    assert preview.candidate.state.tasks == baseline.state.tasks
    assert sequence(preview.candidate)[:2] == ["day_task_002", "day_task_001"]
    pairs = {(x.before_task_ref, x.after_task_ref) for x in preview.candidate.feedback_decision.ordering_constraints}
    assert ("day_task_002", "day_task_001") in pairs
    assert ("day_task_001", "day_task_002") not in pairs
    assert ("day_task_001", "day_task_003") in pairs
    calls = caller.count
    page.render(caller)
    text = "\n".join(page.markdown_calls)
    assert "背单词" in text and "写作业" in text
    preview_text = next(row for row in page.markdown_calls if '<section class="cf-whatif-card"' in row)
    assert "背单词" in preview_text and "写作业" in preview_text and "19:00" in preview_text
    assert caller.count == calls
    page.render(caller, clicked="p5_apply_what_if")
    adopted = load_live_final_turn(page.session_state)
    assert not page.errors
    assert adopted.version == baseline.version + 1
    assert adopted.result is preview.candidate.result
    assert adopted.execution_context == preview.candidate.execution_context
    assert adopted.state.commitments == baseline.state.commitments
    assert adopted.result.allocation_plan.planned_minutes_by_task["day_task_001"] == 90
    assert adopted.result.allocation_plan.planned_minutes_by_task["day_task_002"] == 60
    assert load_what_if_preview(page.session_state) is None
    assert caller.count == calls
    # Adoption publishes the exact preview candidate without a new model or
    # planner pass, but its header must explain the verified change.  The
    # structured P4 assumption remains the one and only location statement.
    page.render(caller)
    rendered = "\n".join(page.markdown_calls)
    assert "已按你的调整，把背单词排在写作业之前。" in rendered
    assert "优先守住固定安排和移动时间，再利用可行窗口推进任务。" not in rendered
    assert "17:00前吃完晚饭" not in rendered
    assert "18:50前到达教室" not in rendered
    assert "连续安排150分钟" in adopted.agent_intelligence.narrative.why_this_plan
    assert "连续安排150分钟" not in rendered
    assert "17:00–17:40" in rendered
    assert "17:40–18:41" in rendered
    if adopted.execution_context.current_location.source.value == "assumed":
        assert rendered.count("你没有填写当前位置") == 1
        assert rendered.count("你现在是在") == 1
    assert caller.count == calls
    page.render(caller, clicked="p5_apply_what_if")
    assert load_live_final_turn(page.session_state) is adopted
    with pytest.raises(ValueError):
        page.session_state[LIVE_SESSION_KEY].apply_what_if_preview_atomic()
    assert load_live_final_turn(page.session_state) is adopted


def test_initial_expression_rejects_wrong_meal_and_classroom_deadlines_from_model():
    caller, page = start(
        caller=BadTimingCaller(), apply_baseline_feedback=False
    )
    bundle = load_live_final_turn(page.session_state)
    assert bundle.agent_intelligence is not None
    page.render(caller)
    rendered = "\n".join(page.markdown_calls)
    assert "17:00前吃完晚饭" not in rendered
    assert "18:50前到达教室" not in rendered
    narrative = bundle.agent_intelligence.narrative
    assert "连续安排150分钟" in narrative.why_this_plan
    validated = " ".join(x for x in (narrative.opening, narrative.why_this_plan, narrative.closing) if x)
    assert "17:00前吃完晚饭" not in validated
    assert "18:50前到达教室" not in validated
    assert "连续安排150分钟" not in rendered
    assert "17:00–17:40" in rendered
    assert "17:40–18:41" in rendered


def test_keep_discards_preview_without_mutation():
    caller, page = start()
    baseline = load_live_final_turn(page.session_state)
    page.render(caller, feedback=REQUEST)
    assert load_what_if_preview(page.session_state).status == "ready"
    page.render(caller, clicked="p5_keep_current_plan")
    assert load_what_if_preview(page.session_state) is None
    assert load_live_final_turn(page.session_state) is baseline


@pytest.mark.parametrize("response,status", [
    ("malformed", "failed"), ("clarify", "needs_clarification"),
    ("conflict", "failed"), ("infeasible", "infeasible"),
])
def test_nonready_preview_has_no_apply_and_backend_rejects(response, status):
    caller, page = start(response)
    baseline = load_live_final_turn(page.session_state)
    page.render(caller, feedback=REQUEST)
    preview = load_what_if_preview(page.session_state)
    assert preview.status == status
    page.render(caller)
    assert not any(key == "p5_apply_what_if" and not disabled for key, disabled in page.buttons)
    text = "\n".join(page.markdown_calls)
    if response == "clarify":
        assert text.count("你指的是哪一项作业？") == 1
    with pytest.raises(ValueError):
        page.session_state[LIVE_SESSION_KEY].apply_what_if_preview_atomic()
    assert load_live_final_turn(page.session_state) is baseline
    assert not page.errors


def test_stale_preview_rejected_by_actual_apply_button():
    caller, page = start()
    page.render(caller, feedback=REQUEST)
    assert load_what_if_preview(page.session_state).status == "ready"
    page.render(caller, clicked="p2_live_refresh")
    reliable = load_live_final_turn(page.session_state)
    calls = caller.count
    page.render(caller, clicked="p5_apply_what_if")
    assert load_live_final_turn(page.session_state) is reliable
    assert caller.count == calls
    assert not page.errors
    assert any("已过期" in str(row) for row in page.warnings)
    assert any("当前方案" in row for row in page.markdown_calls)


def test_preview_reports_no_change_without_inventing_time_savings():
    caller, page = start("same")
    page.render(caller, feedback=REQUEST)
    preview = load_what_if_preview(page.session_state)
    assert preview.status == "ready"
    text = "\n".join(preview.key_differences)
    assert "顺序没有变化" in text
    assert "节省" not in text
