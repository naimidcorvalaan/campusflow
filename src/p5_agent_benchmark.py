"""Deterministic evaluation contract for the P5 Agent scenario benchmark.

The benchmark does not call a model.  Tests feed mocked semantic decisions and
hard-validated candidate observations into this evaluator, so a scenario only
passes when its meaningful relation, allocation, suggestion, clarification,
and copy expectations hold.
"""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class AgentScenarioExpectation:
    required_before: Tuple[Tuple[str, str], ...] = ()
    required_concurrency: Tuple[Tuple[str, str], ...] = ()
    forbidden_concurrency: Tuple[Tuple[str, str], ...] = ()
    expected_completion: Tuple[Tuple[str, int], ...] = ()
    expected_remaining: Tuple[Tuple[str, int], ...] = ()
    clarification_expected: Optional[bool] = None
    suggestion_expected: Optional[bool] = None
    required_timeline_phrases: Tuple[str, ...] = ()
    forbidden_copy_phrases: Tuple[str, ...] = ()
    expected_meal_windows: Tuple[Tuple[str, int, int], ...] = ()
    forbidden_overlap_pairs: Tuple[Tuple[str, str], ...] = ()
    canonical_state_unchanged: Optional[bool] = None


@dataclass(frozen=True)
class AgentScenarioObservation:
    task_sequence: Tuple[str, ...] = ()
    concurrency_pairs: Tuple[Tuple[str, str], ...] = ()
    task_completion: Tuple[Tuple[str, int], ...] = ()
    remaining_work: Tuple[Tuple[str, int], ...] = ()
    clarification_shown: bool = False
    suggestion_shown: bool = False
    timeline: Tuple[str, ...] = ()
    user_copy: str = ""
    meal_start_minutes: Tuple[Tuple[str, int], ...] = ()
    overlap_pairs: Tuple[Tuple[str, str], ...] = ()
    canonical_fingerprint_before: Optional[str] = None
    canonical_fingerprint_after: Optional[str] = None


@dataclass(frozen=True)
class AgentBenchmarkScenario:
    scenario_id: str
    category: str
    expectation: AgentScenarioExpectation
    observation: AgentScenarioObservation

    def __post_init__(self):
        if not isinstance(self.scenario_id, str) or not self.scenario_id.strip():
            raise ValueError("scenario_id must be non-empty")
        if not isinstance(self.category, str) or not self.category.strip():
            raise ValueError("category must be non-empty")


@dataclass(frozen=True)
class AgentBenchmarkCaseResult:
    scenario_id: str
    category: str
    passed: bool
    failures: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentBenchmarkReport:
    results: Tuple[AgentBenchmarkCaseResult, ...]

    @property
    def total(self):
        return len(self.results)

    @property
    def passed(self):
        return sum(1 for item in self.results if item.passed)

    @property
    def failed(self):
        return self.total - self.passed

    @property
    def failed_scenario_ids(self):
        return tuple(item.scenario_id for item in self.results if not item.passed)


def evaluate_agent_scenario(scenario):
    if not isinstance(scenario, AgentBenchmarkScenario):
        raise TypeError("scenario must be AgentBenchmarkScenario")
    expected = scenario.expectation
    actual = scenario.observation
    failures = []
    first_positions = {}
    last_positions = {}
    for index, task_ref in enumerate(actual.task_sequence):
        first_positions.setdefault(task_ref, index)
        last_positions[task_ref] = index
    for before, after in expected.required_before:
        if before not in last_positions or after not in first_positions:
            failures.append("required ordering task missing: {} before {}".format(before, after))
        elif last_positions[before] >= first_positions[after]:
            failures.append("required ordering violated: {} before {}".format(before, after))
    actual_pairs = set(actual.concurrency_pairs)
    for pair in expected.required_concurrency:
        if pair not in actual_pairs:
            failures.append("required concurrency missing: {}".format(pair))
    for pair in expected.forbidden_concurrency:
        if pair in actual_pairs:
            failures.append("forbidden concurrency present: {}".format(pair))
    completion = dict(actual.task_completion)
    for task_ref, minutes in expected.expected_completion:
        if completion.get(task_ref) != minutes:
            failures.append("completion mismatch: {}".format(task_ref))
    remaining = dict(actual.remaining_work)
    for task_ref, minutes in expected.expected_remaining:
        if remaining.get(task_ref) != minutes:
            failures.append("remaining mismatch: {}".format(task_ref))
    if expected.clarification_expected is not None and actual.clarification_shown != expected.clarification_expected:
        failures.append("clarification expectation mismatch")
    if expected.suggestion_expected is not None and actual.suggestion_shown != expected.suggestion_expected:
        failures.append("suggestion expectation mismatch")
    timeline = "\n".join(actual.timeline)
    for phrase in expected.required_timeline_phrases:
        if phrase not in timeline:
            failures.append("timeline phrase missing: {}".format(phrase))
    for phrase in expected.forbidden_copy_phrases:
        if phrase in actual.user_copy:
            failures.append("forbidden copy phrase present: {}".format(phrase))
    meal_starts = dict(actual.meal_start_minutes)
    for task_ref, starts_at, ends_at in expected.expected_meal_windows:
        actual_start = meal_starts.get(task_ref)
        if actual_start is None or not starts_at <= actual_start < ends_at:
            failures.append("meal window mismatch: {}".format(task_ref))
    overlaps = set(actual.overlap_pairs)
    for pair in expected.forbidden_overlap_pairs:
        if pair in overlaps or (pair[1], pair[0]) in overlaps:
            failures.append("forbidden overlap present: {}".format(pair))
    if expected.canonical_state_unchanged is not None:
        unchanged = (
            actual.canonical_fingerprint_before is not None
            and actual.canonical_fingerprint_before
            == actual.canonical_fingerprint_after
        )
        if unchanged != expected.canonical_state_unchanged:
            failures.append("canonical mutation expectation mismatch")
    return AgentBenchmarkCaseResult(
        scenario.scenario_id, scenario.category, not failures, tuple(failures)
    )


def run_agent_benchmark(scenarios):
    scenarios = tuple(scenarios)
    ids = [item.scenario_id for item in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("benchmark scenario ids must be unique")
    return AgentBenchmarkReport(tuple(evaluate_agent_scenario(item) for item in scenarios))
