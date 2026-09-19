"""The answer belongs to an existing question, never to generic feedback UI."""
import json
from dataclasses import replace
from datetime import datetime

import pytest

from scripts.confirmation_preview_model import ConfirmationPreviewModel
from src.p2_live_main import (_submit_planning_confirmation, LIVE_SESSION_KEY,
                             LIVE_MAP_KEY, LIVE_INTAKE_KEY)
from src.p2_session import load_live_final_turn
from src.planning_confirmation_ui import bind_confirmation, answer_message, verify_answer_result
from tests.test_p2_live_main import _StubSt, _run_main, dt


def start(next_question=False):
    model = ConfirmationPreviewModel(next_question)
    st = _StubSt().set_inputs(intake='我现在在图书馆，写90分钟作业，19:00去9教上课。', intake_submitted=True)
    _run_main(st, model.agent_caller, now=dt(14))
    assert not st.errors
    if next_question:
        from scripts.confirmation_preview_model import seed_followup_question
        seed_followup_question(st.session_state)
    return st, model


def submit(st, binding, answer):
    _submit_planning_confirmation(st, st.session_state[LIVE_SESSION_KEY], binding, answer, (),
                                  dt(14), st.session_state[LIVE_MAP_KEY], 'beiyangyuan', dt(14))


@pytest.mark.parametrize('answer', ['21:00', '九点'])
def test_answer_binds_current_commitment_and_preserves_start_tasks_progress(answer):
    st, model = start()
    before = load_live_final_turn(st.session_state)
    binding = bind_confirmation(before, st.intake_text)
    message = answer_message(binding, answer, before)
    payload = json.loads(message.split('当前确认回答：')[1])
    assert payload['field'] == 'ends_at'
    assert payload['target_commitment_ref'] == before.state.commitments[0].commitment_ref
    assert payload['target_starts_at'].endswith('19:00:00')
    # The existing controller already owns the original request. Repeating
    # its old "current location" in this answer would replay an old fact.
    assert '我现在在图书馆' not in message
    submit(st, binding, answer)
    after = load_live_final_turn(st.session_state)
    assert after.state.commitments[0].ends_at == dt(21)
    assert after.state.commitments[0].starts_at == before.state.commitments[0].starts_at
    assert after.state.tasks == before.state.tasks
    assert after.state.now == before.state.now
    assert after.execution_context.current_location == before.execution_context.current_location
    assert bind_confirmation(after) is None
    assert model.calls.count('confirmation') == 1
    assert not st.errors and not st.warnings


def test_rejected_answer_retains_old_bundle_and_input_and_can_retry():
    st, model = start()
    before = load_live_final_turn(st.session_state)
    binding = bind_confirmation(before)
    key = 'cf_planning_answer_' + binding.token
    st.session_state[key] = '还不确定'
    submit(st, binding, st.session_state[key])
    assert load_live_final_turn(st.session_state) is before
    assert st.session_state[key] == '还不确定'
    assert any('时间' in str(w) for w in st.warnings)
    submit(st, binding, '21:00')
    assert load_live_final_turn(st.session_state).state.commitments[0].ends_at == dt(21)
    assert model.calls.count('confirmation') == 2


def test_stale_question_rejected_before_any_model_call():
    st, model = start()
    before = load_live_final_turn(st.session_state)
    binding = bind_confirmation(before)
    submit(st, binding, '21:00')
    current = load_live_final_turn(st.session_state)
    calls = len(model.calls)
    submit(st, binding, '22:00')
    assert load_live_final_turn(st.session_state) is current
    assert len(model.calls) == calls
    assert any('已更新' in str(w) for w in st.warnings)


def test_next_question_is_unresolved_question_not_answered_first():
    st, model = start(True)
    before = load_live_final_turn(st.session_state)
    binding = bind_confirmation(before)
    submit(st, binding, '21:00')
    after = load_live_final_turn(st.session_state)
    next_binding = bind_confirmation(after)
    assert after.state.commitments[0].ends_at == dt(21)
    assert next_binding.commitment_ref is None
    assert '休息' in next_binding.question
    assert next_binding.token != binding.token


