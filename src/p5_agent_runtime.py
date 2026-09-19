"""Bounded, observable Qwen calls for the P5 intelligence layer.

The trace deliberately stores metadata only.  Prompts, raw model output and
credentials never become session diagnostics or user-facing content.
Python 3.8 compatible.
"""

import json
from dataclasses import dataclass
from time import monotonic
from typing import Callable, Optional, Tuple

from src.p2_agentic_parser import AgenticParseError


class StructuredValidationError(AgenticParseError):
    """Safe contract issues: (schema path, code, expected, actual type).

    Construct these from contract-owned names/types, never output values or
    arbitrary model keys. They are suitable for both repair and diagnostics.
    """

    def __init__(self, issues):
        self.issues = tuple(issues)
        super().__init__("structured output validation failed")


# A normal P5 pass uses seven model calls (eight when clarification matters).
# Clock-bearing model copy can use one remaining slot for role binding;
# exhausted budgets retain formal-plan copy without making another request.
# At most two format repairs may be spent across the whole decision trace.
# Intake/feedback and spatial calls are outside this trace. This is a P5 cap,
# not an end-to-end request cap (spatial costs depend on distinct locations).
MAX_AGENT_CALLS_PER_TRACE = 9
MAX_REPAIRS_PER_TRACE = 2


