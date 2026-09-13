#!/usr/bin/env python3
"""Manual connectivity check for the TJU LLM OpenAI-compatible endpoint."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tju_llm_client import TJUClientError, call_tju_llm


def main() -> int:
    try:
        response = call_tju_llm("只回复：连接成功")
        print(response)
        return 0
    except TJUClientError as exc:
        print(f"安全错误: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
