"""Read-only progress and segment facts for narration, never allocation."""
import re
from datetime import datetime, timedelta


COPY_SEMANTICS_RULES = (
    "只引用正式地点事实，不为购物等任务自行命名商店或目的地。"
    "当前位置未知与课程地点未知是两回事；不能因缺当前位置而说已明确的课程地点待确认。"
    "出发、抵达与开始准备分别使用对应事件时刻，不能互换；不要在结尾重新下达已在时间线中的行动指令。"
    "一句话若把多个动作绑定到同一时刻，必须逐个核对；其中一个动作的时刻正确不能证明整句正确。"
    "抵达地点不代表已完成其后的准备或操作，开始准备不代表已经完成准备。"
    "task.state=active 仅表示可安排的未结束任务，不代表已经开始。"
    "abandoned 表示用户已取消，skipped_today 表示今天明确不再安排；两者都不是今天待补排的工作。"
    "历史剩余分钟保留用于状态记录，只有eligible_today=true的任务才可描述为今天尚待安排。"
    "planned_minutes_by_task是整日计划分配量，不是已完成量；unallocated_minutes_after_entire_plan"
    "是整份计划之后仍未安排的工作量，不能说它已经安排在某个后续时段。"
    "以 copy_semantics 为准：not_started 只能说‘现在开始/先做/接下来做’，"
    "不得说‘正在/已经开始/继续/接着完成’；planned_minutes 不是真实进度。"
    "partially_completed 可以说已开始、部分完成或继续，但不等于此刻正在做；"
    "只有 reported_in_progress=true 才能说‘正在/进行中’，completed 才能说已经全部完成。"
    "完成当前片段不等于完成整个任务。opening 默认指首个片段："
    "segment_covers_remaining=false 时只能说‘先做N分钟/完成前N分钟/推进一部分’，"
    "不得说‘做完/完成这项任务/全部完成’。后段能完成时必须明确那一段的时间，"
    "不能用整日 remaining_work=0 替首段作完成承诺。后续重复片段用‘再做’表述未来安排。"
)


def copy_semantic_facts(context, actual_tasks=()):
    tasks = {}
    for task in tuple(context.active_tasks) + tuple(actual_tasks or ()):
        total = getattr(task, "effective_duration_minutes", None)
        if total is None:
            total = tasks.get(task.task_ref, {}).get("adopted_duration_minutes", task.total_minutes)
        progress = task.completed_minutes
        remaining = max(0, total - progress) if total is not None else task.remaining_minutes
        state = getattr(task.state, "value", task.state)
        background_running = (getattr(task, "attention_mode", "active") == "background"
                              and getattr(task, "user_reported_running", False))
        status = (state if state in ("abandoned", "skipped_today") else
                  "completed" if state == "completed" or task.remaining_minutes == 0 else
                  "running" if background_running else
                  "partially_completed" if progress > 0 else "not_started")
        latest = context.latest_feedback_text or context.latest_user_text or ""
        reported_running = status == "partially_completed" and any(
            task.title in statement and re.search(r"正在|进行中|现在在做", statement)
            and not re.search(r"暂停|停下|不再|不在|没在|不是|并非", statement)
            for statement in re.split(r"[。；;！？!?\n]", latest)
        )
        tasks[task.task_ref] = dict(task_ref=task.task_ref, title=task.title,
            total_minutes=task.total_minutes, adopted_duration_minutes=total,
            completed_minutes=progress, remaining_minutes=remaining,
            progress_status=status, state=state, eligible_today=state == "active")
        tasks[task.task_ref]["reported_in_progress"] = bool(reported_running or
            (background_running and state == "active" and status != "completed"))
    windows = {row[0]: row[1] for row in context.available_windows}
    cursors = {}
    segments = []
    for item in context.selected_plan:
        start = item.starts_at or cursors.get(item.window_ref) or windows.get(item.window_ref)
        end = None
        if start:
            end = (datetime.fromisoformat(start) + timedelta(minutes=item.planned_minutes)).isoformat()
            if item.starts_at is None:
                cursors[item.window_ref] = end
        segments.append(dict(task_ref=item.task_ref, allocation_ref=item.allocation_ref,
            start=start, end=end, planned_minutes=item.planned_minutes,
            remaining_before=item.planned_minutes + item.remaining_after,
            remaining_after=item.remaining_after,
            occupies_attention=item.occupies_attention,
            segment_covers_remaining=item.remaining_after == 0))
    return {"tasks": list(tasks.values()), "segments": segments}


