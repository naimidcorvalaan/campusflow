"""Ref-bound attention and elapsed-time facts; no language interpretation.

Active allocations occupy the person. Background allocations occupy only their
own process and release dependants at their end. A planned launch is never an
observation that the process has already started.
"""
from dataclasses import replace
from datetime import timedelta


BACKGROUND_SEMANTICS = (
    "把需要人操作的启动、启动后无需持续操作或监督的运行过程、完成后处理结果区分为普通任务。"
    "启动动作和自主运行是两个不同事件，启动不是整个运行过程；必须保留自主运行事件及其完整持续时长。"
    "未给启动动作分钟时留作估时，不能把运行持续分钟转给启动动作。"
    "只有有原文或常识依据证明无需持续人工操作或监督的过程才能作为后台；"
    "需要持续注意、操作或监督的动作仍独占人的注意力，不因用户想赶时间而改成后台。"
    "后台过程依赖启动，后续需要其结果的任务依赖过程完成；"
    "期间执行的兼容动作不依赖整个过程完成，不应被串行化。"
    "把用户要求的期间执行表示为重叠关系，与等待完成的因果依赖区分。"
    "前往、返回、离开都是移动；用户要求某过程结束后才前往或返回时，约束的是出发，"
    "不能只让到达后的工作等到过程结束、却提前走完返程。把这种关系交给正式出发依赖。"
    "后台过程的地点不是人的当前位置；启动和领取/检查动作各自保留真实地点。"
    "已经运行仅是用户明确报告的实际事实，不能把未来安排当作已经运行。"
    "计划中的‘启动后运行’仍未启动；只有报告已经启动才是实际运行。"
    "参考当前时刻不是用户指定的任务开始/截止；未给绝对钟点时不要从持续分钟推算钟点字段。"
)


def validate_attention(task, known=None):
    mode = task.attention_mode
    overlap = getattr(task, 'overlap_task_ref', None)
    if overlap is not None:
        if mode != 'active' or not isinstance(overlap, str) or overlap == task.task_ref:
            raise ValueError('overlap requires active work and a distinct background target')
        if overlap in getattr(task, 'predecessor_task_refs', ()):
            raise ValueError('overlap cannot also require the same process to finish')
        if known is not None and (overlap not in known or known[overlap].attention_mode != 'background'):
            raise ValueError('overlap target must be a known background process')
    if mode not in ('active', 'background') or not isinstance(task.user_reported_running, bool):
        raise ValueError('invalid task attention mode/state')
    if mode == 'active':
        if task.launch_task_ref is not None or task.background_reason is not None or task.user_reported_running:
            raise ValueError('active task cannot carry background facts')
        return
    if not isinstance(task.background_reason, str) or not task.background_reason.strip():
        raise ValueError('background process requires semantic evidence')
    if not task.user_reported_running and not task.launch_task_ref:
        raise ValueError('background process requires a launch or confirmed running state')
    if task.launch_task_ref is not None:
        if not isinstance(task.launch_task_ref, str) or task.launch_task_ref == task.task_ref:
            raise ValueError('background launch ref must be a distinct task')
        if known is not None:
            launch = known.get(task.launch_task_ref)
            if launch is None or launch.attention_mode != 'active':
                raise ValueError('background launch must reference an active-attention task')
    if task.is_splittable is True:
        raise ValueError('unattended elapsed process cannot be fragmented')
    if known is not None and task.user_reported_running and task.launch_task_ref:
        from src.p2_models import TaskState
        launch = known[task.launch_task_ref]
        if launch.state is not TaskState.COMPLETED and launch.remaining_minutes != 0:
            raise ValueError('reported running process conflicts with unfinished launch')


def allocation_spans(state, plan):
    """One interval source for legacy sequential and explicit-time allocations."""
    windows = {w.window_ref: w for w in state.windows}
    offsets = {}
    result = []
    for part in sorted(plan.allocations, key=lambda p: (
            p.starts_at or windows[p.window_ref].starts_at, p.sequence_index, p.allocation_ref)):
        start = part.starts_at
        if start is None:
            start = windows[part.window_ref].starts_at + timedelta(minutes=offsets.get(part.window_ref, 0))
        end = start + timedelta(minutes=part.planned_minutes)
        if part.starts_at is None:
            offsets[part.window_ref] = offsets.get(part.window_ref, 0) + part.planned_minutes
        result.append((part, start, end))
    return tuple(sorted(result, key=lambda row: (row[1], row[2], row[0].allocation_ref)))


