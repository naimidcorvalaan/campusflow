"""Qwen-heavy initial event semantics; every model response here is mocked."""

import json
from datetime import datetime

import pytest

from src.p2_day_intake import apply_day_intake, parse_day_intake, run_day_intake
from src.p4_event_semantics import (
    EVENT_SEMANTIC_SCHEMA_VERSION,
    RAW_EVENT_SCHEMA_VERSION,
    materialize_day_intake,
    parse_event_semantic_graph,
    parse_raw_event_extraction,
)
from src.p4_intake_auditor import INTAKE_AUDIT_SCHEMA_VERSION


REFERENCE = datetime(2026, 9, 1, 13, 20)


def _event(event_id, event_type, title, order, start=None, duration=None, location=None, kind=None):
    return {
        "local_event_id": event_id,
        "event_type": event_type,
        "title": title,
        "order_in_utterance": order,
        "starts_at": start,
        "ends_at": None,
        "explicit_duration_minutes": duration,
        "location_text": location,
        "commitment_kind": kind,
        "raw_evidence": title,
    }


def _raw(events):
    return {
        "schema_version": RAW_EVENT_SCHEMA_VERSION,
        "day_end": "23:00",
        "current_location": None,
        "transport_mode": None,
        "events": events,
        "questions": [],
    }


def _graph(*, durations=(), relations=(), periods=(), sequence=(), profiles=(), conflicts=()):
    return {
        "schema_version": EVENT_SEMANTIC_SCHEMA_VERSION,
        "duration_of": [{"event_id": event_id, "minutes": minutes} for event_id, minutes in durations],
        "commitment_relations": [
            {"event_id": event_id, "commitment_event_id": commitment_id, "relation": relation}
            for event_id, commitment_id, relation in relations
        ],
        "meal_period_by_event": [
            {"event_id": event_id, "meal_period": period} for event_id, period in periods
        ],
        "explicit_sequence": [
            {"before_event_id": before_id, "after_event_id": after_id}
            for before_id, after_id in sequence
        ],
        "execution_profiles": [
            {"event_id": event_id, "splittable": splittable,
             "minimum_chunk_minutes": minimum, "preferred_chunk_minutes": preferred,
             "requires_single_session": single, "source": source}
            for event_id, splittable, minimum, preferred, single, source in profiles
        ],
        "conflicts": list(conflicts),
    }


def _complete_raw():
    return _raw([
        _event("task_homework", "task", "写作业", 1, location="图书馆"),
        _event("meal_dinner", "meal", "吃晚饭", 2),
        _event("course", "fixed_commitment", "上课", 3, start="19:00", location="9教", kind="class"),
        _event("task_vocab", "task", "背单词", 4),
        _event("task_laundry", "task", "洗衣服", 5),
    ])


def _complete_graph():
    return _graph(
        durations=(("course", 90), ("task_vocab", 30), ("task_laundry", 20)),
        relations=(
            ("meal_dinner", "course", "before"),
            ("task_vocab", "course", "after"),
            ("task_laundry", "course", "after"),
        ),
        periods=(("meal_dinner", "dinner"),),
    )


def test_linked_graph_materializes_real_full_sentence_facts_without_time_guessing():
    raw = parse_raw_event_extraction(json.dumps(_complete_raw(), ensure_ascii=False))
    graph = parse_event_semantic_graph(json.dumps(_complete_graph(), ensure_ascii=False), raw)
    proposal = materialize_day_intake(raw, graph)
    applied = apply_day_intake(REFERENCE, proposal)

    assert [item.title for item in proposal.tasks] == ["写作业", "吃晚饭", "背单词", "洗衣服"]
    assert proposal.tasks[1].meal_period == "dinner"
    assert proposal.tasks[1].meal_before_commitment_index == 1
    assert [item.after_commitment_index for item in proposal.tasks[2:]] == [1, 1]
    commitment = applied.state.commitments[0]
    assert commitment.starts_at.strftime("%H:%M") == "19:00"
    assert commitment.ends_at.strftime("%H:%M") == "20:30"