@pytest.mark.parametrize('fault', ['start', 'other_task', 'new_commitment', 'wrong_end', 'wrong_target'])
def test_invalid_candidate_cannot_be_persisted_or_adopted(fault):
    st, model = start()
    before = load_live_final_turn(st.session_state)
    binding = bind_confirmation(before)
    # Test the guard on a validated candidate, without weakening the formal
    # final-turn constructor just to synthesize a broken object.
    submit(st, binding, '21:00')
    after = load_live_final_turn(st.session_state)
    from types import SimpleNamespace
    state = after.state
    if fault == 'start':
        state = replace(state, commitments=(replace(state.commitments[0], starts_at=dt(18)),) + state.commitments[1:])
    elif fault == 'other_task':
        state = replace(state, tasks=state.tasks[1:])
    elif fault == 'new_commitment':
        state = replace(state, commitments=())
    elif fault == 'wrong_end':
        state = replace(state, commitments=before.state.commitments)
    else:
        state = replace(state, commitments=(replace(state.commitments[0], commitment_ref='other'),))
    unsafe = SimpleNamespace(turn=SimpleNamespace(result=SimpleNamespace(updated_state=state)))
    with pytest.raises(ValueError):
        verify_answer_result(binding, before, unsafe)


def test_form_mounts_once_and_all_persistent_widgets_mount_before_slow_call(monkeypatch):
    events = []
    from contextlib import contextmanager
    class Surface(_StubSt):
        def form(self, name):
            events.append(('form', name))
            return super().form(name)
    from src import material_ui
    original = material_ui.render_material_inbox
    def material(*args):
        events.append(('material', 'mounted'))
        return original(*args)
    monkeypatch.setattr(material_ui, 'render_material_inbox', material)
    model = ConfirmationPreviewModel()
    def caller(system, user):
        events.append(('model', 'call'))
        return model.agent_caller(system, user)
    st = Surface().set_inputs(intake='19:00上课，写作业', intake_submitted=True)
    _run_main(st, caller, now=dt(14))
    assert events.count(('form', 'p2_live_intake_form')) == 1
    assert events.index(('material', 'mounted')) < events.index(('model', 'call'))
    calls = len(model.calls)
    st.set_inputs()
    events.clear()
    _run_main(st, caller, now=dt(14))
    assert events.count(('form', 'p2_live_intake_form')) == 1
    assert len(model.calls) == calls


def test_inline_form_submits_once_without_using_generic_feedback_input():
    st, model = start()
    class AnswerSurface(_StubSt):
        def text_input(self, label, key=None, **kwargs):
            return '21:00' if label == '你的回答' else super().text_input(label, key=key, **kwargs)
        def form_submit_button(self, label, **kwargs):
            return label == '确认并继续'
    page = AnswerSurface(st.session_state).set_inputs(feedback='这不是确认答案')
    _run_main(page, model.agent_caller, now=dt(14))
    assert load_live_final_turn(page.session_state).state.commitments[0].ends_at == dt(21)
    assert model.calls.count('confirmation') == 1
    assert model.bindings[0]['answer'] == '21:00'


def test_new_answer_drafts_follow_existing_profile_cleanup():
    from src.p2_live_main import _clear_profile_session
    store = {'cf_planning_answer_token': '21:00', 'cf_confirmation_target_token': 'course',
             'cf_intake_editor_open': True, 'unrelated_control': 'keep'}
    _clear_profile_session(store)
    assert store == {'unrelated_control': 'keep'}


def test_answered_named_question_cannot_be_rebound_to_another_unknown_class():
    from src.p2_question_filter import filter_pending_questions
    from types import SimpleNamespace
    state = SimpleNamespace(commitments=(
        SimpleNamespace(title='上课', starts_at=dt(19), ends_at=dt(21), location_text='9教'),
        SimpleNamespace(title='班会', starts_at=dt(21, 30), ends_at=None, location_text='9教'),
    ))
    assert filter_pending_questions(('“上课”大约几点结束？', '“班会”大约几点结束？'), state) == ('“班会”大约几点结束？',)
