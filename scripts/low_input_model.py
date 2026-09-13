"""Finite lowest-level responses for the three low-input rehearsal stories."""
from scripts.offline_product_model import OfflineProductModel, encoded


STORIES = {
    "a": "晚上七点有课，课前想吃饭，还有高数作业想做一点。",
    "b": "想整理一下今天的笔记。",
    "c": "下午想把作业做一点。",
}


class LowInputModel(OfflineProductModel):
    def __init__(self, story="b", estimate=True):
        super().__init__()
        self.story = story
        self.estimate = estimate
        self.prompts = []  # synthetic material only; this class is development-only

    def agent_caller(self, system, user):
        self.prompts.append((system, user))
        return super().agent_caller(system, user)

    def _response(self, system, user):
        if "的 Raw Event Extractor。" in system:
            title = "整理今天的笔记" if self.story == "b" else "高数作业"
            events = [dict(local_event_id="work", event_type="task", title=title,
                order_in_utterance=1, starts_at=None, ends_at=None,
                explicit_duration_minutes=None, location_text=None,
                commitment_kind=None, raw_evidence=title)]
            if self.story == "a":
                for ref, kind, title, start in (("meal", "meal", "吃饭", None),
                                              ("class", "fixed_commitment", "上课", "19:00")):
                    events.append(dict(local_event_id=ref, event_type=kind, title=title,
                        order_in_utterance=len(events) + 1, starts_at=start, ends_at=None,
                        explicit_duration_minutes=None, location_text=None,
                        commitment_kind="class" if ref == "class" else None, raw_evidence=title))
            return encoded("p4.raw-event-extraction.v1", day_end=None, current_location=None,
                transport_mode=None, events=events, questions=[]), "extract"
        if "Semantic Linker" in system:
            return encoded("p4.event-semantics.v1", duration_of=[],
                commitment_relations=([dict(event_id="meal", commitment_event_id="class", relation="before")]
                                     if self.story == "a" else []),
                meal_period_by_event=[], explicit_sequence=[], conflicts=[],
                execution_profiles=[dict(event_id="work", splittable=True, minimum_chunk_minutes=15,
                    preferred_chunk_minutes=30, requires_single_session=False, source="qwen_semantic")]), "link"
        if "day-plan-intent" in system:
            return encoded("p2.day-plan-intent.v1", task_order=[], include_low_attention=False,
                task_estimates=([dict(task_ref="day_task_001", estimated_total_minutes=45,
                    is_splittable=True, minimum_slice_minutes=15)] if self.estimate else []),
                rationale="暂按一段笔记整理或作业推进估计专注工作；完整范围仍可补充确认。"), "plan"
        return super()._response(system, user)