@pytest.mark.parametrize(
    "title,start,minutes,expected_end",
    [
        ("上课", "19:00", 90, "20:30"),
        ("开会", "15:00", 120, "17:00"),
        ("训练", "19:00", 90, "20:30"),
        ("考试", "10:00", 120, "12:00"),
    ],
)
def test_duration_of_is_semantically_linked_then_program_calculates_end(title, start, minutes, expected_end):
    raw = parse_raw_event_extraction(json.dumps(_raw([
        _event("event", "fixed_commitment", title, 1, start=start, kind="other"),
    ]), ensure_ascii=False))
    graph = parse_event_semantic_graph(json.dumps(_graph(durations=(("event", minutes),)), ensure_ascii=False), raw)
    applied = apply_day_intake(REFERENCE, materialize_day_intake(raw, graph))
    assert applied.state.commitments[0].ends_at.strftime("%H:%M") == expected_end


def test_multiple_commitments_bind_after_relations_to_the_named_event_not_an_index_guess():
    raw = parse_raw_event_extraction(json.dumps(_raw([
        _event("meeting", "fixed_commitment", "开会", 1, start="15:00", kind="meeting"),
        _event("library", "task", "去图书馆", 2),
        _event("course", "fixed_commitment", "上课", 3, start="19:00", kind="class"),
        _event("laundry", "task", "洗衣服", 4),
    ]), ensure_ascii=False))
    graph = parse_event_semantic_graph(json.dumps(_graph(
        durations=(("meeting", 60), ("course", 90)),
        relations=(("library", "meeting", "after"), ("laundry", "course", "after")),
    ), ensure_ascii=False), raw)
    proposal = materialize_day_intake(raw, graph)
    assert [item.after_commitment_index for item in proposal.tasks] == [1, 2]


def test_graph_rejects_duration_attached_to_an_unknown_event():
    raw = parse_raw_event_extraction(json.dumps(_raw([_event("course", "fixed_commitment", "上课", 1, start="19:00")]), ensure_ascii=False))
    with pytest.raises(Exception):
        parse_event_semantic_graph(json.dumps(_graph(durations=(("vocab", 90),)), ensure_ascii=False), raw)


def test_semantic_sequence_is_applied_with_raw_order_as_a_deterministic_tiebreaker():
    raw = parse_raw_event_extraction(json.dumps(_raw([
        _event("vocab", "task", "背单词", 1),
        _event("homework", "task", "写作业", 2),
    ]), ensure_ascii=False))
    graph = parse_event_semantic_graph(json.dumps(_graph(sequence=(("homework", "vocab"),)), ensure_ascii=False), raw)
    proposal = materialize_day_intake(raw, graph)
    assert [item.title for item in proposal.tasks] == ["写作业", "背单词"]


def test_raw_event_boundary_preserves_explicit_current_location_without_making_it_a_task_location():
    payload = _raw([_event("study", "task", "写报告", 1, location="图书馆")])
    payload["current_location"] = "31斋"
    payload["transport_mode"] = "bike"
    raw = parse_raw_event_extraction(json.dumps(payload, ensure_ascii=False))
    proposal = materialize_day_intake(raw, parse_event_semantic_graph(
        json.dumps(_graph(), ensure_ascii=False), raw,
    ))
    assert proposal.current_location == "31斋"
    assert proposal.transport_mode == "bike"
    assert proposal.tasks[0].location_text == "图书馆"


def test_semantic_execution_profile_reaches_intake_without_title_keyword_rules():
    raw = parse_raw_event_extraction(json.dumps(_raw([
        _event("work", "task", "资料整理", 1),
        _event("exam", "task", "一套卷子", 2),
    ]), ensure_ascii=False))
    graph = parse_event_semantic_graph(json.dumps(_graph(profiles=(
        ("work", True, 15, 30, False, "qwen_semantic"),
        ("exam", False, 60, 60, True, "user_explicit"),
    )), ensure_ascii=False), raw)
    proposal = materialize_day_intake(raw, graph)
    assert proposal.tasks[0].is_splittable is True
    assert proposal.tasks[0].preferred_chunk_minutes == 30
    assert proposal.tasks[1].requires_single_session is True
    applied = apply_day_intake(REFERENCE, proposal)
    assert applied.state.tasks[0].is_splittable is True
    assert applied.state.tasks[0].minimum_slice_minutes == 15
    assert applied.state.tasks[1].is_splittable is False


