"""P1e 候选建议的 system/user 分离提示词。"""
import json

from src.p1_context_bundle import P1ContextBundle


def build_p1_candidate_system_prompt():
    return """
你为 CampusFlow 提出一个首选候选方案和可选备选。只返回一个纯 JSON 对象，不得使用 Markdown、解释文字或额外字段。
决策优先考虑：用户当前意愿、到期或必须任务、基本生活需要、精力注意力、减少步行、利用碎片时间。
只能引用上下文给出的 task_ref 与 commitment_ref，不得新增任务或修改任务属性。候选步骤字段仅为 task_ref、planned_minutes、execution_context、commitment_ref；execution_context 只能是 free_window 或 commitment。
地点任务可提出但必须保持暂定，不得声称路线可行。不要输出路径、步行分钟、ETA、到达时间、缓冲、地图 ID、可行性、完成状态或生命周期更新。
唯一允许的完整结构：
{"schema_version":"p1.candidate.v1","decision_status":"proposed|missing_information|no_executable_tasks","primary_candidate":null,"alternative_candidate":null,"safe_summary":"简短中文说明"}
primary_candidate 和 alternative_candidate 非 null 时必须且只能是：{"candidate_ref":"稳定引用","steps":[{"task_ref":"已有任务引用","planned_minutes":正整数,"execution_context":"free_window|commitment","commitment_ref":null}],"rationale":"简短中文理由","assumptions":["假设"],"warnings":["警告"]}。
free_window 时 commitment_ref 必须为 null；commitment 时必须是已有安排引用。proposed 时 primary_candidate 必须存在，alternative_candidate 可为 null；missing_information 和 no_executable_tasks 时两个候选都必须为 null。若没有合理候选，使用后两种状态；备选可为 null，不要强行编造。
若任务总时长超过当前窗口，但 is_splittable=true，必须优先提出不低于 minimum_slice_minutes 且能放入窗口的部分执行方案，不得仅因完整任务放不下就返回无候选。planned_minutes 只表示本窗口推进的分钟，不表示任务已经全部完成；推荐理由应明确“先推进一部分”。例如预计 120 分钟、最小片段 30 分钟的计组实验，在较短窗口中可以先安排 30～90 分钟。
""".strip()


def build_p1_candidate_user_prompt(bundle):
    if not isinstance(bundle, P1ContextBundle):
        raise TypeError("bundle 必须是 P1ContextBundle。")
    window = bundle.window_document
    context = {
        "user_text": window.original_user_input if window is not None else (None if bundle.task_document is None else bundle.task_document.original_user_input),
        "has_time_boundary": bundle.has_time_boundary,
        "route_context_ready": bundle.route_context_ready,
        "current_location": None if bundle.current_context is None else bundle.current_context.current_location_text,
        "time_limit": None if window is None or window.effective_time_constraint is None else window.effective_time_constraint.isoformat(),
        "commitments": [] if window is None else [{"commitment_ref": x.commitment_ref, "starts_at": None if x.starts_at is None else x.starts_at.isoformat(), "ends_at": None if x.ends_at is None else x.ends_at.isoformat(), "availability": x.availability_during.value, "location": x.location_text} for x in window.commitments],
        "tasks": [{"task_ref": x.task_ref, "title": x.title, "features": {"location_requirement": x.features.location_requirement.value, "estimated_total_minutes": x.features.estimated_total_minutes, "is_splittable": x.features.is_splittable, "minimum_slice_minutes": x.features.minimum_slice_minutes, "attention": None if x.features.attention_required is None else x.features.attention_required.value}, "needs_confirmation": list(x.needs_confirmation), "evidence": {k: v.source.value for k, v in x.field_evidence.items()}} for x in bundle.tasks],
        "questions": [{"target_ref": x.target_ref, "field_name": x.field_name} for x in bundle.prioritized_questions],
    }
    return "安全上下文：\n" + json.dumps(context, ensure_ascii=False, separators=(",", ":"))


def build_p1_candidate_prompt(bundle):
    return build_p1_candidate_system_prompt(), build_p1_candidate_user_prompt(bundle)
