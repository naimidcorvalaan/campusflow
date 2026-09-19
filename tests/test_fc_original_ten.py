import json
from datetime import datetime
from scripts.fc_original_ten import gold_rows,audit_candidate,TEXTS
from src.material_inbox import MaterialInbox,update_source
from src.material_actionability_sources import source_spans
from src.material_function_transport import ToolArguments


def candidate(index=0):
    gold=gold_rows()[index]
    draft=update_source(MaterialInbox(),gold['text'],'',datetime(2026,9,16,14),'plan','beiyangyuan').draft
    value=dict(schema_version='campusflow.material-text.v2',reference_date=None,reference_evidence=None,items=[],
        actionability=dict(is_estimatable=True,reason='明确任务',evidence_refs=[source_spans(draft)[0]['ref']],short_scope=gold['text']),
        workload=[dict(task_name=gold['label'],scope=gold['text'],features=[dict(kind=g[0],units=1,evidence=gold['text']) for g in gold['expected_feature_groups']])])
    return draft,gold,value


def test_gold_fixed_original_ten_not_model_generated():
    gold=gold_rows();assert len(gold)==10
    assert [g['text'] for g in gold]==TEXTS
    assert all(g['expected_is_estimatable'] for g in gold)


def test_full_business_and_compound_source_guard_execute():
    for index in range(10):
        draft,gold,value=candidate(index)
        row=audit_candidate(ToolArguments(json.dumps(value)),draft,gold)
        assert row['business_pass'],(index,row)
        assert row['actionability_executed'] and row['verified_evidence']


def test_legal_enum_is_not_automatically_semantically_correct():
    draft,gold,value=candidate()
    value['workload'][0]['features'][0]['kind']='score'
    row=audit_candidate(ToolArguments(json.dumps(value)),draft,gold)
    assert row['schema_valid'] and not row['business_pass'] and row['primary_failure_layer']=='enum semantics'


def test_invalid_enum_still_formal_error_and_unknown_ref_rejected():
    draft,gold,value=candidate();value['workload'][0]['features'][0]['kind']='writing'
    row=audit_candidate(ToolArguments(json.dumps(value)),draft,gold)
    assert not row['schema_valid'] and row['formal_failure']['code']=='invalid_enum'
    draft,gold,value=candidate();value['actionability']['evidence_refs']=['foreign']
    row=audit_candidate(ToolArguments(json.dumps(value)),draft,gold)
    assert row['schema_valid'] and not row['verified_evidence'] and row['primary_failure_layer']=='evidence'


def test_false_actionability_not_counted_as_gold_success():
    draft,gold,value=candidate();value['actionability']['is_estimatable']=False;value['actionability']['evidence_refs']=[]
    row=audit_candidate(ToolArguments(json.dumps(value)),draft,gold)
    assert row['schema_valid'] and row['primary_failure_layer']=='actionability semantics'
