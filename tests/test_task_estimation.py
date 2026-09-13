import json
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pytest

from src.p1_models import SourceKind
from src.p2_session import P2SessionController, load_live_final_turn
from src.p2_live_main import _run_estimator_action
from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
from src.p2_tju_live_adapter import TJUP2CallAdapter
from src.task_estimation import (
    TaskEstimationError,
    TaskEstimationImageUnsupported,
    TaskEstimationStale,
    confirm_estimated_task,
    make_material,
    run_task_estimation,
)


def estimate_json(
    recommended=90,
    supplement_basis=None,
    understood=True,
    clarification=False,
    deadline_time=None,
):
    payload = {
        "schema_version": "campusflow.task-estimate.v1",
        "understood": understood,
        "task_name": "高数作业" if understood else None,
        "scope_summary": "完成第三章习题并订正错题" if understood else None,
        "completion_criteria": "全部作答并完成订正" if understood else None,
        "min_focus_minutes": recommended - 20 if understood and not clarification else None,
        "max_focus_minutes": recommended + 30 if understood and not clarification else None,
        "recommended_minutes": recommended if understood and not clarification else None,
        "basis": "结合题目范围和订正步骤估算" if understood else None,
        "assumptions": ["题目清晰且材料完整"] if understood else [],
        "clarification_needed": clarification,
        "clarification_question": "需要完成哪些页？" if clarification else None,
        "adjustment_basis": supplement_basis,
        "location_text": None,
        "deadline_time": deadline_time,
        "is_splittable": True if understood else None,
        "minimum_chunk_minutes": 20 if understood else None,
        "preferred_chunk_minutes": 45 if understood else None,
        "requires_single_session": False if understood else None,
    }
    return json.dumps(payload, ensure_ascii=False)


class QueueCaller:
    def __init__(self, *values):
        self.values = list(values)
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        return self.values.pop(0)


def test_text_estimate_reestimate_and_user_adopted_minutes_keep_scope():
    material = make_material(text="完成高数第三章作业，前两题已做完")
    first = run_task_estimation(
        "draft_001", material, "", QueueCaller(estimate_json(90))
    ).draft
    assert first.result.recommended_minutes == 90
    assert first.call_count == 1

    second = run_task_estimation(
        first.draft_id,
        material,
        "我做数学比较慢，前两题已经完成",
        QueueCaller(estimate_json(110, "根据本份任务的做题速度与已完成部分调整")),
        confirmed_scope=first.result.scope_summary,
        confirmed_completion=first.result.completion_criteria,
        previous_result=first.result,
    ).draft
    assert second.result.recommended_minutes == 110
    assert "做题速度" in second.result.adjustment_basis
    confirmed = confirm_estimated_task(
        second,
        material,
        second.supplemental_context,
        second.result.task_name,
        second.result.scope_summary,
        second.result.completion_criteria,
        100,
    )
    assert confirmed.total_minutes == 100
    assert confirmed.duration_source == "user_modified"


def test_explicit_deadline_is_preserved_but_available_time_is_not_workload():
    material = make_material(text="今晚18:00前完成这份作业；我今天只有40分钟可投入")
    caller = QueueCaller(estimate_json(120, deadline_time="18:00"))
    draft = run_task_estimation("draft_deadline", material, "", caller).draft
    assert draft.result.deadline_time == "18:00"
    assert draft.result.recommended_minutes == 120
    assert "用户今天可用时长" in caller.calls[0][0]


def test_material_or_confirmed_scope_change_invalidates_old_estimate():
    material = make_material(text="完成第三章作业")
    draft = run_task_estimation(
        "draft_001", material, "", QueueCaller(estimate_json())
    ).draft
    with pytest.raises(TaskEstimationStale, match="重新估算"):
        confirm_estimated_task(
            draft,
            make_material(text="完成第三章和第四章作业"),
            "",
            draft.result.task_name,
            draft.result.scope_summary,
            draft.result.completion_criteria,
            draft.adopted_minutes,
        )
    with pytest.raises(TaskEstimationStale, match="重新估算"):
        confirm_estimated_task(
            draft,
            material,
            "",
            draft.result.task_name,
            "完成第三、四章",
            draft.result.completion_criteria,
            draft.adopted_minutes,
        )


