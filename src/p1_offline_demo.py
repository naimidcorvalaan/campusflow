"""P1j 固定离线样例；不从 tests 导入，不访问网络。"""
import json
from datetime import timedelta
from pathlib import Path

from src.campus_map import load_campus_map
from src.p1_planning_pipeline import run_p1_planning
from src.p1_result_formatter import format_p1_result
from src.p1_route_models import RouteDataTrust
from src.p1_route_provider import CampusMapRouteProvider


EXAMPLE_ONE = "我在宿舍，12:00 去教学楼上课，想背半小时单词。"
EXAMPLE_TWO = "我在图书馆，11:30 去教学楼上课，先整理 30 分钟实验报告。"
EXAMPLES = (EXAMPLE_ONE, EXAMPLE_TWO)


def is_supported_example(text):
    return text.strip() in EXAMPLES if isinstance(text, str) else False


def _task_reply(text):
    title = "背单词" if text == EXAMPLE_ONE else "整理实验报告"
    fragment = "背半小时单词" if text == EXAMPLE_ONE else "整理 30 分钟实验报告"
    data = {"schema_version":"p1.task-understanding.v1","task_interpretations":[{"task_ref":"task-1","title":title,"original_text":fragment,
            "understood_features":{"location_requirement":"no_specific_location","location_text":None,"environment_requirements":[],"equipment_requirements":[],"estimated_total_minutes":30,"is_splittable":False,"minimum_slice_minutes":30,"attention_required":"low","interruption_allowed":True,"may_have_open_hours":False},
            "field_evidence":{"location_requirement":{"source":"ai_extracted_from_user_text","explanation":"根据用户描述"},"estimated_total_minutes":{"source":"ai_extracted_from_user_text","explanation":"用户明确说30分钟"},"is_splittable":{"source":"ai_estimated","explanation":"按任务特点暂估"},"minimum_slice_minutes":{"source":"ai_estimated","explanation":"按任务特点暂估"},"attention_required":{"source":"ai_estimated","explanation":"按任务特点暂估"},"interruption_allowed":{"source":"ai_estimated","explanation":"按任务特点暂估"},"may_have_open_hours":{"source":"ai_estimated","explanation":"按任务特点暂估"}},"needs_confirmation":[]}],"clarification_questions":[]}
    return json.dumps(data, ensure_ascii=False)


def _window_reply(text, reference):
    current = "宿舍" if text == EXAMPLE_ONE else "图书馆"
    hour, minute = (12, 0) if text == EXAMPLE_ONE else (11, 30)
    start = reference.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if start <= reference:
        start += timedelta(days=1)
    data = {"schema_version":"p1.window-context.v1","current_context":{"current_location_text":current,"current_location_fragment":current,"assumed_current_datetime":None,"assumed_current_datetime_fragment":None,"field_evidence":{"current_location_text":{"source":"ai_extracted_from_user_text","explanation":"用户明确说明地点"}},"needs_confirmation":[]},"commitments":[{"commitment_ref":"class-1","title":"上课","original_text":"12:00 去教学楼上课" if text == EXAMPLE_ONE else "11:30 去教学楼上课","starts_at":start.isoformat(),"ends_at":None,"location_text":"教学楼","availability_during":None,"field_evidence":{"starts_at":{"source":"ai_extracted_from_user_text","explanation":"用户明确说明时间"},"location_text":{"source":"ai_extracted_from_user_text","explanation":"用户明确说明地点"}},"needs_confirmation":[]}],"window_constraints":{"free_duration_minutes":None,"ends_at":None,"user_buffer_minutes":None,"field_evidence":{},"needs_confirmation":[]},"clarification_questions":[]}
    return json.dumps(data, ensure_ascii=False)


def _candidate_reply(system, user):
    return json.dumps({"schema_version":"p1.candidate.v1","decision_status":"proposed","primary_candidate":{"candidate_ref":"demo-primary","steps":[{"task_ref":"task-1","planned_minutes":30,"execution_context":"free_window","commitment_ref":None}],"rationale":"利用当前空档完成任务","assumptions":[],"warnings":[]},"alternative_candidate":None,"safe_summary":"离线演示建议"}, ensure_ascii=False)


def run_offline_demo(text, reference_datetime):
    if not is_supported_example(text):
        return None
    root = Path(__file__).resolve().parents[1]
    map_path = root / "tests" / "fixtures" / "sample_campus_map.json"
    provider = CampusMapRouteProvider(load_campus_map(str(map_path)), RouteDataTrust.SYNTHETIC_TEST)
    return format_p1_result(run_p1_planning(text, reference_datetime,
                                             lambda system, user: _task_reply(text),
                                             lambda system, user: _window_reply(text, reference_datetime),
                                             _candidate_reply, provider))
