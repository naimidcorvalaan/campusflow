"""Safe, value-free diagnostics for a single P1 planning operation."""
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class DiagnosticStage(str, Enum):
    TASK_EXTRACTION = "task_extraction"
    WINDOW_EXTRACTION = "window_extraction"
    CANDIDATE_GENERATION = "candidate_generation"
    AGENT_GENERATOR = "agent_generator"
    AGENT_RESCUE_GENERATOR = "agent_rescue_generator"
    AGENT_DIRECT_ANSWER = "agent_direct_answer"
    AGENT_REVIEWER = "agent_reviewer"
    AGENT_REVISER = "agent_reviser"
    AGENT_PRESENTER = "agent_presenter"
    FINAL_VIEW = "final_view"


class DiagnosticStatus(str, Enum):
    NOT_STARTED = "not_started"
    SUCCESS = "success"
    CALL_FAILED = "call_failed"
    EMPTY_OUTPUT = "empty_output"
    PARSE_FAILED = "parse_failed"
    NOT_ACTIONABLE = "not_actionable"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INTERNAL_ERROR = "internal_error"
    FALLBACK_STARTED = "fallback_started"
    FALLBACK_COMPLETED = "fallback_completed"


class SafeErrorCategory(str, Enum):
    AUTHENTICATION = "authentication"
    NETWORK = "network"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    INVALID_RESPONSE = "invalid_response"
    INTERNAL = "internal"


@dataclass(frozen=True)
class SafeDiagnostic:
    stage: DiagnosticStage
    status: DiagnosticStatus
    attempts: int
    call_count: int
    error_category: Optional[SafeErrorCategory] = None


def safe_error_category(exc):
    """Class-name-only classification; never retain exception text or repr."""
    name = type(exc).__name__.casefold()
    if "auth" in name or "permission" in name:
        return SafeErrorCategory.AUTHENTICATION
    if "timeout" in name:
        return SafeErrorCategory.TIMEOUT
    if "rate" in name or "throttle" in name:
        return SafeErrorCategory.RATE_LIMIT
    if "connection" in name or "network" in name or "socket" in name:
        return SafeErrorCategory.NETWORK
    if "server" in name or "http" in name:
        return SafeErrorCategory.SERVER
    return SafeErrorCategory.INTERNAL


def diagnostics_with(records, *more):
    return tuple(records) + tuple(more)