def test_unresolved_or_malformed_estimate_never_becomes_addable_and_repair_is_bounded():
    material = make_material(text="做一下这份模糊的作业")
    unresolved = run_task_estimation(
        "draft_001",
        material,
        "",
        QueueCaller(estimate_json(understood=False, clarification=True)),
    ).draft
    assert unresolved.status == "needs_clarification"
    assert unresolved.adopted_minutes is None
    with pytest.raises(TaskEstimationError, match="先完成"):
        confirm_estimated_task(unresolved, material, "", "作业", "范围", "完成", 30)

    caller = QueueCaller("not-json", "still-not-json")
    with pytest.raises(TaskEstimationError, match="可靠估算"):
        run_task_estimation("draft_002", material, "", caller)
    assert len(caller.calls) == 2


def test_image_boundary_validates_bytes_and_requires_explicit_service_support():
    png = b"\x89PNG\r\n\x1a\n" + b"safe-image-bytes"
    material = make_material(
        image_name="task.png", image_mime="image/png", image_bytes=png
    )
    with pytest.raises(TaskEstimationImageUnsupported):
        run_task_estimation(
            "draft_image", material, "", QueueCaller(estimate_json()),
            image_caller=lambda *args: estimate_json(), image_supported=False,
        )
    with pytest.raises(TaskEstimationError, match="格式"):
        make_material(
            image_name="fake.png", image_mime="image/png", image_bytes=b"not-png"
        )

    captured = {}

    def message_call(messages, **kwargs):
        captured["messages"] = messages
        return estimate_json()

    adapter = TJUP2CallAdapter(
        call_function=lambda *args, **kwargs: estimate_json(),
        message_call_function=message_call,
        supports_image_inputs=True,
    )
    outcome = run_task_estimation(
        "draft_image",
        material,
        "",
        adapter.agent_caller,
        image_caller=adapter.task_estimation_caller,
        image_supported=adapter.supports_image_inputs,
    )
    assert outcome.draft.status == "ready"
    user_content = captured["messages"][1]["content"]
    assert user_content[0]["type"] == "text"
    assert user_content[1]["type"] == "image_url"
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_estimation_prompt_preserves_uncertain_user_knowledge():
    from src.task_estimation import build_task_estimate_prompt, make_material

    system, user = build_task_estimate_prompt(
        make_material(text="完成第3—6题"),
        "知道大概方法，但可能需要查笔记",
    )
    assert "不得扩写成已经完全掌握" in system
    assert "可能需要查笔记" in user


def test_image_service_and_unverifiable_response_have_distinct_safe_failures():
    from src.task_estimation import (
        TaskEstimationImageResponseError,
        TaskEstimationImageServiceError,
        make_material,
    )

    material = make_material(
        image_name="task.png", image_mime="image/png",
        image_bytes=b"\x89PNG\r\n\x1a\n" + b"safe",
    )

    def service_failure(*args):
        raise RuntimeError("private network detail")

    with pytest.raises(TaskEstimationImageServiceError) as captured:
        run_task_estimation(
            "image_service", material, "", lambda *_: "",
            image_caller=service_failure, image_supported=True,
        )
    assert "private" not in str(captured.value)

    with pytest.raises(TaskEstimationImageResponseError, match="无法验证"):
        run_task_estimation(
            "image_response", material, "", lambda *_: "still bad",
            repair_caller=lambda *_: "still bad",
            image_caller=lambda *_: "not json", image_supported=True,
        )


