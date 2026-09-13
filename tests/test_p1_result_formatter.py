from dataclasses import replace
from datetime import datetime, timedelta

from src.p1_result_formatter import format_p1_result
from src.p1_route_models import RouteDataTrust
from tests.test_p1_route_validator import FakeProvider
from tests.test_p1_planning_pipeline import (NOW, candidate_payload, plan_candidate,
                                              plan_multi, run, task_json, window_json,
                                              VerifiedRouteProvider)


def test_single_headline_card_and_synthetic_notice_have_no_refs():
    view = format_p1_result(run([task_json()], [window_json()], [candidate_payload(plan_candidate())]))
    assert "背单词 30 分钟" in view.headline
    assert view.primary_card is not None and view.primary_card.total_task_minutes == 30
    assert view.status_title == "当前方案"
    assert view.route_data_notice is not None and "演示路线数据" in view.route_data_notice.message
    assert "input-task-1" not in view.headline


def test_multi_step_headline_uses_then_and_final_destination():
    result = run([task_json()], [window_json(free=70)], [candidate_payload(plan_multi([30, 30]), plan_candidate("alt", 30))])
    view = format_p1_result(result)
    assert "先" in view.headline and "然后" in view.headline
    assert "教学楼" in view.headline
    assert view.alternative_summary is None


def test_promoted_alternative_is_displayed_as_current_plan_and_has_short_summary():
    result = run([task_json()], [window_json(free=70)], [candidate_payload(plan_multi([30, 30]), plan_candidate("alt", 30))])
    view = format_p1_result(result)
    assert view.primary_card.steps[0].title == "背单词"
    assert view.alternative_summary is None
    assert view.selection_notice is not None


def test_ai_estimate_tentative_notice_question_and_source_label():
    view = format_p1_result(run([task_json(ai=True)], [window_json()], [candidate_payload(plan_candidate())], route_provider=VerifiedRouteProvider()))
    assert view.status_title == "当前方案"
    assert view.tentative_notice is not None and "AI 暂估" in view.source_notes
    assert view.primary_question is not None
    assert "（AI 暂估）" in view.primary_question.text
    assert [item.label for item in view.primary_question.quick_options] == ["就按这个", "调整时长"]
    confirm, adjust = view.primary_question.quick_options
    assert confirm.value == "120" and confirm.target_ref == "input-task-1"
    assert confirm.field_name == "estimated_total_minutes" and confirm.target_label == "背单词"
    assert adjust.target_ref == confirm.target_ref and adjust.value == "120"


def test_location_and_time_questions_get_deterministic_options():
    location = format_p1_result(run([task_json()], [window_json(current=None)], [candidate_payload(plan_candidate())]))
    time = format_p1_result(run([task_json()], [window_json(commitment=False)], [candidate_payload(plan_candidate())]))
    assert location.primary_question.quick_options[0].label == "填写地点"
    assert [item.label for item in time.primary_question.quick_options][:3] == ["15 分钟", "30 分钟", "1 小时"]


def test_route_trust_notices_cover_unverified_and_real_verified():
    synthetic = format_p1_result(run([task_json()], [window_json()], [candidate_payload(plan_candidate())]))
    real = format_p1_result(run([task_json()], [window_json()], [candidate_payload(plan_candidate())], route_provider=VerifiedRouteProvider()))
    assert "演示路线数据" in synthetic.route_data_notice.message
    assert "已核验路线数据" in real.route_data_notice.message
    assert "演示路线数据" not in real.route_data_notice.message
    assert "AI 暂估" not in real.route_data_notice.message


def test_tentative_candidate_keeps_single_current_plan_title():
    view = format_p1_result(run([task_json(ai=True)], [window_json()], [candidate_payload(plan_candidate())], route_provider=VerifiedRouteProvider()))
    assert view.primary_card is not None
    assert view.status_title == "当前方案"
    assert "AI 暂定建议" not in view.status_title


def test_no_candidate_statuses_and_quick_action_mapping():
    no_task = format_p1_result(run([task_json(empty=True)], [window_json()], [RuntimeError("unused")]))
    infeasible = format_p1_result(run([task_json()], [window_json(free=70)], [candidate_payload(plan_multi([30, 30])), candidate_payload(plan_multi([30, 30]))]))
    assert "告诉我" in no_task.headline and no_task.primary_card is None
    assert "来不及" in infeasible.headline
    assert [item.label for item in infeasible.quick_actions] == ["延长可用时间", "换个任务", "修改地点"]


