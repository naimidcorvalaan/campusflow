"""Finite document rehearsal responses; no network and no retained payloads."""
import json
from scripts.offline_product_model import OfflineProductModel
from scripts.material_demo_model import row, timing


class DocumentPreviewModel(OfflineProductModel):
    def __init__(self, rehearsal=''):
        super().__init__()
        self.rehearsal=rehearsal
        self.material_requests=[]

    def agent_caller(self, system, user):
        if self.rehearsal:
            import time
            time.sleep(2)  # finite offline delay for real waiting-state observation
        return super().agent_caller(system,user)

    def material_images_caller(self, system, user, images):
        self.calls.append('material_document_images')
        return self._response(system,user)[0]

    def _response(self, system, user):
        if 'campusflow.workload-estimate.v1' in system:
            request=json.loads(user)
            self.material_requests.append(dict(stage='workload_estimate',
                supplement=request.get('supplemental_context'),
                feature_count=sum(len(w['features']) for w in request['workloads']),
                no_formal_fields=not any(k in request for k in ('items','material','formal_item_format'))))
            # This rehearsal intentionally denies minutes in every model reply.
            # The product's independent workload engine must still produce time.
            if self.rehearsal=='workload_empty':
                return '{}','material_workload_empty'
            if self.rehearsal in ('intake_chain','workload_model'):
                from tests.test_workload_engine import estimate_response
                return estimate_response(request,4 if self.rehearsal=='intake_chain' else 43),'material_workload'
            return '{}','material_workload_empty'
        if 'campusflow.material-text.v2' not in system:
            return super()._response(system,user)
        if self.rehearsal:
            from tests.test_material_estimate_recovery import payload, action_payload, quality_payload
            data=payload(broken=self.rehearsal!='full')
            if self.rehearsal in ('workload_empty','workload_model'):
                request=json.loads(user)
                self.material_requests.append(dict(stage='material',supplement=request.get('supplemental_context'),
                    feature_count=sum(len(w['features']) for w in request['recognized_workload']),
                    source_present=bool(request['material'])))
                data=dict(schema_version='campusflow.material-text.v2',items=None,
                    actionability=dict(is_estimatable=False,reason='没有时间估计',evidence=''))
            elif self.rehearsal in ('workload', 'workload_partial'):
                request = json.loads(user.split('\n上次输出')[0])
                material = request['material']
                # Verify the real reader delivered the blank form's controls
                # and nested cells. Never select this response by filename.
                for field in ('姓名', '学号', '德', '智', '体', '美', '劳', '自我评价（约300字', '支撑材料名称'):
                    if field not in material:
                        raise ValueError('Synthetic form workload was not read')
                self.material_requests.append(dict(supplement=request.get('supplemental_context'),
                    stage=request.get('request_stage', 'initial'), source_present=True))
                data=action_payload()
                data['actionability'].update(task_name='准备并填写综合素质测评表',
                    short_scope='填写个人信息和五项评分，回顾经历写约300字自我评价，整理证明并复核。',
                    evidence='自我评价（约300字，结合本学年经历）',
                    reason='已读到基本信息、评分、主观文字与证明要求', confirmation_required=['identity'])
                data['estimate'].update(min_focus_minutes=35,max_focus_minutes=65,recommended_minutes=50,
                    basis='个人信息与评分录入、回忆经历及约300字写作、整理已有证明并复核。',
                    assumptions=['评分标准已知，证明可在已有记录中找到'],
                    clarification_question='未读部分是否还有其他评价要求？' if self.rehearsal=='workload_partial' else None)
                data['coverage']=dict(level='partial' if self.rehearsal=='workload_partial' else 'whole',
                    reason='依据实际读到的字段及主观文字要求',
                    uncovered_content='图片中的其他要求' if self.rehearsal=='workload_partial' else '')
            elif self.rehearsal=='intake_chain':
                request=json.loads(user.split('\n上次输出')[0])
                self.material_requests.append(dict(
                    supplement=request.get('supplemental_context'),
                    stage=request.get('request_stage','initial'),
                    source_present='德' in request.get('material','')))
                if request.get('request_stage')=='estimate_completion':
                    data=quality_payload()
                else:
                    # Equivalent failure: readable form, valid envelope, no
                    # complete formal work item and no independent estimate.
                    data=dict(schema_version='campusflow.material-text.v2',reference_date=None,
                        reference_evidence=None,items=[])
            elif self.rehearsal=='quality':
                data=quality_payload()
            elif self.rehearsal in ('action','action_broken'):
                data=action_payload()
                if self.rehearsal=='action_broken':
                    data['items']=None
            elif self.rehearsal=='needs_input':
                data['items']=[]
                data['actionability']=dict(is_estimatable=False,reason='只有学校背景介绍，没有要求处理的事项',evidence='学校简介')
            elif self.rehearsal=='exam':
                data=payload(False)
                data['items'][0].update(title='微积分试卷',evidence='Calculus exam',
                    scope='极限、导数与积分计算',completion='完成试题，保留步骤并检查')
                data['items'][0]['estimate'].update(min_focus_minutes=120,max_focus_minutes=180,recommended_minutes=150,
                    basis='按试题计算与检查工作量估算',assumptions=['具备相关课程基础'])
            if self.rehearsal not in ('quality','intake_chain','workload','workload_partial'):
                data['coverage']=dict(level='whole',reason='合成材料的工作范围和步骤已完整识别',uncovered_content='')
            return json.dumps(data,ensure_ascii=False),'material_rehearsal'
        data=json.loads(user.split('\n上次输出')[0])
        text=data['material']
        items=[]
        for number,label,title in ((1,'one','实验一报告'),(2,'two','实验二报告')):
            if 'Experiment '+label not in text and not (number==1 and data.get('attached_image_pages_in_order')):
                continue
            item=row(title,'Experiment '+label,scope='完成指定实验步骤并整理结果',completion='提交实验报告')
            item['estimate']=dict(min_focus_minutes=60,max_focus_minutes=90,recommended_minutes=75,
                basis='按实验步骤、检查和报告整理估算',assumptions=['仅估所选材料中的要求'],clarification_question=None)
            # Finite fixture dates, only when actually present in extracted text.
            day='2026-09-15' if number==1 else '2026-09-22'
            if day+' 22:00' in text:
                item['deadline']=timing(day+' 22:00',day,'22:00')
            items.append(item)
        return json.dumps(dict(schema_version='campusflow.material-text.v2',reference_date=None,
            reference_evidence=None,items=items),ensure_ascii=False),'material_document'


