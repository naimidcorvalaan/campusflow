"""Complete grounded effort accounting and atomic fallback publication."""
import json
from copy import deepcopy
from dataclasses import replace
import pytest

from src.material_effort import check_effort,validate_ledger,digest,REPAIR
from src.material_formal_validation import FormalValidationError
from src.material_estimate_diagnostics import capture_estimate_diagnostics,EstimateTrace
from src.material_workload import estimate_workloads
from src.material_estimate_contract import complete_final_estimate,validate_final_estimate
from src.material_estimate_recovery import minimal_confirmation_mode
from src.material_inbox import MaterialInbox,MATERIAL_INBOX_KEY,update_source,extract_material
from src.material_ui import save_material_edit,handle_material_action
from src.p2_session import load_live_final_turn
from tests.test_estimate_protocol_diagnostics import work,row
from tests.test_material_estimate_recovery import live,run,NOW,FORM,action_payload,draft_for
from tests.test_material_owner_qualifier import FACTS,REQ,CLAIMS,GOOD


def ledger(core,extra=0,included=None):
    return dict(completeness='complete',core=[dict(category='writing',minutes=core)],
        adjustments=[dict(category='lookup',minutes=extra,reason='补查资料并确认引用',
            evidence_ref='supplement',evidence='还需查资料',included_in=included)] if extra else [])


@pytest.mark.parametrize('core,extra,suggested,valid',[
    (130,20,150,True),(110,40,150,True),(110,0,150,False),
    (110,0,125,True),(45,0,135,False),(55,0,55,True),(130,0,150,True)])
def test_explicit_accounting_uses_existing_tolerance(core,extra,suggested,valid):
    result=check_effort(suggested,40,180,'依据',ledger=ledger(core,extra),sources={'supplement':'还需查资料'})
    assert result.valid is valid
    assert result.explained_min==result.explained_max==core+extra
    assert result.tolerance<=max(15,suggested*.2)


def test_recommendation_still_must_be_in_own_range():
    assert not check_effort(150,90,140,'依据',ledger=ledger(150)).valid


def test_already_included_adjustment_is_not_added_twice():
    value=ledger(130,20,included=0)
    assert validate_ledger(value)==(130,0)
    assert check_effort(150,120,180,'依据',ledger=value).explained_min==130


@pytest.mark.parametrize('mutation,code',[
    (lambda o:o.update(completeness='illustrative'),'incomplete_effort_ledger'),
    (lambda o:o['adjustments'][0].update(evidence='凭空编造的材料要求'),'unverified_adjustment'),
    (lambda o:o['adjustments'][0].update(evidence_ref='unknown'),'unverified_adjustment'),
    (lambda o:o['adjustments'][0].update(reason='补足差额'),'unsupported_adjustment_reason'),
    (lambda o:o['adjustments'][0].update(reason='考虑复杂性'),'unsupported_adjustment_reason'),
])
def test_gap_is_not_automatically_filled_or_grounding_invented(mutation,code):
    value=ledger(110,40);mutation(value)
    with pytest.raises(FormalValidationError) as exc:
        validate_ledger(value,{'supplement':'还需查资料'})
    assert exc.value.feedback['code']==code
    assert '否则降低或修正建议' in REPAIR and '不是要求补同样数额' in REPAIR


@pytest.mark.parametrize('lower_suggested',[False,True])
def test_one_repair_can_add_grounded_work_or_lower_recommendation(lower_suggested):
    calls=[]
    def caller(system,user):
        request=json.loads(user);calls.append(request)
        value=row(request,150)
        value.update(min_focus_minutes=100,max_focus_minutes=180,basis='完成写作并核对引用',
            effort_ledger=ledger(110))
        if len(calls)==2:
            feedback=request['validation_feedback'][0]
            assert feedback['unexplained_gap_minutes']==40
            assert feedback['recommended_minutes']==150
            assert feedback['core_subtotal']==110 and feedback['explicit_adjustments']==0
            assert request['workloads']==calls[0]['workloads']
            if lower_suggested:value['recommended_minutes']=125
            else:value['effort_ledger']=ledger(110,40)
        return json.dumps(dict(estimates=[value]))
    with capture_estimate_diagnostics() as events:
        values,error=estimate_workloads([work()],caller,supplement='还需查资料')
    assert len(calls)==2 and not error
    _,estimate,origin=values[0]
    assert origin=='model_workload'
    assert estimate['effort_grounding']==digest(estimate['effort_ledger'])
    assert estimate['recommended_minutes']==(125 if lower_suggested else 150)
    assert not any(e.get('fallback') for e in events)


def test_safe_snapshot_uses_typed_ledger_not_duplicate_prose():
    value=dict(recommended_minutes=150,min_focus_minutes=100,max_focus_minutes=180,
        basis='private material secret',assumptions=['secret API Key'],effort_ledger=ledger(110,40))
    with capture_estimate_diagnostics() as events:
        EstimateTrace('test').response(json.dumps(value))
    snapshot=events[0]['snapshot']['estimates'][0]
    assert snapshot['effort_ledger']['total']==150
    assert snapshot['arithmetic']['explained_min']==150
    encoded=json.dumps(events,ensure_ascii=False)
    for secret in ['private material secret','secret API Key','还需查资料','补查资料并确认引用']:
        assert secret not in encoded