def test_cross_midnight_keeps_full_date_and_other_questions_are_safe_unique():
    result = run([task_json()], [window_json()], [candidate_payload(plan_candidate())])
    validation = replace(result.final_route_validation, expected_finish_at=datetime(2026, 5, 2, 0, 5))
    result = replace(result, final_route_validation=validation, other_questions=("补充地点", "补充地点", "Authorization Bearer"))
    view = format_p1_result(result)
    assert view.primary_card.expected_finish_text == "2026-05-02 00:05"
    assert view.other_questions == ("补充地点",)
    assert all("Bearer" not in item for item in view.other_questions)


def test_feasible_unselected_candidate_is_marked_as_available_alternative():
    alternative = plan_multi([30, 30]); alternative["candidate_ref"] = "alternative"
    result = run([task_json()], [window_json()], [candidate_payload(plan_candidate(), alternative)])
    view = format_p1_result(result)
    assert view.alternative_summary is not None
    assert view.alternative_summary.startswith("可作为备选：")


def test_unvalidated_candidate_is_never_described_as_feasible_backup():
    alternative = plan_multi([30, 30]); alternative["candidate_ref"] = "alternative"
    result = run([task_json()], [window_json()], [candidate_payload(plan_candidate(), alternative)])
    selection = replace(result.recommendation_result.first_route_selection, alternative_validation=None)
    result = replace(result, recommendation_result=replace(result.recommendation_result, first_route_selection=selection))
    view = format_p1_result(result)
    assert view.alternative_summary.startswith("另一个思路（尚未验证）：")


def test_regenerated_success_does_not_resurface_failed_first_round_candidate():
    result = run([task_json()], [window_json(free=70)], [candidate_payload(plan_multi([30, 30])), candidate_payload(plan_candidate("new", 30))])
    view = format_p1_result(result)
    assert view.alternative_summary is None
    assert view.selection_notice is None


def test_each_route_data_trust_has_its_own_notice_or_none_without_validation():
    synthetic_result = run([task_json()], [window_json()], [candidate_payload(plan_candidate())])
    synthetic = format_p1_result(synthetic_result)
    estimated = format_p1_result(run([task_json()], [window_json()], [candidate_payload(plan_candidate())], route_provider=FakeProvider()))
    real = format_p1_result(run([task_json()], [window_json()], [candidate_payload(plan_candidate())], route_provider=VerifiedRouteProvider()))
    assert "演示路线数据" in synthetic.route_data_notice.message
    assert "未核验估计" in estimated.route_data_notice.message
    assert "已核验路线数据" in real.route_data_notice.message
    assert format_p1_result(replace(synthetic_result, final_route_validation=None)).route_data_notice is None


def test_source_notes_are_current_candidate_only_deduplicated_and_human_readable():
    view = format_p1_result(run([task_json()], [window_json()], [candidate_payload(plan_candidate())]))
    assert "根据你的描述" in view.source_notes
    assert "系统默认" in view.source_notes and "程序确认" in view.source_notes
    assert len(view.source_notes) == len(set(view.source_notes))
    assert not any("_" in item for item in view.source_notes)


def test_safe_filter_drops_internal_refs_sensitive_and_container_text_everywhere():
    result = run([task_json()], [window_json()], [candidate_payload(plan_candidate())])
    bad = replace(result.final_candidate, rationale="task_ref=input-task-1", assumptions=("commitment_ref=class-1", "['dormitory']"), warnings=("api_key=x", "object at 0x123"))
    result = replace(result, final_candidate=bad, primary_question="Authorization Bearer", other_questions=("path=['dormitory']", "正常问题"))
    view = format_p1_result(result)
    visible = " ".join([view.headline, view.primary_card.rationale or "", *view.primary_card.assumptions, *view.primary_card.warnings, view.primary_question.text, *view.other_questions])
    assert "task_ref" not in visible and "commitment_ref" not in visible
    assert "api_key" not in visible and "Bearer" not in visible and "dormitory" not in visible


def test_missing_statuses_and_question_options_are_rendered_without_internal_enums():
    no_task = format_p1_result(run([task_json(empty=True)], [window_json()], [RuntimeError("unused")]))
    failed = format_p1_result(run([task_json()], [window_json()], ["bad", "bad"] ))
    partial = format_p1_result(run(["bad", "bad"], [window_json()], [RuntimeError("unused")]))
    assert no_task.primary_question.quick_options[0].label == "添加任务"
    assert "无法" in failed.headline and "格式" in partial.headline


def test_headline_does_not_append_task_location_without_confirmed_next_commitment():
    result = run([task_json(location_requirement="specific_location", location_text="图书馆")], [window_json(commitment=False, free=60)], [candidate_payload(plan_candidate())])
    view = format_p1_result(result)
    assert "前往图书馆" not in view.headline


def test_headline_without_location_or_next_commitment_does_not_invent_destination():
    result = run([task_json()], [window_json(commitment=False, free=60)], [candidate_payload(plan_candidate())])
    view = format_p1_result(result)
    assert "前往" not in view.headline
    assert view.primary_question is None
