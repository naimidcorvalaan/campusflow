"""P4c initial-intake semantic completeness audit (all callers are mocks)."""

import json
from datetime import datetime

import pytest

from src.p2_day_intake import run_day_intake
from src.p4_intake_auditor import (
    INTAKE_AUDIT_SCHEMA_VERSION,
    audit_initial_intake,
    build_initial_intake_audit_prompt,
)


REFERENCE = datetime(2026, 9, 3, 18, 0)


def _commitment(title, start, *, relative=None, end=None, duration=None, location=None, kind="other"):
    return {
        "title": title,
        "starts_at": start,
        "ends_at": end,
        "starts_in_minutes": None,
        "duration_minutes": duration,
        "relative_end_minutes": relative,
        "location_text": location,
        "commitment_kind": kind,
        "class_arrival_lead_minutes": None,
    }


def _task(title, *, minutes=None, location=None, kind="generic", **extra):
    result = {
        "title": title,
        "total_minutes": minutes,
        "is_splittable": None,
        "minimum_slice_minutes": None,
        "location_text": location,
        "activity_kind": kind,
        "duration_source": "user_explicit" if minutes is not None else None,
    }
    result.update(extra)
    return result


def _proposal(commitments, tasks, day_end="23:00"):
    return {
        "schema_version": "p2.day-intake.v1",
        "day_end": day_end,
        "commitments": commitments,
        "tasks": tasks,
        "questions": [],
        "current_location": None,
        "transport_mode": None,
    }


def _audit(decision="approve", repaired=None, issues=()):
    return json.dumps({
        "schema_version": INTAKE_AUDIT_SCHEMA_VERSION,
        "decision": decision,
        "issues": list(issues),
        "repaired_proposal": repaired,
    }, ensure_ascii=False)


class RoleCaller:
    def __init__(self, extraction, audit):
        self.extraction = extraction
        self.audit = audit
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        if "Initial Intake Semantic Auditor" in system:
            return self.audit
        return self.extraction


class FormatRepairThenAuditCaller:
    def __init__(self, malformed_extraction, format_repair, audit):
        self.malformed_extraction = malformed_extraction
        self.format_repair = format_repair
        self.audit = audit
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        if "Initial Intake Semantic Auditor" in system:
            return self.audit
        if user.startswith('{') and json.loads(user).get('request_stage') == 'day_intake_repair':
            return self.format_repair
        return self.malformed_extraction


@pytest.mark.parametrize(
    "user_text,title,start,relative,expected_end",
    [
        ("21点上课，一个半小时后下课", "上课", "21:00", 90, (22, 30)),
        ("下午3点开会，两小时后结束", "开会", "15:00", 120, (17, 0)),
        ("晚上7点训练，90分钟后结束", "训练", "19:00", 90, (20, 30)),
    ],
)
def test_relative_end_is_bound_by_model_and_added_by_program(
    user_text, title, start, relative, expected_end
):
    kind = "class" if title == "上课" else "other"
    extracted = _proposal([_commitment(title, start, relative=relative, kind=kind)], [])
    caller = RoleCaller(json.dumps(extracted, ensure_ascii=False), _audit())
    outcome = run_day_intake(
        REFERENCE,
        user_text,
        caller,
        semantic_auditor_caller=caller,
        campus_id="weijinlu",
    )
    commitment = outcome.applied.state.commitments[0]
    assert (commitment.ends_at.hour, commitment.ends_at.minute) == expected_end
    assert outcome.proposal.commitments[0].relative_end_minutes == relative
    assert outcome.call_count == 2
    assert outcome.semantic_audit_used


def test_auditor_repairs_missing_laundry_in_full_real_input():
    raw = (
        "我想在图书馆写一会儿实验报告，然后吃饭，今天21:00去9教上课，"
        "一个半小时后下课。然后写一个小时作业，还想背单词，还需要洗20分钟衣服"
    )
    pass_a = _proposal(
        [_commitment("上课", "21:00", relative=90, location="9教", kind="class")],
        [
            _task("写实验报告", location="图书馆"),
            _task("吃饭", kind="meal"),
            _task("写作业", minutes=60),
            _task("背单词"),
        ],
    )
    repaired = _proposal(
        pass_a["commitments"],
        pass_a["tasks"] + [_task("洗衣服", minutes=20)],
    )
    caller = RoleCaller(
        json.dumps(pass_a, ensure_ascii=False),
        _audit("repair", repaired, ("漏掉用户明确提出的洗衣服20分钟",)),
    )
    outcome = run_day_intake(
        REFERENCE, raw, caller, semantic_auditor_caller=caller, campus_id="weijinlu"
    )
    assert outcome.applied is not None
    assert outcome.semantic_repair_used
    assert [task.title for task in outcome.applied.state.tasks] == [
        "写实验报告", "吃饭", "写作业", "背单词", "洗衣服",
    ]
    assert outcome.applied.state.tasks[-1].total_minutes == 20
    assert outcome.applied.state.commitments[0].ends_at.hour == 22
    assert outcome.applied.state.commitments[0].ends_at.minute == 30