class _ThreePassCaller:
    def __init__(self, raw, graph, audit):
        self.raw = json.dumps(raw, ensure_ascii=False)
        self.graph = json.dumps(graph, ensure_ascii=False)
        self.audit = audit
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        if "Semantic Linker" in system:
            return self.graph
        if "Raw Event Extractor" in system:
            return self.raw
        if "Initial Intake Semantic Auditor" in system:
            return self.audit
        raise AssertionError("unexpected model role")


def test_run_day_intake_uses_raw_linker_auditor_then_strict_program_apply():
    caller = _ThreePassCaller(
        _complete_raw(), _complete_graph(), json.dumps({
            "schema_version": INTAKE_AUDIT_SCHEMA_VERSION,
            "decision": "approve", "issues": [], "repaired_proposal": None,
        }),
    )
    outcome = run_day_intake(
        REFERENCE,
        "我下午想去图书馆写一会儿作业，然后吃晚饭，晚上19:00去9教上课，上一小时半。下课以后再背半小时单词，还要洗20分钟衣服。",
        caller,
        raw_event_caller=caller,
        semantic_linker_caller=caller,
        semantic_auditor_caller=caller,
        campus_id="weijinlu",
    )
    assert outcome.applied is not None
    assert outcome.call_count == 3
    assert outcome.applied.state.commitments[0].ends_at.strftime("%H:%M") == "20:30"
    assert len(caller.calls) == 3


def _repaired_audit(proposal, issues):
    from src.p4_intake_auditor import _proposal_payload
    return json.dumps({
        "schema_version": INTAKE_AUDIT_SCHEMA_VERSION,
        "decision": "repair",
        "issues": list(issues),
        "repaired_proposal": _proposal_payload(proposal),
    }, ensure_ascii=False)


def test_auditor_repairs_raw_event_extraction_that_omitted_explicit_laundry():
    raw = _raw(_complete_raw()["events"][:-1])
    graph = _complete_graph()
    graph["duration_of"] = graph["duration_of"][:-1]
    graph["commitment_relations"] = graph["commitment_relations"][:-1]
    repaired = materialize_day_intake(
        parse_raw_event_extraction(json.dumps(_complete_raw(), ensure_ascii=False)),
        parse_event_semantic_graph(json.dumps(_complete_graph(), ensure_ascii=False),
                                   parse_raw_event_extraction(json.dumps(_complete_raw(), ensure_ascii=False))),
    )
    caller = _ThreePassCaller(raw, graph, _repaired_audit(repaired, ("漏掉洗衣服20分钟",)))
    outcome = run_day_intake(
        REFERENCE, "19点上课上一小时半，下课后背半小时单词，还要洗20分钟衣服",
        caller, raw_event_caller=caller, semantic_linker_caller=caller,
        semantic_auditor_caller=caller, campus_id="weijinlu",
    )
    assert outcome.semantic_repair_used
    assert [task.title for task in outcome.proposal.tasks][-1] == "洗衣服"
    assert outcome.applied.state.tasks[-1].total_minutes == 20


def test_auditor_repairs_linker_duration_attached_to_the_wrong_event():
    raw = _complete_raw()
    wrong_graph = _complete_graph()
    wrong_graph["duration_of"] = [
        {"event_id": "task_vocab", "minutes": 90},
        {"event_id": "task_laundry", "minutes": 20},
    ]
    corrected_raw = parse_raw_event_extraction(json.dumps(raw, ensure_ascii=False))
    corrected_graph = parse_event_semantic_graph(json.dumps(_complete_graph(), ensure_ascii=False), corrected_raw)
    repaired = materialize_day_intake(corrected_raw, corrected_graph)
    caller = _ThreePassCaller(
        raw, wrong_graph,
        _repaired_audit(repaired, ("90分钟应绑定上课，而不是背单词",)),
    )
    outcome = run_day_intake(
        REFERENCE, "晚上19点去9教上课，上一小时半，下课后背半小时单词，还要洗20分钟衣服",
        caller, raw_event_caller=caller, semantic_linker_caller=caller,
        semantic_auditor_caller=caller, campus_id="weijinlu",
    )
    assert outcome.semantic_repair_used
    assert outcome.applied.state.commitments[0].ends_at.strftime("%H:%M") == "20:30"
    assert outcome.applied.state.tasks[2].total_minutes == 30