def test_confirmed_estimate_enters_canonical_ledger_and_plan_once_without_reestimate():
    estimate_calls = QueueCaller(estimate_json(90))
    material = make_material(text="完成高数第三章作业")
    store = {}
    draft = _run_estimator_action(
        store,
        SimpleNamespace(caller=estimate_calls, repair_caller=estimate_calls),
        SimpleNamespace(task_estimation_caller=None, supports_image_inputs=False),
        material,
        "",
    ).draft
    confirmed = confirm_estimated_task(
        draft,
        material,
        "",
        draft.result.task_name,
        draft.result.scope_summary,
        draft.result.completion_criteria,
        85,
    )

    planning_calls = []

    def planning_caller(system, user):
        planning_calls.append((system, user))
        return json.dumps({
            "schema_version": "p2.day-review.v1",
            "decision": "accept",
            "reason": None,
            "suggested_task_order": None,
            "include_low_attention": None,
        })

    controller = P2SessionController(
        store,
        planning_caller,
        map_data=DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu"),
        campus_id="weijinlu",
    )
    added = controller.add_confirmed_estimated_task_atomic(
        confirmed, reference_datetime=datetime(2026, 9, 5, 9, 0)
    )
    bundle = load_live_final_turn(store)
    task = next(item for item in bundle.state.tasks if item.task_ref == added.task_ref)
    binding = bundle.execution_context.binding_for(added.task_ref)
    assert task.title == "高数作业"
    assert task.total_minutes == 85
    assert task.completed_minutes == 0
    assert task.total_source is SourceKind.USER_CONFIRMED
    assert binding.effective_duration_minutes == 85
    assert binding.duration_source == "user_explicit"
    assert binding.scope_summary == "完成第三章习题并订正错题"
    assert binding.completion_criteria == "全部作答并完成订正"
    assert bundle.result.allocation_plan.planned_minutes_by_task[added.task_ref] == 85
    assert len(estimate_calls.calls) == 1

    repeated = controller.add_confirmed_estimated_task_atomic(
        confirmed, reference_datetime=datetime(2026, 9, 5, 9, 0)
    )
    assert repeated.duplicate is True
    assert repeated.task_ref == added.task_ref
    assert len(store["p2_state"].tasks) == 1
    assert len(planning_calls) == 1

    reliable_bundle = load_live_final_turn(store)
    impossible_location = replace(
        confirmed, draft_id="draft_002", location_text="不存在的校内地点"
    )
    with pytest.raises(ValueError, match="not resolved"):
        controller.add_confirmed_estimated_task_atomic(
            impossible_location, reference_datetime=datetime(2026, 9, 5, 9, 0)
        )
    assert load_live_final_turn(store) is reliable_bundle
    assert len(store["p2_state"].tasks) == 1
    assert store["task_estimate_draft"] is draft


def test_live_page_exposes_unified_task_entry_without_calling_service_on_open_or_rerun(monkeypatch):
    from streamlit.testing.v1 import AppTest

    for name in ("TJU_LLM_BASE_URL", "TJU_LLM_API_KEY", "TJU_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    network_calls = []
    monkeypatch.setattr(
        "requests.post", lambda *args, **kwargs: network_calls.append((args, kwargs))
    )
    app = AppTest.from_file("src/p2_live_main.py").run(timeout=20)
    assert not app.exception
    assert app.selectbox[0].value == "北洋园校区"
    assert any(item.key == "cf_material_text" for item in app.text_area)
    assert not any(item.key == "cf_material_supplement" for item in app.text_area)
    assert any(item.label == "帮我看看" for item in app.button)
    assert not any(item.label == "帮我估时" for item in app.button)
    # Streamlit 1.31 AppTest serializes selectbox display labels on rerun.  A
    # single-digit system hour/minute would otherwise be compared as ``0``
    # against the formatted ``00`` option even though the live widget value is
    # correctly an integer.  Pin the test-only reference controls to values
    # whose labels and values have the same textual form.
    app.session_state["p2_live_reference_hour"] = 12
    app.session_state["p2_live_reference_minute"] = 30
    app.button(key='cf_material_extra_toggle').click().run(timeout=20)
    assert any(item.key == "cf_material_supplement" for item in app.text_area)
    app.run(timeout=20)
    assert network_calls == []