def attention_validation_errors(state, plan, context=None):
    """Independently validate attention and launch facts before publication."""
    from src.p2_models import TaskState
    tasks = {t.task_ref: t for t in state.tasks}
    windows = {w.window_ref: w for w in state.windows}
    errors, by_ref = [], {}
    for task in state.tasks:
        try:
            validate_attention(task, tasks)
        except ValueError as exc:
            errors.append(str(exc))
    for part, start, end in allocation_spans(state, plan):
        task = tasks.get(part.task_ref)
        if task is None or task.state is not TaskState.ACTIVE:
            errors.append('allocation references inactive/unknown task')
            continue
        by_ref.setdefault(part.task_ref, []).append((start, end))
        if part.occupies_attention != (task.attention_mode == 'active'):
            errors.append('allocation attention differs from formal task')
        if start < state.now or end > state.day_end:
            errors.append('attention interval outside planning horizon')
        if task.attention_mode == 'active':
            window = windows[part.window_ref]
            if start < window.starts_at or end > min(window.ends_at,
                    window.starts_at + timedelta(minutes=window.capacity_minutes)):
                errors.append('active allocation exceeds available window')
    for ref, intervals in by_ref.items():
        task = tasks[ref]
        if task.overlap_task_ref and tasks[task.overlap_task_ref].state is TaskState.ACTIVE:
            process_intervals = by_ref.get(task.overlap_task_ref, ())
            if not any(a < d and c < b for a,b in intervals for c,d in process_intervals):
                errors.append('required background overlap is absent')
        from src.p2_allocator import _remaining_for_allocation
        binding = context.binding_for(ref) if context is not None else None
        remaining = _remaining_for_allocation(task, getattr(binding, 'effective_duration_minutes', None))
        if remaining is not None and plan.planned_minutes_by_task.get(ref, 0) > remaining:
            errors.append('allocated minutes exceed formal remaining work')
        if task.attention_mode != 'background':
            continue
        if len(intervals) != 1:
            errors.append('background process fragmented')
        if task.user_reported_running:
            continue
        launch = tasks.get(task.launch_task_ref)
        launches = by_ref.get(task.launch_task_ref, ())
        if launch is None:
            continue  # reference validation above already failed
        if launch.state is not TaskState.COMPLETED and (not launches
                or plan.planned_minutes_by_task.get(launch.task_ref, 0) < (launch.remaining_minutes or 1)
                or max(end for _, end in launches) > min(start for start, _ in intervals)):
            errors.append('background process begins without completed launch')
        # The launch is a person's action, the autonomous process is not.
        # A remotely started process can be elsewhere; its location must never
        # impose a person-route requirement. Launch/result actions keep theirs.
    return tuple(dict.fromkeys(errors))


