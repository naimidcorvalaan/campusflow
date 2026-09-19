import json
from scripts.material_capacity_report import summarize


def test_material_report_uses_frozen_per_document_requirements(tmp_path):
    batch=tmp_path/'batch';path=batch/'report';path.mkdir(parents=True)
    gold=dict(material_id='report',minimum_pages=2,maximum_pages=3,discussion_words=400)
    (batch/'materials.json').write_text(json.dumps([gold]),encoding='utf-8')
    entry=dict(origin='model_workload',adoption_path='normal',
        estimate=dict(short_scope='第一部分 第二部分 第三部分 第四部分 第五部分',
                      recommended_minutes=60,focused_minutes_min=50,focused_minutes_max=70),
        quantity_claims=[dict(owner='deliverable_body',metric='page_count',qualifier='range',minimum=2,maximum=3),
                        dict(owner='subtask_discussion',metric='word_count',qualifier='approximately',minimum=400)],
        final_contract={'material_facts':{'page_count':{'value':3}}},final_contract_seal='verified',
        final_validation_errors=[],confirmation_fields=[])
    row=dict(material_id='report',file_type='pdf',pages=3,extracted_text_chars=3000,file_bytes=1200,errors=[],
             phases=[dict(phase='initial',handler_success=True,result={'estimates':[entry]})])
    (path/'results.json').write_text(json.dumps(row),encoding='utf-8')
    (path/'requests.json').write_text('[]',encoding='utf-8')
    report=summarize(tmp_path)
    assert report['materials'][0]['phases'][0]['entries'][0]['requirements_preserved']
    entry['quantity_claims'][1]['qualifier']='minimum'
    (path/'results.json').write_text(json.dumps(row),encoding='utf-8')
    assert not summarize(tmp_path)['materials'][0]['phases'][0]['entries'][0]['requirements_preserved']
    entry['origin']='local_workload'
    row['phases'].append(dict(phase='reestimate',handler_success=False,result={'estimates':[entry]}))
    (path/'results.json').write_text(json.dumps(row),encoding='utf-8')
    assert summarize(tmp_path)['materials'][0]['fallback']==1