def test_final_model_contract_keeps_ledger_facts_and_manual_minutes():
    draft=update_source(MaterialInbox(),FORM,'',NOW,'plan','beiyangyuan','还需查资料').draft
    obj=action_payload()
    obj['estimate'].update(recommended_minutes=150,min_focus_minutes=120,max_focus_minutes=180,
        basis='填写并复核',effort_ledger=ledger(110,40))
    result=extract_material(draft,lambda *a:json.dumps(obj))
    entry=result.estimate_fallbacks[0]
    # The final object attaches program-owned source facts, never model copies.
    entry=complete_final_estimate(entry,draft,set(),[json.dumps(obj)],FACTS,REQ)
    assert validate_final_estimate(entry)==()
    assert all(c in entry['quantity_claims'] for c in CLAIMS)
    expected=ledger(110,40);validate_ledger(expected,{'supplement':'还需查资料'})
    assert entry['effort_ledger']==expected
    from src.material_estimate_recovery import confirm_minimal_task
    result=replace(result,estimate_fallbacks=(entry,))
    adopted=confirm_minimal_task(result,entry['item_id'],'材料处理',157,True,entry['estimate']['short_scope'])
    assert adopted.items[0].user_edits['minutes']=='157'
    assert entry['estimate']['recommended_minutes']==150


def candidate_and_live(monkeypatch):
    st,session,model,_=live();old,_=run()
    assert save_material_edit(st,MaterialInbox(old),NOW)
    new=update_source(MaterialInbox(old),FORM,'',NOW,'plan','beiyangyuan','还需查资料').draft
    result=extract_material(new,lambda *a:'{}',workload_caller=lambda *a:'{}')
    entry=result.estimate_fallbacks[0]
    assert entry['origin']=='local_workload'
    obj=action_payload();obj['estimate']['basis']=GOOD
    entry=complete_final_estimate(entry,new,set(),[json.dumps(obj)],FACTS,REQ)
    assert validate_final_estimate(entry)==()
    result=replace(result,estimate_fallbacks=(entry,))
    import src.material_ui as ui
    monkeypatch.setattr(ui,'extract_material',lambda *a,**kw:result)
    return st,session,model,old,new,result


def test_valid_fallback_replaces_old_atomically_preserves_facts_and_is_adoptable(monkeypatch):
    st,session,model,old,new,result=candidate_and_live(monkeypatch)
    with capture_estimate_diagnostics() as events:
        assert handle_material_action(st,session,model,'extract',NOW,source_draft=new)
    actual=st.session_state[MATERIAL_INBOX_KEY].draft
    assert actual==result and actual!=old
    entry=actual.estimate_fallbacks[0]
    assert validate_final_estimate(entry)==() and minimal_confirmation_mode(entry)!='blocked'
    assert all(c in entry['quantity_claims'] for c in CLAIMS)
    assert entry['confirmation_fields']==()
    assert entry['estimate']['short_scope']
    codes=[e['code'] for e in events]
    assert codes.index('fallback_validated')<codes.index('draft_replacement_attempted')<codes.index('draft_replaced')<codes.index('fallback_published')
    assert handle_material_action(st,session,model,'prepare_estimate',NOW,item_id=entry['item_id'],
        confirmed_title=entry['estimate']['task_name'],confirmed_minutes=57,simple_confirmed=True,
        confirmed_scope=entry['estimate']['short_scope'])
    assert st.session_state[MATERIAL_INBOX_KEY].draft.items[0].user_edits['minutes']=='57'
    assert load_live_final_turn(st.session_state) is None


@pytest.mark.parametrize('source_argument',[True,False])
def test_invalid_fallback_preserves_old_draft_and_reports_exact_field(monkeypatch,source_argument):
    st,session,model,old,new,result=candidate_and_live(monkeypatch)
    result.estimate_fallbacks[0]['estimate']['recommended_minutes']=0
    with capture_estimate_diagnostics() as events:
        assert not handle_material_action(st,session,model,'extract',NOW,source_draft=new if source_argument else None)
    assert st.session_state[MATERIAL_INBOX_KEY].draft==old
    assert minimal_confirmation_mode(old.estimate_fallbacks[0])!='blocked'
    assert any(e.get('formal_validation',{}).get('field_path')=='$.estimate.recommended_minutes' for e in events)
    assert any(e['code']=='fallback_failed' for e in events)
    assert not any(e['code']=='draft_replacement_attempted' for e in events)


@pytest.mark.parametrize('unexpected',[False,True])
def test_fallback_disk_failure_rolls_back_and_records_failure(monkeypatch,tmp_path,unexpected):
    st,session,model,old,new,result=candidate_and_live(monkeypatch)
    from src import p2_live_main as main
    from src.local_persistence import LocalProfileStore,LocalPersistenceError
    st.session_state[main.LOCAL_PROFILE_STORE_KEY]=LocalProfileStore(tmp_path/'temporary.sqlite3')
    st.session_state[main.LOCAL_PROFILE_REVISION_KEY]=0
    def fail(*a,**kw):raise (RuntimeError if unexpected else LocalPersistenceError)('synthetic write failure')
    monkeypatch.setattr(main,'_persist_local_profile',fail)
    with capture_estimate_diagnostics() as events:
        assert not handle_material_action(st,session,model,'extract',NOW,source_draft=new)
    assert st.session_state[MATERIAL_INBOX_KEY].draft==old
    assert any(e['code']=='draft_replacement_failed' for e in events)
    assert any(e['code']=='fallback_failed' and e['fallback_reason']=='persistence_failed' for e in events)
    assert not any(e['code']=='fallback_published' for e in events)
    assert load_live_final_turn(st.session_state) is None
