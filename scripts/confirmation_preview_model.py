"""Finite fake for the inline clarification UI; no production imports this."""
import json
from scripts.offline_product_model import OfflineProductModel, encoded


class ConfirmationPreviewModel(OfflineProductModel):
    def __init__(self, next_question=False):
        super().__init__()
        self.next_question = next_question
        self.bindings = []

    def _response(self, system, user):
        if 'p3.unified-feedback.v1' in system:
            marker = '当前确认回答：'
            data, _ = json.JSONDecoder().raw_decode(user.split(marker, 1)[1])
            self.bindings.append({k: data[k] for k in ('question', 'answer', 'target_commitment_ref', 'field')})
            accepted = data['answer'] in ('21:00', '九点')
            return encoded('p3.unified-feedback.v1', task_updates=[],
                commitment_updates=[dict(target_commitment_ref=data['target_commitment_ref'],
                    action='update_time', ends_at='21:00', starts_at=None, title=None, delay_minutes=None)] if accepted else [],
                movement=dict(has_movement=False,origin_text=None,destination_text=None,mode=None,depart_at=None,arrive_by=None),
                current_location=None, questions=[] if accepted else ['请填写具体的下课时间。'], reason=None), 'confirmation'
        raw, role = super()._response(system, user)
        if role == 'extract':
            data = json.loads(raw)
            data['current_location'] = '图书馆'
            data['events'][-1]['explicit_duration_minutes'] = None
            data['questions'] = ['上课大约几点结束？']
            return json.dumps(data, ensure_ascii=False), role
        if role == 'link':
            data = json.loads(raw)
            data['duration_of'] = []
            return json.dumps(data, ensure_ascii=False), role
        if role == 'feedback':
            data = json.loads(raw)
            data['intent_type'] = 'mixed'
            return json.dumps(data, ensure_ascii=False), role
        return raw, role


def seed_followup_question(store):
    """A formal pending-question fixture, not a simulated resolved fact.

    The finite intake fake does not model rest placement. Seed its unresolved
    question explicitly via the same final-turn validator so UI tests can
    exercise two pending questions without changing any planning logic.
    """
    from src.p2_session import load_live_final_turn, commit_live_final_turn
    bundle = load_live_final_turn(store)
    if bundle is not None and not store.get('offline_followup_question_seeded'):
        commit_live_final_turn(store, bundle.turn, store['p2_live_map'],
            extra_questions=bundle.extra_questions + ('这20分钟休息，你希望放在哪两个学习任务之间？',))
        store['offline_followup_question_seeded'] = True
        return True
    return False


class LocationConfirmationPreviewModel(OfflineProductModel):
    """A fixed class + after-class meal fixture; no natural-language parser."""
    def __init__(self, current=None):
        super().__init__()
        self.current = current
        self.bindings = []

    def _response(self, system, user):
        if '的 Raw Event Extractor。' in system:
            def event(ref, kind, title, order, start=None, end=None, location=None):
                return dict(local_event_id=ref, event_type=kind, title=title,
                    order_in_utterance=order, starts_at=start, ends_at=end,
                    explicit_duration_minutes=None, location_text=location,
                    commitment_kind='class' if kind=='fixed_commitment' else None, raw_evidence=title)
            return encoded('p4.raw-event-extraction.v1', day_end=None,
                current_location=self.current, transport_mode=None, questions=[],
                events=[event('course','fixed_commitment','上课',1,'16:00','18:00','46教学楼'),
                        event('meal','meal','吃晚饭',2)]), 'extract'
        if 'Semantic Linker' in system:
            return encoded('p4.event-semantics.v1', duration_of=[],
                commitment_relations=[dict(event_id='meal',commitment_event_id='course',relation='after')],
                meal_period_by_event=[dict(event_id='meal',meal_period='dinner')],
                explicit_sequence=[],execution_profiles=[],conflicts=[]), 'link'
        if 'Feedback Interpreter' in system:
            marker='当前确认回答：'
            data, _=json.JSONDecoder().raw_decode(user.split(marker,1)[1])
            self.bindings.append(data)
            return encoded('p4.feedback-decision.v1', intent_type='location_correction',
                target_task_refs=[], preferred_next_task_ref=None, priority_changes=[],
                ordering_constraints=[],cancelled_task_refs=[],restored_task_refs=[],
                completed_task_refs=[],postponed_task_refs=[],concurrency_changes=[],
                day_preference_candidate=False,location_correction='诚园7斋',
                explicit_user_preference=True,confidence=1.0,clarification_needed=False,
                clarification_question=None), 'location_confirmation'
        return super()._response(system,user)