def allocate_attention(state, order, windows, durations, protected, earliest, latest,
                       chunks, concurrent, predecessors, presence=None):
    """Use the same tasks/windows/plan model with explicit attention intervals."""
    from src.p2_models import TaskState
    from src.p2_allocation_models import DayAllocationPlan, TaskAllocation, make_allocation_ref
    from src.p2_allocator import (_remaining_for_allocation, DEFAULT_MINIMUM_SLICE_MINUTES,
        MEAL_MINIMUM_MINUTES, _available_while_preserving_later_protected)
    from src.task_dependencies import dependency_order, validate_predecessors
    tasks = {t.task_ref: t for t in state.tasks}
    edges = {ref: tuple(values) for ref, values in predecessors.items()}
    for task in state.tasks:
        validate_attention(task, tasks)
        if task.attention_mode == 'background' and task.launch_task_ref and not task.user_reported_running:
            edges[task.task_ref] = tuple(dict.fromkeys(edges.get(task.task_ref, ()) + (task.launch_task_ref,)))
    edges = validate_predecessors(tasks, edges)
    ordering = {ref: tuple(dict.fromkeys(edges.get(ref, ()) +
        ((task.overlap_task_ref,) if task.overlap_task_ref else ()))) for ref,task in tasks.items()}
    order = dependency_order(order, validate_predecessors(tasks, ordering))
    free = [(w.window_ref, max(w.starts_at, state.now), min(w.ends_at,
             w.starts_at + timedelta(minutes=w.capacity_minutes))) for w in windows]
    complete = {t.task_ref: state.now for t in state.tasks if t.state is TaskState.COMPLETED}
    parts, missing, warnings, process_starts = [], [], [], {}
    for position, ref in enumerate(order):
        task = tasks[ref]
        if task.state is not TaskState.ACTIVE:
            continue
        parents = () if task.user_reported_running else edges.get(ref, ())
        amount = _remaining_for_allocation(task, durations.get(ref))
        amount = None if amount is None else max(0, amount - concurrent.get(ref, 0))
        if amount is None or any(p not in complete for p in parents):
            missing.append(ref)
            continue
        if amount <= 0:
            continue
        release = max([state.now, earliest.get(ref, state.now)] + [complete[p] for p in parents])
        overlap_end = None
        if task.overlap_task_ref and tasks[task.overlap_task_ref].state is TaskState.ACTIVE:
            if task.overlap_task_ref not in process_starts:
                missing.append(ref)
                continue
            release = max(release, process_starts[task.overlap_task_ref])
            overlap_end = complete[task.overlap_task_ref]
        limit = min(state.day_end, latest.get(ref, state.day_end))
        remaining = amount
        if task.attention_mode == 'background':
            end = release + timedelta(minutes=amount)
            if end > limit:
                missing.append(ref)
                continue
            parts.append(TaskAllocation(make_allocation_ref(len(parts)), ref, task.title,
                None, amount, len(parts), False, amount, 0, release, False))
            complete[ref] = end
            process_starts[ref] = release
            continue
        for window_ref, left, right in tuple(free):
            if presence and ref in presence and not any(
                    start <= left and right <= end for start, end in presence[ref]):
                continue
            start, end = max(left, release), min(right, limit)
            if overlap_end is not None and remaining == amount and start >= overlap_end:
                continue
            available = int((end-start).total_seconds()//60)
            protected_after = tuple(protected[later] for later in order[position+1:]
                if later in protected and tasks[later].state is TaskState.ACTIVE)
            if ref not in protected:
                capacities = {(w,a,b): int((b-a).total_seconds()//60) for w,a,b in free}
                # Preserve the full preferred duration when the current free
                # capacity supports it, before considering the existing floor.
                reserve = sum(min(value, max(capacities.values(), default=0))
                    for value in protected_after)
                available = min(available, max(0, sum(capacities.values()) - reserve))
                available = _available_while_preserving_later_protected(
                    available, (window_ref,left,right), capacities, protected_after)
            if available <= 0:
                continue
            if ref in protected:
                count = min(remaining, available)
                if count < MEAL_MINIMUM_MINUTES:
                    continue
            elif task.is_splittable is not True:
                if available < remaining:
                    continue
                count = remaining
            else:
                count = min(remaining, available)
                preferred = chunks.get(ref)
                if preferred is not None and remaining > available:
                    count = min(count, preferred)
                    if not protected_after:
                        later_capacity = sum(max(0, int((min(b, limit) - max(a, release)).total_seconds()//60))
                            for _, a, b in free if a >= right)
                        count = max(count, min(available, remaining - later_capacity))
                minimum = task.minimum_slice_minutes or DEFAULT_MINIMUM_SLICE_MINUTES
                if count < minimum and count < remaining:
                    continue
            finish = start + timedelta(minutes=count)
            parts.append(TaskAllocation(make_allocation_ref(len(parts)), ref, task.title,
                window_ref, count, len(parts), count < remaining, remaining, remaining-count, start))
            free.remove((window_ref, left, right))
            if left < start:
                free.append((window_ref, left, start))
            if finish < right:
                free.append((window_ref, finish, right))
            free.sort(key=lambda row: row[1])
            remaining -= count
            if remaining == 0 or ref in protected:
                complete[ref] = finish
                remaining = 0
                break
        if remaining:
            missing.append(ref)
    parts.sort(key=lambda p: (p.starts_at, p.sequence_index))
    parts = tuple(replace(p, sequence_index=i) for i, p in enumerate(parts))
    current = next((p for p in parts if tasks[p.task_ref].attention_mode == 'active'
        and p.starts_at <= state.now < p.starts_at+timedelta(minutes=p.planned_minutes)), None)
    unused = tuple((w.window_ref, sum(int((b-a).total_seconds()//60)
        for ref,a,b in free if ref == w.window_ref)) for w in windows)
    return DayAllocationPlan(parts, tuple(missing), unused, state.active_window_ref,
        current.allocation_ref if current else None,
        tuple(p for p in parts if p.window_ref != state.active_window_ref), tuple(warnings),
        sum(p.planned_minutes for p in parts))