def has_invalid_progress_or_completion(context, candidate, narrative, actual_tasks=()):
    facts = copy_semantic_facts(context, actual_tasks)
    tasks = facts["tasks"]
    if not tasks:
        return False
    for field in ("opening", "why_this_plan", "closing", "proactive_suggestion", "risk_note"):
        text = getattr(narrative, field) or ""
        for clause in re.split(r"[。！？!?；;\n]", text):
            named = [task for task in tasks if task["title"] in clause]
            # Anonymous imperative in an opening/closing refers to the
            # current task only if it is unambiguous. Universal claims apply
            # to every task; never borrow another task's progress as support.
            current_ref = facts["segments"][0]["task_ref"] if facts["segments"] else None
            current = [task for task in tasks if task["task_ref"] == current_ref]
            subjects = named or (tasks if re.search(r"全部|所有|都|各项", clause) else current or tasks)
            if re.search(r"正在|进行中|现在在做", clause) and any(
                not task["reported_in_progress"] for task in subjects
            ):
                return True
            resumed = bool(re.search(r"已经开始|已开始|继续(?:练习|学习|做|写|读|背|复习|完成|推进|整理)|接着完成", clause))
            if not resumed:
                resumed = any("继续" + task["title"] in clause for task in subjects)
            if resumed and any(task["progress_status"] != "partially_completed" for task in subjects):
                return True
            reported_progress = re.findall(r"(?:已经|已)(?:做|写|读|学习|投入|完成)(?:了)?(\d+)\s*分钟", clause)
            if reported_progress and any(
                int(value) != task["completed_minutes"] for value in reported_progress for task in subjects
            ):
                return True
            # A legitimate partial-completion phrase cannot hide a second
            # whole-task claim in the same sentence.
            whole_claim = re.sub(
                r"(?:已经|已)?部分完成|完成(?:了)?(?:(?:前|这|本)(?:一段|部分|\d+\s*分钟)|一部分|本段|当前片段|\d+\s*分钟)",
                "", clause,
            )
            historical_complete = bool(re.search(r"(?:已经|已)(?:全部|完全|全都|都)?(?:做完|写完|完成)|(?:做完|写完|完成)了", whole_claim))
            if historical_complete:
                if any(task["progress_status"] != "completed" for task in subjects):
                    return True
            if "部分完成" in clause and any(task["progress_status"] != "partially_completed" for task in subjects):
                return True
            if re.search(r"尚未开始|还没开始|未开始", clause) and any(
                task["progress_status"] != "not_started" for task in subjects
            ):
                return True
            if not re.search(r"做完|写完|完成", whole_claim) or historical_complete:
                continue
            for task in subjects:
                if task["progress_status"] == "completed":
                    continue
                segments = [item for item in facts["segments"] if item["task_ref"] == task["task_ref"]]
                if not segments:
                    if dict(candidate.remaining_work).get(task["task_ref"], task["remaining_minutes"]) != 0:
                        return True
                    continue
                aggregate_scope = re.search(r"今天|今日|合计|总共|全部片段|两个片段|两段", clause)
                immediate_scope = re.search(r"现在|当前|本段|先(?:做|安排|完成|把)", clause)
                if (aggregate_scope and not immediate_scope
                        and sum(item["planned_minutes"] for item in segments) >= segments[0]["remaining_before"]):
                    continue
                mentioned_times = re.findall(r"\d{1,2}:\d{2}", clause)
                referenced = [item for item in segments if any(
                    time in (item["start"] or "", item["end"] or "")
                    or time == (item["start"] or "")[11:16]
                    or time == (item["end"] or "")[11:16]
                    for time in mentioned_times
                )]
                # A bare "finish" inherits the first segment, not the sum of
                # all later segments. A named later end may promise finishing
                # then, but never reports it as already completed.
                target = referenced[-1] if referenced else segments[0]
                amounts = [int(value) for value in re.findall(r"(\d+)\s*分钟", clause)]
                if not target["segment_covers_remaining"]:
                    return True
                if amounts and max(amounts) < target["remaining_before"]:
                    return True
    return False


def opening_states_known_progress(context, opening, actual_tasks=()):
    return any(task["title"] in opening and task["progress_status"] == "completed"
               for task in copy_semantic_facts(context, actual_tasks)["tasks"])
