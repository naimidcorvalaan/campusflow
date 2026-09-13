"""P1b 严格解析的安全结果对象。"""
from dataclasses import dataclass
from typing import Optional, Tuple

from src.p1_window_models import WindowContextDocument, WindowClarificationQuestion


@dataclass(frozen=True)
class P1WindowParseResult:
    status: str
    error_type: Optional[str]
    message: str
    document: Optional[WindowContextDocument] = None

    @property
    def questions(self) -> Tuple[WindowClarificationQuestion, ...]:
        return () if self.document is None else self.document.clarification_questions
