"""Bounded, observable Qwen calls for the P5 intelligence layer.

The trace deliberately stores metadata only.  Prompts, raw model output and
credentials never become session diagnostics or user-facing content.
Python 3.8 compatible.
"""

from dataclasses import dataclass
from time import monotonic
from typing import Callable, Optional, Tuple


# A normal P5 pass uses seven model calls (eight when clarification matters).
# At most two format repairs may be spent across the whole decision trace.
# Existing intake/feedback semantic calls are outside this trace, so this cap
# keeps the end-to-end extreme path within the documented 11–12-call budget.
MAX_AGENT_CALLS_PER_TRACE = 9


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
    parsed, current_trace = _one_call(
        caller, system_prompt, user_prompt, parser, stage, purpose,
        current_trace, repair=False,
    )
    if parsed is not None or max_repairs == 0:
        if parsed is None:
            current_trace = current_trace.append(
                AgentCallRecord(stage, purpose, False, fallback=True)
            )
        return parsed, current_trace
    if current_trace.call_count >= max_total_calls:
        return None, current_trace.append(
            AgentCallRecord(stage, purpose, False, fallback=True)
        )
    repair_system = repair_system_prompt or (
        "你是结构化输出修复器。只输出该阶段要求的完整 JSON，不解释，也不增加新事实。"
    )
    parsed, current_trace = _one_call(
        repair_caller, repair_system, user_prompt, parser, stage, purpose,
        current_trace, repair=True,
    )
    if parsed is None:
        current_trace = current_trace.append(
            AgentCallRecord(stage, purpose, False, fallback=True)
        )
    return parsed, current_trace


def _one_call(caller, system, user, parser, stage, purpose, trace, repair):
    started = monotonic()
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
        return parsed, trace.append(record)
    except Exception:  # semantic/output failures are bounded stage failures
        record = AgentCallRecord(
            stage=stage,
            purpose=purpose,
            success=False,
            repair=repair,
            latency_ms=max(0, int((monotonic() - started) * 1000)),
            model_alias=_model_alias(caller),
        )
        return None, trace.append(record)


def _token_usage(raw):
    usage = getattr(raw, "usage", None)
    if usage is None:
        return None
    value = getattr(usage, "total_tokens", None)
    return value if isinstance(value, int) and value >= 0 else None


def _model_alias(caller):
    value = getattr(caller, "model_alias", None) or getattr(caller, "model", None)
    return value if isinstance(value, str) and value.strip() else None
