#!/usr/bin/env python3
"""Manual validation of the natural-language parser against the real TJU LLM service."""

import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.natural_language_parser import parse_natural_language


def main() -> int:
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_text = (
        "我现在在宿舍，最后要到教学楼。必须先去图书馆还书，预计停留5分钟，15:00前完成；"
        "如果来得及再去食堂吃饭，预计停留30分钟。"
    )

    result = parse_natural_language(user_text, current_time)
    print(f"status={result.status}")
    print(f"error_type={result.error_type}")
    print(f"message={result.message}")
    print(f"missing_fields={result.missing_fields}")
    print(f"questions={result.questions}")
    if result.planning_request is not None:
        print(f"planning_request={result.planning_request}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
