"""Last-mile, state-aware filtering for user-visible clarification questions."""

import re


_SPACE_PUNCT_RE = re.compile(r"[\s，。！？?：:、]+")
_INTENT_PATTERNS = (
    ("destination", re.compile(r"(去哪里|目的地|哪个教学楼|哪个楼|去哪个|在哪栋|在哪个地点)")),
    ("current_location", re.compile(r"(现在(?:大概)?在哪里|当前位置|当前所在|你在哪|你现在是在)")),
    ("mode", re.compile(r"(怎么过去|交通方式|怎么去|如何过去)")),
    ("starts_at", re.compile(r"(几点(开始|上课)|什么时候(开始|上课)|上课时间)")),
    ("ends_at", re.compile(r"(几点结束|什么时候结束)")),
)


def _intent(question):
    for name, pattern in _INTENT_PATTERNS:
        if pattern.search(question):
            return name
    return None


def _known_fields(state, movement_blocks):
    known = set()
    commitments = tuple(getattr(state, "commitments", ()) or ())
    if any(getattr(item, "location_text", None) for item in commitments):
        known.add("destination")
    if any(getattr(item, "starts_at", None) is not None for item in commitments):
        known.add("starts_at")
    if movement_blocks:
        known.update(("destination", "current_location", "mode"))
    return known


def _has_unknown_end(state):
    return any(
        getattr(item, "starts_at", None) is not None and getattr(item, "ends_at", None) is None
        for item in tuple(getattr(state, "commitments", ()) or ())
    )


def named_question_commitments(question, state):
    """Only explicit existing titles, never an inferred/new commitment."""
    return tuple(item for item in getattr(state, 'commitments', ())
                 if getattr(item, 'title', None) and item.title in question)


def filter_pending_questions(questions, state, movement_blocks=(), execution_context=None):
    """Remove answered questions and duplicate/same-intent candidates.

    Upstream agents currently emit text-only questions, so classification is
    intentionally limited to the five structured facts they can ask about.
    """
    known = _known_fields(state, tuple(movement_blocks or ()))
    if execution_context is not None:
        # A future leg proves its origin at departure, not the user's location now.
        known.discard('current_location')
        if execution_context.current_location.source.value == 'user':
            known.add('current_location')
    result = []
    seen = set()
    seen_intents = set()
    for raw in questions or ():
        if not isinstance(raw, str) or not raw.strip():
            continue
        question = raw.strip()
        intent = _intent(question)
        if intent == 'destination':
            named = named_question_commitments(question, state)
            if named:
                if all(item.location_text for item in named):
                    continue
                # A different known venue or future route cannot answer this
                # specifically targeted missing commitment location.
                known.discard('destination')
        if intent in ('starts_at', 'ends_at'):
            named = named_question_commitments(question, state)
            if named and all(getattr(item, intent) is not None for item in named):
                # Another commitment may still lack this field. That cannot
                # make an explicitly answered question about this one recur.
                continue
        if intent in known:
            continue
        if intent == "ends_at" and not _has_unknown_end(state):
            continue
        normalized = _SPACE_PUNCT_RE.sub("", question)
        dedupe_key = (intent, normalized) if intent is None else (intent, "")
        if dedupe_key in seen or (intent is not None and intent in seen_intents):
            continue
        seen.add(dedupe_key)
        if intent is not None:
            seen_intents.add(intent)
        result.append(question)
    return tuple(result)
