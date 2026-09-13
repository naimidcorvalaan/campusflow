"""P1 任务理解严格解析的结果模型。"""

from dataclasses import dataclass
from typing import Optional, Tuple

from src.p1_models import (
    ClarificationQuestion,
    TaskUnderstanding,
    TaskUnderstandingDocument,
)


@dataclass(frozen=True)
class P1TaskUnderstandingParseResult:
    status: str
    error_type: Optional[str]
    message: str
    document: Optional[TaskUnderstandingDocument] = None

    @property
    def tasks(self) -> Tuple[TaskUnderstanding, ...]:
        if self.document is None:
            return ()
        return self.document.tasks

    @property
    def questions(self) -> Tuple[ClarificationQuestion, ...]:
        if self.document is None:
            return ()
        return self.document.clarification_questions


# 简短别名，便于调用方在只处理 P1 解析结果时使用。
P1ParseResult = P1TaskUnderstandingParseResult