def test_auditor_repairs_meal_before_class_without_marking_preclass_prefix_after_class():
    raw = (
        "我想在图书馆写一会儿实验报告，然后吃饭，今天17:00去9教上课，"
        "一个半小时后下课。然后写一个小时作业，还想背单词，还需要洗20分钟衣服"
    )
    commitments = [_commitment("上课", "17:00", relative=90, location="9教", kind="class")]
    pass_a = _proposal(
        commitments,
        [
            _task("写实验报告", location="图书馆", after_commitment_index=1),
            _task("吃饭", kind="meal", after_commitment_index=1),
            _task("写作业", minutes=60, after_commitment_index=1),
            _task("背单词", after_commitment_index=1),
            _task("洗衣服", minutes=20, after_commitment_index=1),
        ],
    )
    repaired = _proposal(
        commitments,
        [
            _task("写实验报告", location="图书馆"),
            _task(
                "吃饭", kind="meal", meal_period="unspecified",
                meal_before_commitment_index=1,
            ),
            _task("写作业", minutes=60, after_commitment_index=1),
            _task("背单词", after_commitment_index=1),
            _task("洗衣服", minutes=20, after_commitment_index=1),
        ],
    )
    caller = RoleCaller(
        json.dumps(pass_a, ensure_ascii=False),
        _audit("repair", repaired, ("课前报告和吃饭不属于下课后任务",)),
    )
    outcome = run_day_intake(
        REFERENCE, raw, caller, semantic_auditor_caller=caller, campus_id="weijinlu"
    )
    tasks = outcome.proposal.tasks
    assert tasks[0].after_commitment_index is None
    assert tasks[1].meal_before_commitment_index == 1
    assert tasks[1].after_commitment_index is None
    assert [item.after_commitment_index for item in tasks[2:]] == [1, 1, 1]


def test_nonideal_extraction_end_conflict_gets_one_format_repair_then_semantic_repair():
    """Mimic a real response that redundantly emits end and duration."""
    malformed = _proposal(
        [_commitment("上课", "21:00", end="22:30", duration=90,
                     location="9教", kind="class")],
        [_task("写作业", minutes=60)],
    )
    format_repaired = _proposal(
        [_commitment("上课", "21:00", relative=90, location="9教", kind="class")],
        [_task("写作业", minutes=60)],
    )
    semantic_repaired = _proposal(
        format_repaired["commitments"],
        format_repaired["tasks"] + [_task("洗衣服", minutes=20)],
    )
    caller = FormatRepairThenAuditCaller(
        json.dumps(malformed, ensure_ascii=False),
        json.dumps(format_repaired, ensure_ascii=False),
        _audit("repair", semantic_repaired, ("漏洗衣服",)),
    )
    outcome = run_day_intake(
        REFERENCE,
        "21点上课，一个半小时后下课，然后写一小时作业，还要洗20分钟衣服",
        caller,
        semantic_auditor_caller=caller,
        campus_id="weijinlu",
    )
    assert outcome.applied is not None
    assert outcome.repair_used and outcome.semantic_repair_used
    assert outcome.call_count == 3
    assert outcome.applied.state.commitments[0].ends_at.strftime("%H:%M") == "22:30"
    assert [task.title for task in outcome.applied.state.tasks] == ["写作业", "洗衣服"]


def test_auditor_repairs_relative_end_wrongly_attached_to_homework():
    raw = "21点上课，一个半小时后下课，然后写一个小时作业"
    pass_a = _proposal(
        [_commitment("上课", "21:00", location="9教", kind="class")],
        [_task("写作业", minutes=90)],
    )
    repaired = _proposal(
        [_commitment("上课", "21:00", relative=90, location="9教", kind="class")],
        [_task("写作业", minutes=60)],
    )
    caller = RoleCaller(
        json.dumps(pass_a, ensure_ascii=False),
        _audit("repair", repaired, ("90分钟应绑定课程结束，作业时长是60分钟",)),
    )
    outcome = run_day_intake(
        REFERENCE, raw, caller, semantic_auditor_caller=caller, campus_id="weijinlu"
    )
    assert outcome.applied.state.commitments[0].ends_at.strftime("%H:%M") == "22:30"
    assert outcome.applied.state.tasks[0].total_minutes == 60


def test_complete_pass_a_is_approved_without_rewrite():
    proposal = _proposal(
        [_commitment("上课", "21:00", relative=90, location="9教", kind="class")],
        [_task("写作业", minutes=60), _task("洗衣服", minutes=20)],
    )
    caller = RoleCaller(json.dumps(proposal, ensure_ascii=False), _audit())
    outcome = run_day_intake(
        REFERENCE,
        "21点上课，一个半小时后下课，然后写一小时作业、洗20分钟衣服",
        caller,
        semantic_auditor_caller=caller,
        campus_id="weijinlu",
    )
    assert not outcome.semantic_repair_used
    assert outcome.proposal.commitments[0].relative_end_minutes == 90
    assert [task.total_minutes for task in outcome.proposal.tasks] == [60, 20]


@pytest.mark.parametrize("bad_audit", ["拒绝回答", "{}", '{"decision":"repair"}'])
def test_malformed_or_refused_auditor_keeps_safe_parsed_extraction(bad_audit):
    proposal = _proposal([], [_task("背单词")])
    caller = RoleCaller(json.dumps(proposal, ensure_ascii=False), bad_audit)
    outcome = run_day_intake(
        REFERENCE,
        "背单词",
        caller,
        semantic_auditor_caller=caller,
        campus_id="weijinlu",
    )
    assert outcome.applied is not None
    assert outcome.proposal.tasks[0].title == "背单词"
    assert not outcome.semantic_repair_used
    assert "schema" not in " ".join(outcome.warnings + outcome.questions).lower()


def test_auditor_prompt_contains_raw_extraction_context_but_no_planning_job():
    proposal = _proposal([], [_task("背单词")])
    from src.p2_day_intake import parse_day_intake
    parsed = parse_day_intake(json.dumps(proposal, ensure_ascii=False))
    system, user = build_initial_intake_audit_prompt(
        REFERENCE, "背单词", parsed, campus_id="weijinlu"
    )
    assert "漏任务" in system
    assert "relative_end_minutes" in system
    assert "weijinlu" in user and "背单词" in user
    assert "不规划路线" in system
