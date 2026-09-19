"""Validate ref-bound causal edges without interpreting user language."""


def dependency_order(priority, predecessors):
    pending = list(priority)
    ordered = []
    while pending:
        ready = next((ref for ref in pending
                      if all(parent in ordered for parent in predecessors.get(ref, ()))), None)
        if ready is None:
            raise ValueError("task dependencies contain a cycle")
        ordered.append(ready)
        pending.remove(ready)
    return tuple(ordered)


def validate_predecessors(known_refs, predecessors):
    known_refs = tuple(known_refs)
    known = set(known_refs)
    result = {}
    for ref, values in (predecessors or {}).items():
        if ref not in known or not isinstance(values, (tuple, list)):
            raise ValueError("dependency references an unknown task")
        values = tuple(values)
        if (any(not isinstance(value, str) or value not in known for value in values)
                or len(values) != len(set(values)) or ref in values):
            raise ValueError("dependency endpoints must be distinct known tasks")
        result[ref] = values
    dependency_order(known_refs, result)
    return result


def dependency_errors(state, plan, effective_duration_by_task_ref=None, protected_duration_by_task_ref=None):
    """Check actual plan intervals, independently of the allocator's order."""
    from src.task_attention import allocation_spans
    from src.p2_models import TaskState
    from src.p2_allocator import _remaining_for_allocation
    tasks = {task.task_ref: task for task in state.tasks}
    edges = validate_predecessors(tasks, {ref: task.predecessor_task_refs
        for ref, task in tasks.items() if task.predecessor_task_refs})
    spans = {}
    for part, start, end in allocation_spans(state, plan):
        spans.setdefault(part.task_ref, []).append((start, end))
    errors = []
    for ref, parents in edges.items():
        if ref not in spans:
            continue
        start = min(left for left, _ in spans[ref])
        for parent in parents:
            task = tasks[parent]
            if task.state is TaskState.COMPLETED:
                continue
            required = _remaining_for_allocation(task, (effective_duration_by_task_ref or {}).get(parent))
            intervals = spans.get(parent, ())
            amount = sum(int((right-left).total_seconds()//60) for left, right in intervals)
            from src.p2_allocator import MEAL_MINIMUM_MINUTES
            full = required is not None and amount >= required
            if parent in (protected_duration_by_task_ref or {}) and amount >= MEAL_MINIMUM_MINUTES:
                full = True
            if (task.state is not TaskState.ACTIVE or not full
                    or not intervals or max(right for _, right in intervals) > start):
                errors.append("unfulfilled task dependency: {} -> {}".format(parent, ref))
    return tuple(errors)


def departure_dependency_errors(state, plan, blocks):
    """Only explicit departure edges constrain travel, not all task edges."""
    from src.task_attention import allocation_spans
    from src.p2_models import TaskState
    tasks = {task.task_ref: task for task in state.tasks}
    finishes = {}
    for part, _, end in allocation_spans(state, plan):
        finishes[part.task_ref] = max(finishes.get(part.task_ref, end), end)
    errors = []
    for block in blocks:
        task = tasks.get(block.destination_activity_ref)
        if task is None:
            continue
        for ref in task.departure_after_task_refs:
            parent = tasks.get(ref)
            if parent is not None and parent.state is TaskState.COMPLETED:
                continue
            if ref not in finishes or block.window_start < finishes[ref]:
                errors.append('unfulfilled departure dependency: {} -> {}'.format(ref, task.task_ref))
    return tuple(errors)
