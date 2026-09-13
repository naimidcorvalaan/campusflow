"""严格、离线解析 ``p1.window-context.v1``。不调用模型、地图或外部服务。"""
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.p1_models import FieldEvidence, SourceKind
from src.p1_window_extraction_models import P1WindowParseResult
from src.p1_window_models import (
    AvailabilityLevel, CurrentContext, DEFAULT_SAFETY_BUFFER_MINUTES,
    FixedCommitment, MAX_COMMITMENTS, MAX_WINDOW_EXPLANATION_LENGTH,
    MAX_WINDOW_FRAGMENT_LENGTH, MAX_WINDOW_LOCATION_LENGTH, MAX_WINDOW_MINUTES,
    MAX_WINDOW_OUTPUT_LENGTH, MAX_WINDOW_QUESTIONS, MAX_WINDOW_REF_LENGTH,
    MAX_WINDOW_TEXT_LENGTH, MAX_WINDOW_TITLE_LENGTH, WINDOW_SCHEMA_VERSION,
    WindowClarificationQuestion, WindowConstraints, WindowContextDocument,
)

TOP = {"schema_version", "current_context", "commitments", "window_constraints", "clarification_questions"}
CURRENT = {"current_location_text", "current_location_fragment", "assumed_current_datetime", "assumed_current_datetime_fragment", "field_evidence", "needs_confirmation"}
COMMITMENT = {"commitment_ref", "title", "original_text", "starts_at", "ends_at", "location_text", "availability_during", "field_evidence", "needs_confirmation"}
CONSTRAINTS = {"free_duration_minutes", "ends_at", "user_buffer_minutes", "field_evidence", "needs_confirmation"}
EVIDENCE = {"source", "explanation"}
QUESTION = {"question_id", "target_ref", "field_name", "question", "blocking", "quick_options"}
CURRENT_FIELDS = {"current_location_text", "assumed_current_datetime"}
COMMITMENT_FIELDS = {"starts_at", "ends_at", "location_text", "availability_during"}
CONSTRAINT_FIELDS = {"free_duration_minutes", "ends_at", "user_buffer_minutes"}
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
CHINESE = re.compile(r"[\u4e00-\u9fff]")


class _Error(ValueError):
    def __init__(self, kind: str, message: str):
        ValueError.__init__(self, message)
        self.kind, self.message = kind, message


def _bad(kind: str, message: str) -> None:
    raise _Error(kind, message)


