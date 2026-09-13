"""严格离线解析 P1e 候选；程序派生暂定与路线验证标记。"""
import json
from datetime import timedelta

from src.p1_candidate_extraction_models import P1CandidateParseResult
from src.p1_candidate_models import (CANDIDATE_SCHEMA_VERSION, CandidateDecisionStatus, CandidateDocument, CandidateStep, ExecutionContext, MAX_CANDIDATE_STEPS, ProposedCandidate)
from src.p1_context_bundle import P1ContextBundle, SourcedField
from src.p1_models import AttentionLevel, LocationRequirement, SourceKind
from src.p1_window_models import AvailabilityLevel


TOP = {"schema_version", "decision_status", "primary_candidate", "alternative_candidate", "safe_summary"}
CANDIDATE = {"candidate_ref", "steps", "rationale", "assumptions", "warnings"}
STEP = {"task_ref", "planned_minutes", "execution_context", "commitment_ref"}


def _reject(kind, message): return P1CandidateParseResult("rejected", kind, message)
def _error(message): raise ValueError(message)
def _text(value, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum or "\n" in value: _error("字段文本无效。")
    return value.strip()
def _exact(value, fields):
    if not isinstance(value, dict) or set(value) != fields: _error("字段结构无效。")
    return value
def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError("duplicate")
        result[key] = value
    return result


def _parse_candidate(raw, bundle):
    item = _exact(raw, CANDIDATE)
    ref = _text(item["candidate_ref"], 64)
    if not isinstance(item["steps"], list) or not item["steps"] or len(item["steps"]) > MAX_CANDIDATE_STEPS: _error("步骤无效。")
    tasks = {x.task_ref: x for x in bundle.tasks}
    commitments = {} if bundle.window_document is None else {x.commitment_ref: x for x in bundle.commitments}
    steps, used_ai, route_needed, warnings = [], [], False, list(_string_list(item["warnings"], 8, 200))
    total_free, commitment_minutes, task_minutes, task_counts = 0, {}, {}, {}
    for raw_step in item["steps"]:
        step = _exact(raw_step, STEP)
        task_ref = _text(step["task_ref"], 64)
        if task_ref not in tasks: _error("任务引用不存在。")
        minutes = step["planned_minutes"]
        if type(minutes) is not int or minutes <= 0 or minutes > 10080: _error("建议分钟无效。")
        try: context = ExecutionContext(step["execution_context"])
        except (TypeError, ValueError): _error("执行环境无效。")
        commitment_ref = step["commitment_ref"]
        task = tasks[task_ref]
        task_minutes[task_ref] = task_minutes.get(task_ref, 0) + minutes
        task_counts[task_ref] = task_counts.get(task_ref, 0) + 1
        _validate_task_minutes(task, minutes)
        if task.features.location_requirement is not LocationRequirement.NO_SPECIFIC_LOCATION:
            route_needed = True
        for name, evidence in task.field_evidence.items():
            if evidence.source is SourceKind.AI_ESTIMATED:
                field = SourcedField("task", task_ref, name, evidence)
                if field not in used_ai: used_ai.append(field)
        if context is ExecutionContext.FREE_WINDOW:
            if commitment_ref is not None: _error("自由窗口不得引用安排。")
            total_free += minutes
        else:
            if not isinstance(commitment_ref, str) or commitment_ref not in commitments: _error("固定安排引用不存在。")
            commitment = commitments[commitment_ref]
            if commitment.starts_at is None or commitment.ends_at is None: _error("安排时长未知。")
            if commitment.availability_during is AvailabilityLevel.UNAVAILABLE: _error("不可用安排不能执行任务。")
            if task.features.location_requirement is not LocationRequirement.NO_SPECIFIC_LOCATION: _error("安排期间不能执行地点任务。")
            commitment_minutes[commitment_ref] = commitment_minutes.get(commitment_ref, 0) + minutes
            if commitment.availability_during is AvailabilityLevel.LOW_ATTENTION and task.features.attention_required is AttentionLevel.HIGH:
                warning = "这项任务需要较高注意力，在低注意力时段执行可能影响效率。"
                if warning not in warnings: warnings.append(warning)
        steps.append(CandidateStep(task_ref, minutes, context, commitment_ref))
    for task_ref, total_minutes in task_minutes.items():
        task = tasks[task_ref]
        if task.features.estimated_total_minutes is not None and total_minutes > task.features.estimated_total_minutes: _error("任务累计时长超限。")
        if task.features.is_splittable is False and (task_counts[task_ref] != 1 or total_minutes != task.features.estimated_total_minutes): _error("不可拆分任务只能完整出现一次。")
    for commitment_ref, used_minutes in commitment_minutes.items():
        commitment = commitments[commitment_ref]
        duration = int((commitment.ends_at - commitment.starts_at).total_seconds() // 60)
        if used_minutes > duration: _error("固定安排累计时长超限。")
    if bundle.window_document is not None and bundle.window_document.effective_time_constraint is not None:
        available = int((bundle.window_document.effective_time_constraint - bundle.current_context.current_datetime).total_seconds() // 60)
        if total_free > available: _error("超出自由窗口时长。")
    uncertain = any(task.needs_confirmation for task in (tasks[x.task_ref] for x in steps))
    window_uncertain = bundle.window_document is None or not bundle.has_time_boundary or bundle.has_unresolved_commitment_start or (bundle.primary_question is not None and bundle.primary_question.source == "window")
    tentative = bool(used_ai) or route_needed or uncertain or window_uncertain
    return ProposedCandidate(ref, tuple(steps), _text(item["rationale"]), _string_list(item["assumptions"], 8, 200), tuple(warnings), bool(used_ai), route_needed, tentative, tuple(used_ai))


def _string_list(value, maximum_items, maximum_length):
    if not isinstance(value, list) or len(value) > maximum_items: _error("文本数组无效。")
    result = tuple(_text(x, maximum_length) for x in value)
    if len(set(result)) != len(result): _error("文本数组重复。")
    return result


def _validate_task_minutes(task, minutes):
    features = task.features
    total = features.estimated_total_minutes
    if total is not None and minutes > total: _error("超出任务总时长。")
    if features.is_splittable is False and total is not None and minutes != total: _error("不可拆分任务必须一次完成。")
    if features.is_splittable is True and features.minimum_slice_minutes is not None and minutes < features.minimum_slice_minutes and (total is None or minutes != total): _error("低于最小有效片段。")


def parse_p1_candidate(raw_reply, bundle):
    if not isinstance(bundle, P1ContextBundle): return _reject("validation_error", "上下文无效。")
    if not isinstance(raw_reply, str) or not raw_reply.strip() or len(raw_reply) > 50000: return _reject("format_error", "模型返回格式无效。")
    text = raw_reply.strip()
    if "```" in text or not text.startswith("{") or not text.endswith("}"): return _reject("format_error", "模型返回必须是纯 JSON 对象。")
    try: top = json.loads(text, object_pairs_hook=_pairs, parse_constant=lambda x: (_ for _ in ()).throw(ValueError()))
    except Exception: return _reject("format_error", "模型返回不是合法 JSON。")
    try:
        top = _exact(top, TOP)
        if top["schema_version"] != CANDIDATE_SCHEMA_VERSION: _error("schema_version 无效。")
        status = CandidateDecisionStatus(top["decision_status"])
        primary_raw, alternative_raw = top["primary_candidate"], top["alternative_candidate"]
        if not bundle.tasks and (primary_raw is not None or alternative_raw is not None): _error("没有任务时不能输出候选。")
        if status is CandidateDecisionStatus.PROPOSED and primary_raw is None: _error("已提出状态必须有首选。")
        if status is not CandidateDecisionStatus.PROPOSED and (primary_raw is not None or alternative_raw is not None): _error("非提出状态不能有候选。")
        primary = None if primary_raw is None else _parse_candidate(primary_raw, bundle)
        alternative = None if alternative_raw is None else _parse_candidate(alternative_raw, bundle)
        if primary is not None and alternative is not None and primary.candidate_ref == alternative.candidate_ref: _error("候选引用不能重复。")
        if primary is not None and alternative is not None and primary.steps == alternative.steps: _error("首选和备选不能相同。")
        return P1CandidateParseResult("ok", None, "候选解析成功。", CandidateDocument(status, primary, alternative, _text(top["safe_summary"])))
    except (ValueError, TypeError): return _reject("validation_error", "候选内容无效。")
