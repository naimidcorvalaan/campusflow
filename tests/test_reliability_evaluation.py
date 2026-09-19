import json
from copy import deepcopy
from scripts.reliability_fixtures import recognition_cases
from scripts.reliability_live import audit
from tests.test_fc_original_ten import candidate
from src.material_function_transport import ToolArguments


def test_diverse_gold_is_fixed_before_model_output():
    cases=recognition_cases()
    assert len(cases)==50 and len({c['text'] for c in cases})==50
    assert sum(c['expected_is_estimatable'] for c in cases)==45
    assert all(c['expected_feature_groups'] for c in cases if c['expected_is_estimatable'])
    assert any('\n' in c['text'] for c in cases)


def test_planning_variants_change_real_workload_without_losing_gold():
    from scripts.reliability_planning import cases
    base=cases();expanded=cases(15)
    assert expanded[:50]==base
    assert len(expanded)==len({c['input_summary'] for c in expanded})==150
    assert {c['campus'] for c in expanded}=={'beiyangyuan','weijinlu'}
    assert all(c['expected_tasks']==len(c['expected_minutes']) for c in expanded)


def test_parameterized_deadline_gold_includes_formal_class_preparation():
    from scripts.reliability_planning import cases
    rows=[r for r in cases(8) if r['category']=='fixed_course']
    assert all(r['expected_preclass_capacity']==40 for r in rows)
    assert all(r['expected_complete']==(sum(r['expected_minutes'])<=40) for r in rows)
    assert not rows[-1]['expected_complete']


def test_background_gold_accepts_no_invented_work():
    draft,_,obj=candidate()
    gold=recognition_cases()[-1]
    obj.update(items=[],workload=[],actionability=None)
    assert audit(ToolArguments(json.dumps(obj)),draft,gold)['business_pass']
    obj['actionability']=dict(is_estimatable=True,reason='invented',evidence_refs=[])
    assert not audit(ToolArguments(json.dumps(obj)),draft,gold)['business_pass']


def test_formal_business_failure_is_never_hidden_by_broad_gold(monkeypatch):
    from src.material_formal_validation import failure
    draft,gold,obj=candidate()
    def reject(*args):failure('workload_semantic_mismatch','$.workload','grounded semantics')
    monkeypatch.setattr('src.material_workload_semantics.validate_workload_semantics',reject)
    row=audit(ToolArguments(json.dumps(obj)),draft,gold)
    assert row['schema_valid'] and not row['business_pass']
    assert row['primary_failure_layer']=='formal business validation'


def test_workload_evidence_failure_is_not_misreported_as_enum_quality():
    draft,gold,obj=candidate()
    obj['workload'][0]['features'][0]['evidence']='unrelated source quotation'
    row=audit(ToolArguments(json.dumps(obj)),draft,gold)
    assert not row['business_pass']
    assert row['formal_failure']['code']=='workload_evidence_mismatch'
    assert row['primary_failure_layer']=='evidence'


def test_extended_gold_has_distinct_source_layouts_not_generated_answers():
    from scripts.reliability_fixtures import extended_recognition_cases
    rows=extended_recognition_cases()
    assert len(rows)==len({r['text'] for r in rows})==100
    assert rows[:50]==recognition_cases()
    assert all(r['expected_feature_groups'] for r in rows[50:])


def test_revision_in_scope_covers_text_without_requiring_duplicate_feature():
    from scripts.fc_action_coverage import coverage
    gold=dict(expected_feature_groups=[['short_text','long_text']])
    obj=dict(workload=[dict(scope='修改英文短文的语法和衔接')])
    assert coverage(obj,[],gold)[0]['covered']


def test_shared_nullable_guide_explains_required_children():
    from src.material_output_schema import recognition_semantic_guide
    guide=recognition_semantic_guide()
    assert '$.items[*].end.text' in guide
    assert '不创建内部字段为null的占位对象' in guide


def test_official_hashes_observe_external_profile_not_temporary_override(tmp_path,monkeypatch):
    from scripts.reliability_live import official_hashes
    directory=tmp_path/'CampusFlow';directory.mkdir()
    profile=directory/'campusflow.sqlite3';profile.write_bytes(b'original')
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path))
    monkeypatch.setenv('CAMPUSFLOW_DATA_DIR',str(tmp_path/'evaluation'))
    before=official_hashes();profile.write_bytes(b'changed')
    assert before['official-profile/campusflow.sqlite3']!=official_hashes()['official-profile/campusflow.sqlite3']
def test_meal_arrival_window_does_not_invent_a_finish_deadline():
    from datetime import datetime
    from types import SimpleNamespace
    from scripts.reliability_planning_core import meal_starts_in_window
    meal=SimpleNamespace(starts_at=datetime(2026,9,17,17,44),ends_at=datetime(2026,9,17,18,40))
    assert meal_starts_in_window([meal],'17:30','18:30')


def test_meal_arrival_outside_explicit_window_still_fails_gold():
    from datetime import datetime
    from types import SimpleNamespace
    from scripts.reliability_planning_core import meal_starts_in_window
    for hour,minute in ((17,29),(18,31)):
        assert not meal_starts_in_window([SimpleNamespace(starts_at=datetime(2026,9,17,hour,minute))],'17:30','18:30')
    assert not meal_starts_in_window([],'17:30','18:30')


def test_shared_system_repairs_are_metered_without_persisting_context(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from scripts import reliability_planning_core as module
    monkeypatch.setattr(module,'OUT',tmp_path)
    response=SimpleNamespace(status_code=200,json=lambda:dict(choices=[dict(message=dict(content='{}'),finish_reason='stop')],usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2)))
    for stage in ('day_intake_repair','unified_feedback_repair'):
        meter=module.Meter('https://evaluation.invalid',lambda *a,**k:response)
        meter.request(None,'POST','https://evaluation.invalid',json=dict(messages=[dict(role='system',content='same formal system'),dict(role='user',content=json.dumps(dict(request_stage=stage,previous_response='private body')))]))
        assert meter.records[0]['repair'] and meter.records[0]['request_stage']==stage
        assert 'private body' not in json.dumps(meter.records)
