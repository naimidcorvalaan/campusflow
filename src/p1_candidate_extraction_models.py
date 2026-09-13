from dataclasses import dataclass
from typing import Optional
from src.p1_candidate_models import CandidateDocument


@dataclass(frozen=True)
class P1CandidateParseResult:
    status: str
    error_type: Optional[str]
    message: str
    document: Optional[CandidateDocument] = None