def _pairs(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject(kind: str, message: str) -> P1WindowParseResult:
    return P1WindowParseResult("rejected", kind, message)


def _exact(value: object, fields: set) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        _bad("format_error", "模型返回字段结构无效。")
    return value


def _text(value: object, maximum: int, message: str) -> str:
    if not isinstance(value, str): _bad("validation_error", message)
    result = value.strip()
    if not result or len(result) > maximum or "\n" in result or "\r" in result:
        _bad("validation_error", message)
    return result


def _nullable_text(value: object, maximum: int, message: str) -> Optional[str]:
    return None if value is None else _text(value, maximum, message)


def _datetime(value: object, field_name: str) -> Optional[datetime]:
    if value is None: return None
    if not isinstance(value, str): _bad("validation_error", field_name + " 无效。")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _bad("validation_error", field_name + " 无效。")
    if parsed.tzinfo is not None: _bad("validation_error", field_name + " 不能带时区。")
    return parsed


def _minutes(value: object, field_name: str) -> Optional[int]:
    if value is None: return None
    if type(value) is not int or value <= 0 or value > MAX_WINDOW_MINUTES:
        _bad("validation_error", field_name + " 无效。")
    return value


def _evidence(value: object, allowed: set, values: Dict[str, object]) -> Dict[str, FieldEvidence]:
    if not isinstance(value, dict): _bad("format_error", "field_evidence 无效。")
    result = {}
    for name, raw in value.items():
        if name not in allowed: _bad("format_error", "field_evidence 引用未知字段。")
        if values.get(name) is None: _bad("validation_error", "空字段不能带来源。")
        item = _exact(raw, EVIDENCE)
        if item["source"] != SourceKind.AI_EXTRACTED_FROM_USER_TEXT.value:
            _bad("validation_error", "模型不得声明非用户原话来源。")
        explanation = _text(item["explanation"], MAX_WINDOW_EXPLANATION_LENGTH, "来源解释无效。")
        if CHINESE.search(explanation) is None: _bad("validation_error", "来源解释必须是中文。")
        result[name] = FieldEvidence(SourceKind.AI_EXTRACTED_FROM_USER_TEXT, explanation)
    for name, raw_value in values.items():
        if raw_value is not None and name not in result:
            _bad("validation_error", "非空用户提取字段必须有来源。")
    return result


def _needs(value: object, allowed: set) -> Tuple[str, ...]:
    if not isinstance(value, list) or len(value) > len(allowed): _bad("validation_error", "needs_confirmation 无效。")
    if any(not isinstance(x, str) or x not in allowed for x in value) or len(set(value)) != len(value):
        _bad("validation_error", "needs_confirmation 无效。")
    return tuple(value)


def _fragment(value: object, user_text: str, message: str) -> str:
    fragment = _text(value, MAX_WINDOW_FRAGMENT_LENGTH, message)
    if fragment not in user_text: _bad("validation_error", "原始片段不在用户输入中。")
    return fragment


def _parse_current(raw: object, user_text: str, reference: datetime) -> CurrentContext:
    item = _exact(raw, CURRENT)
    location = _nullable_text(item["current_location_text"], MAX_WINDOW_LOCATION_LENGTH, "当前位置无效。")
    assumed = _datetime(item["assumed_current_datetime"], "假定当前时间")
    if (location is None) != (item["current_location_fragment"] is None): _bad("validation_error", "当前位置片段不一致。")
    if location is not None: _fragment(item["current_location_fragment"], user_text, "当前位置片段无效。")
    if (assumed is None) != (item["assumed_current_datetime_fragment"] is None): _bad("validation_error", "假定时间片段不一致。")
    if assumed is not None: _fragment(item["assumed_current_datetime_fragment"], user_text, "假定时间片段无效。")
    values = {"current_location_text": location, "assumed_current_datetime": assumed}
    evidence = _evidence(item["field_evidence"], CURRENT_FIELDS, values)
    needs = _needs(item["needs_confirmation"], CURRENT_FIELDS)
    if location is None and "current_location_text" not in needs: _bad("validation_error", "缺少当前位置必须待确认。")
    evidence["reference_datetime"] = FieldEvidence(SourceKind.SYSTEM_VERIFIED, "程序传入可信时间")
    return CurrentContext(reference, assumed or reference, location, evidence, needs)


def _parse_commitment(raw: object, user_text: str) -> FixedCommitment:
    item = _exact(raw, COMMITMENT)
    ref = _text(item["commitment_ref"], MAX_WINDOW_REF_LENGTH, "commitment_ref 无效。")
    if REF.fullmatch(ref) is None: _bad("validation_error", "commitment_ref 无效。")
    starts, ends = _datetime(item["starts_at"], "开始时间"), _datetime(item["ends_at"], "结束时间")
    if starts is not None and ends is not None and ends < starts: _bad("validation_error", "安排结束早于开始。")
    location = _nullable_text(item["location_text"], MAX_WINDOW_LOCATION_LENGTH, "安排地点无效。")
    availability_raw = item["availability_during"]
    if availability_raw is None: availability = AvailabilityLevel.UNAVAILABLE
    else:
        try: availability = AvailabilityLevel(availability_raw)
        except (TypeError, ValueError): _bad("validation_error", "可用程度无效。")
    values = {"starts_at": starts, "ends_at": ends, "location_text": location, "availability_during": availability_raw}
    evidence = _evidence(item["field_evidence"], COMMITMENT_FIELDS, values)
    if availability_raw is None: evidence["availability_during"] = FieldEvidence(SourceKind.SYSTEM_DEFAULT, "未声明时保守不可用")
    needs = _needs(item["needs_confirmation"], COMMITMENT_FIELDS)
    if starts is None and "starts_at" not in needs: _bad("validation_error", "缺少开始时间必须待确认。")
    if location is None and "location_text" not in needs: _bad("validation_error", "缺少安排地点必须待确认。")
    if availability in (AvailabilityLevel.LOW_ATTENTION, AvailabilityLevel.FULLY_AVAILABLE) and ends is None and "ends_at" not in needs:
        _bad("validation_error", "允许课堂任务但缺少结束时间必须待确认。")
    return FixedCommitment(ref, _text(item["title"], MAX_WINDOW_TITLE_LENGTH, "标题无效。"), _fragment(item["original_text"], user_text, "原始片段无效。"), starts, ends, location, availability, evidence, needs)


def _parse_constraints(raw: object) -> WindowConstraints:
    item = _exact(raw, CONSTRAINTS)
    duration, ends, buffer = _minutes(item["free_duration_minutes"], "空闲时长"), _datetime(item["ends_at"], "空闲结束时间"), _minutes(item["user_buffer_minutes"], "安全缓冲")
    values = {"free_duration_minutes": duration, "ends_at": ends, "user_buffer_minutes": buffer}
    evidence = _evidence(item["field_evidence"], CONSTRAINT_FIELDS, values)
    needs = _needs(item["needs_confirmation"], CONSTRAINT_FIELDS)
    if buffer is None:
        buffer = DEFAULT_SAFETY_BUFFER_MINUTES
        evidence["safety_buffer_minutes"] = FieldEvidence(SourceKind.SYSTEM_DEFAULT, "默认安全缓冲 10 分钟")
    else:
        evidence["safety_buffer_minutes"] = evidence.pop("user_buffer_minutes")
    return WindowConstraints(duration, ends, buffer, evidence, needs)


def _parse_questions(raw: object, current: CurrentContext, commitments: Tuple[FixedCommitment, ...], constraints: WindowConstraints) -> Tuple[WindowClarificationQuestion, ...]:
    if not isinstance(raw, list) or len(raw) > MAX_WINDOW_QUESTIONS: _bad("validation_error", "确认问题无效。")
    targets = {"current_context": set(current.needs_confirmation), "window_constraints": set(constraints.needs_confirmation)}
    targets.update({item.commitment_ref: set(item.needs_confirmation) for item in commitments})
    result, seen = [], set()
    for raw_item in raw:
        item = _exact(raw_item, QUESTION)
        qid = _text(item["question_id"], MAX_WINDOW_REF_LENGTH, "question_id 无效。")
        target, field = _text(item["target_ref"], MAX_WINDOW_REF_LENGTH, "target_ref 无效。"), _text(item["field_name"], MAX_WINDOW_REF_LENGTH, "field_name 无效。")
        if qid in seen or target not in targets or field not in targets[target]: _bad("validation_error", "确认问题引用无效。")
        seen.add(qid)
        if type(item["blocking"]) is not bool or not isinstance(item["quick_options"], list) or len(item["quick_options"]) > 5: _bad("validation_error", "确认问题无效。")
        options = tuple(_text(x, 80, "快捷选项无效。") for x in item["quick_options"])
        result.append(WindowClarificationQuestion(qid, target, field, _text(item["question"], 200, "问题无效。"), item["blocking"], options))
    return tuple(result)


def parse_window_context(raw_reply: object, user_text: object, reference_datetime: object) -> P1WindowParseResult:
    if not isinstance(user_text, str) or not user_text.strip() or len(user_text) > MAX_WINDOW_TEXT_LENGTH or not isinstance(reference_datetime, datetime):
        return _reject("validation_error", "输入上下文无效。")
    if not isinstance(raw_reply, str) or len(raw_reply) > MAX_WINDOW_OUTPUT_LENGTH or not raw_reply.strip(): return _reject("format_error", "模型返回格式无效。")
    text = raw_reply.strip()
    if "```" in text or not text.startswith("{") or not text.endswith("}"): return _reject("format_error", "模型返回必须是纯 JSON 对象。")
    try: payload = json.loads(text, object_pairs_hook=_pairs, parse_constant=lambda x: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, RecursionError): return _reject("format_error", "模型返回不是合法 JSON。")
    try:
        top = _exact(payload, TOP)
        if top["schema_version"] != WINDOW_SCHEMA_VERSION: _bad("validation_error", "schema_version 无效。")
        current = _parse_current(top["current_context"], user_text, reference_datetime)
        if not isinstance(top["commitments"], list) or len(top["commitments"]) > MAX_COMMITMENTS: _bad("validation_error", "固定安排数量无效。")
        commitments = tuple(_parse_commitment(x, user_text) for x in top["commitments"])
        if len({x.commitment_ref for x in commitments}) != len(commitments): _bad("validation_error", "固定安排引用重复。")
        if any(x.starts_at is not None and x.starts_at < current.current_datetime for x in commitments): _bad("validation_error", "固定安排早于当前时间。")
        constraints = _parse_constraints(top["window_constraints"])
        if constraints.ends_at is not None and constraints.ends_at < current.current_datetime: _bad("validation_error", "窗口结束早于当前时间。")
        if not commitments and constraints.free_duration_minutes is None and constraints.ends_at is None and not ({"free_duration_minutes", "ends_at"} & set(constraints.needs_confirmation)):
            _bad("validation_error", "没有时间边界时必须要求补充边界。")
        questions = _parse_questions(top["clarification_questions"], current, commitments, constraints)
    except _Error as exc: return _reject(exc.kind, exc.message)
    return P1WindowParseResult("ok", None, "窗口上下文解析成功。", WindowContextDocument(WINDOW_SCHEMA_VERSION, user_text, current, commitments, constraints, questions))


P1WindowParser = type("P1WindowParser", (), {"parse": staticmethod(parse_window_context)})