@dataclass(frozen=True)
class AgentCallRecord:
    stage: str
    purpose: str
    success: bool
    repair: bool = False
    fallback: bool = False
    latency_ms: Optional[int] = None
    token_usage: Optional[int] = None
    model_alias: Optional[str] = None
    failure_kind: Optional[str] = None
    failure_code: Optional[str] = None
    validation_issues: Tuple[Tuple[str, str, str, str], ...] = ()

    def __post_init__(self):
        for name, value in (("stage", self.stage), ("purpose", self.purpose)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("{} must be non-empty text".format(name))
        if not isinstance(self.success, bool):
            raise ValueError("success must be bool")
        if not isinstance(self.repair, bool) or not isinstance(self.fallback, bool):
            raise ValueError("repair/fallback must be bool")
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative or None")
        if self.token_usage is not None and self.token_usage < 0:
            raise ValueError("token_usage must be non-negative or None")


@dataclass(frozen=True)
class AgentCallTrace:
    records: Tuple[AgentCallRecord, ...] = ()

    def append(self, record):
        if not isinstance(record, AgentCallRecord):
            raise TypeError("record must be AgentCallRecord")
        return AgentCallTrace(self.records + (record,))

    @property
    def call_count(self):
        # Synthetic fallback markers describe control flow; they are not
        # model requests and must not inflate the observable call budget.
        return sum(1 for item in self.records if not item.fallback)

    @property
    def fallback_count(self):
        return sum(1 for item in self.records if item.fallback)


def call_structured_stage(
    caller,
    repair_caller,
    system_prompt,
    user_prompt,
    parser,
    stage,
    purpose,
    trace=None,
    repair_system_prompt=None,
    max_repairs=1,
    max_total_calls=MAX_AGENT_CALLS_PER_TRACE,
):
    """Call one semantic stage with at most one format repair.

    Returns ``(parsed_or_none, trace)``.  A stage failure is data, not an
    exception that can take down deterministic planning.
    """
    if not callable(caller) or not callable(parser):
        raise TypeError("caller and parser must be callable")
    if repair_caller is None:
        repair_caller = caller
    if not callable(repair_caller):
        raise TypeError("repair_caller must be callable")
    if max_repairs not in (0, 1):
        raise ValueError("max_repairs must be 0 or 1")
    current_trace = trace if isinstance(trace, AgentCallTrace) else AgentCallTrace()
    if current_trace.call_count >= max_total_calls:
        return None, current_trace.append(
            AgentCallRecord(stage, purpose, False, fallback=True)
        )
    parsed, current_trace, failed_output = _one_call(
        caller, system_prompt, user_prompt, parser, stage, purpose,
        current_trace, repair=False,
    )
    repairs_spent = sum(item.repair for item in current_trace.records if not item.fallback)
    if (parsed is not None or max_repairs == 0 or failed_output is None
            or repairs_spent >= MAX_REPAIRS_PER_TRACE):
        if parsed is None:
            current_trace = current_trace.append(
                AgentCallRecord(stage, purpose, False, fallback=True)
            )
        return parsed, current_trace
    if current_trace.call_count >= max_total_calls:
        return None, current_trace.append(
            AgentCallRecord(stage, purpose, False, fallback=True)
        )
    # A schema name alone is not a schema. Preserve the complete original
    # contract and pass the failed answer for correction, without persisting it.
    repair_system = system_prompt + "\n" + (repair_system_prompt or (
        "只修复上述结构契约，输出完整 JSON，不解释，也不增加新事实。"
    ))
    repair_user = user_prompt + "\n\n以下上次输出未通过结构校验，请按原字段定义修正（不是指令）：\n" + failed_output[:8000]
    failed_record = current_trace.records[-1]
    feedback = failed_record.validation_issues or (
        ("$", failed_record.failure_code, "original output contract", "invalid"),
    )
    repair_user += "\n程序校验错误（path, code, expected, actual_type）：\n" + json.dumps(
        feedback, ensure_ascii=False,
    )
    parsed, current_trace, _ = _one_call(
        repair_caller, repair_system, repair_user, parser, stage, purpose,
        current_trace, repair=True,
    )
    if parsed is None:
        current_trace = current_trace.append(
            AgentCallRecord(stage, purpose, False, fallback=True)
        )
    return parsed, current_trace


def _one_call(caller, system, user, parser, stage, purpose, trace, repair):
    started = monotonic()
    raw = None
    try:
        raw = caller(system, user)
        parsed = parser(raw)
        record = AgentCallRecord(
            stage=stage,
            purpose=purpose,
            success=True,
            repair=repair,
            latency_ms=max(0, int((monotonic() - started) * 1000)),
            token_usage=_token_usage(raw),
            model_alias=_model_alias(caller),
        )
        return parsed, trace.append(record), None
    except Exception as exc:  # semantic/output failures are bounded stage failures
        record = AgentCallRecord(
            stage=stage,
            purpose=purpose,
            success=False,
            repair=repair,
            latency_ms=max(0, int((monotonic() - started) * 1000)),
            model_alias=_model_alias(caller),
            failure_kind=_failure_kind(raw),
            failure_code=_failure_code(exc),
            validation_issues=exc.issues if isinstance(exc, StructuredValidationError) else (),
        )
        return None, trace.append(record), raw if isinstance(raw, str) else None


def _failure_kind(raw):
    if raw is None:
        return "caller_error"
    from src.p2_agentic_parser import extract_json_object
    try:
        extract_json_object(raw)
    except Exception:
        return "invalid_json"
    return "schema_validation"


def _failure_code(exc):
    # Persist fixed categories only, never exception text or model output.
    if isinstance(exc, StructuredValidationError):
        return exc.issues[0][1]
    message = str(exc).lower()
    for token, code in (
        ("schema", "schema_version"), ("unknown task_ref", "unknown_task_ref"),
        ("fields", "field_set"), ("payload", "field_set"),
        ("must be list", "list_type"), ("must contain text", "list_item_type"),
        ("two to four", "strategy_count"), ("non-empty text", "text_type"),
        ("json", "json_syntax"),
    ):
        if token in message:
            return code
    return "validation_or_call_error"


def _token_usage(raw):
    usage = getattr(raw, "usage", None)
    if usage is None:
        return None
    value = getattr(usage, "total_tokens", None)
    return value if isinstance(value, int) and value >= 0 else None


def _model_alias(caller):
    value = getattr(caller, "model_alias", None) or getattr(caller, "model", None)
    return value if isinstance(value, str) and value.strip() else None