if __name__ == '__main__':
    from pathlib import Path
    from tests.document_fixtures import docx_bytes,pdf_bytes,TASK_TEXT,TASK_TWO
    target=Path('artifacts/document_material')
    target.mkdir(parents=True,exist_ok=True)
    for name,data in {
        'task.docx':docx_bytes(), 'text.pdf':pdf_bytes(),
        'scan.pdf':pdf_bytes((None,None)), 'mixed.pdf':pdf_bytes((TASK_TEXT,None)),
        'long-scan.pdf':pdf_bytes((None,)*6), 'old.doc':b'unsupported old format',
        'application.docx':docx_bytes('人文学术讲座学分申请表：填写姓名、学号、学院、讲座名称、日期及学分申请；核对证明材料。'),
        'exam.pdf':pdf_bytes(('Calculus exam: complete limits, derivatives and integrals, show calculations and check answers.',)),
        'reference.docx':docx_bytes('学校简介：校园始建于十九世纪，校训严谨治学。'),
        '本科生综合素质测评考核表【主观评价部分】.docx':docx_bytes('本科生综合素质测评考核表：填写德、智、体、美、劳各项分数。',complex_content=True),
    }.items():
        (target/name).write_bytes(data)
    from tests.document_fixtures import assessment_docx_bytes, reference_docx_bytes
    workload=target/'workload'
    workload.mkdir(exist_ok=True)
    for name,data in {'assessment.docx':assessment_docx_bytes(),
            'assessment-partial.docx':assessment_docx_bytes(True),
            'reference.docx':reference_docx_bytes()}.items():
        (workload/name).write_bytes(data)
    # Inspect the actual renderer output, not a static mock of a PDF page.
    from src.file_material import read_file_material,PDF_MIME
    rendered=read_file_material('scan.pdf',PDF_MIME,pdf_bytes((None,)),render=True)
    (target/'rendered-page.png').write_bytes(rendered.images[0][1])
    print('Synthetic document fixtures ready in artifacts/document_material')
