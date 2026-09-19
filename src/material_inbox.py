"""Text material drafts. No planning, identity, or implicit model calls here."""
import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

from src.p2_agentic_parser import AgenticParseError
from src.material_formal_validation import extract_material_object as extract_json_object
from src.task_estimation import make_material, run_task_estimation
from src.file_material import FILE_SOURCE_TYPES, DOCX_MIME, PDF_MIME
from src import file_material as file_capacity
from src.estimate_arithmetic import ensure_estimate_arithmetic, ARITHMETIC_INSTRUCTIONS

MATERIAL_INBOX_KEY = 'material_inbox'
SCHEMA = 'campusflow.material-text.v2'
ISSUE_FIELDS = frozenset(('identity','scope','completion','deadline','start','end','location','minutes','general'))


class MaterialError(ValueError):
    pass


@dataclass(frozen=True)
class MaterialTime:
    text: str = ''
    date: Optional[str] = None
    clock: Optional[str] = None
    offset_days: Optional[int] = None
    week_offset: Optional[int] = None
    weekday: Optional[int] = None


@dataclass(frozen=True)
class MaterialItem:
    item_id: str
    kind: str
    title: str
    scope: str
    completion: str
    evidence: str
    deadline: MaterialTime = field(default_factory=MaterialTime)
    start: MaterialTime = field(default_factory=MaterialTime)
    end: MaterialTime = field(default_factory=MaterialTime)
    location_text: str = ''
    campus_id: Optional[str] = None
    commitment_kind: Optional[str] = None
    minutes: Optional[int] = None
    duration_source: Optional[str] = None
    # v2 stores bounded structured issue dictionaries; strings remain readable
    # for fail-closed recovery of drafts written by v1.
    ambiguities: Tuple[object, ...] = ()
    possible_task_ref: Optional[str] = None
    user_edits: dict = field(default_factory=dict)
    estimate: Optional[object] = None
    estimate_basis_fingerprint: Optional[str] = None
    estimate_revision: int = 0
    estimate_completed_minutes: int = 0
    estimate_min_minutes: Optional[int] = None
    estimate_max_minutes: Optional[int] = None
    estimate_basis: str = ''
    estimate_assumptions: Tuple[str, ...] = ()
    estimate_waiting_note: str = ''


@dataclass(frozen=True)
class MaterialDraft:
    original_text: str
    notice_date: str
    source_fingerprint: str
    reference_date: Optional[str]
    created_at: datetime
    plan_fingerprint: str
    campus_id: str
    items: Tuple[MaterialItem, ...] = ()
    status: str = 'unprocessed'
    message: str = ''
    model_calls: int = 0
    source_type: str = 'text'
    extraction_revision: int = 0
    source_name: str = ''
    source_mime: str = ''
    supplemental_context: str = ''
    source_basis: str = ''  # file digest + page selection hash only, never content
    estimate_fallbacks: tuple = ()  # bounded estimates only; never formal task facts
    diagnostics: dict = field(default_factory=dict)
    estimate_coverage: str = 'partial'  # presentation only; never a planning fact
    coverage_note: str = ''
    source_incomplete: bool = False
    workload_summary: tuple = ()  # independent counts/scope; never formal facts

    def __post_init__(self):
        if self.source_type not in ('text','image') + FILE_SOURCE_TYPES or self.status not in (
                'unprocessed','stale','ready','failed','confirmed','discarded'):
            raise ValueError('material draft invalid')
        if self.source_type == 'text':
            valid = {
                source_fingerprint(self.original_text,self.notice_date,self.supplemental_context),
                fingerprint(self.original_text.strip(),self.notice_date.strip()),  # saved v1/v2 drafts
            }
            if self.source_fingerprint not in valid:
                raise ValueError('material fingerprint invalid')
        elif (not re.fullmatch(r'[0-9a-f]{64}', self.source_fingerprint)
                or self.source_mime not in ({'image': ('image/jpeg','image/png'),
                    'docx': (DOCX_MIME,), 'pdf_text': (PDF_MIME,), 'pdf_vision': (PDF_MIME,)}[self.source_type])
                or not self.source_name or len(self.original_text) > (
                    file_capacity.MAX_TEXT_CHARS if self.source_type in FILE_SOURCE_TYPES else 12000)):
            raise ValueError('image material metadata invalid')


@dataclass(frozen=True)
class MaterialInbox:
    draft: Optional[MaterialDraft] = None
    # One receipt per explicitly confirmed source, including deselected items.
    # It is an idempotency ledger, not a second collection of tasks.
    receipts: dict = field(default_factory=dict)


def fingerprint(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True,
        default=str).encode('utf-8')).hexdigest()


def source_fingerprint(text, notice_date, supplemental_context=''):
    return fingerprint(text.strip(), notice_date.strip(), str(supplemental_context or '').strip())


def update_source(inbox, text, notice_date, now, plan_fingerprint, campus_id,
                  supplemental_context=''):
    text, notice_date = str(text or '').strip(), str(notice_date or '').strip()
    supplement = str(supplemental_context or '').strip()
    if len(text) > 12000 or len(notice_date) > 40 or len(supplement) > 1200:
        raise MaterialError('材料过长，请每次处理一份通知（最多12000字）。')
    fp = source_fingerprint(text, notice_date, supplement)
    old = inbox.draft
    if old and fp == old.source_fingerprint:
        return inbox
    draft = MaterialDraft(text, notice_date, fp, None, now, plan_fingerprint, campus_id,
        status='stale' if old and old.items else 'unprocessed',
        message='材料或补充情况已变化，请重新整理。' if old and old.items else '',
        supplemental_context=supplement)
    return replace(inbox, draft=draft)


def update_image_source(inbox, image_name, image_mime, image_bytes, notice_date,
                        now, plan_fingerprint, campus_id, supplemental_context='',
                        source_note=''):
    """Create image metadata only; raw bytes stay in the Streamlit upload state."""
    material = make_material(image_name=image_name, image_mime=image_mime,
        image_bytes=image_bytes)
    notice_date = str(notice_date or '').strip()
    supplement = str(supplemental_context or '').strip()
    source_note = str(source_note or '').strip()
    if len(notice_date) > 40 or len(supplement) > 1200 or len(source_note) > 12000:
        raise MaterialError('补充情况过长，请只保留影响这份任务的信息。')
    fp = fingerprint('image', material.fingerprint, notice_date, supplement, source_note)
    old = inbox.draft
    if old and old.source_type == 'image' and fp == old.source_fingerprint:
        return inbox
    draft = MaterialDraft(source_note, notice_date, fp, None, now, plan_fingerprint,
        campus_id, status='stale' if old and old.items else 'unprocessed',
        message='图片或补充情况已变化，请重新整理。' if old and old.items else '',
        source_type='image', source_name=str(image_name or '任务图片'),
        source_mime=material.image_mime or '', supplemental_context=supplement)
    return replace(inbox, draft=draft)


def update_file_source(inbox, source, notice_date, now, plan_fingerprint, campus_id,
                       supplemental_context='', source_note=''):
    """Only metadata/user note goes into the persistent inbox, never document text."""
    note, supplement, notice = (str(source_note or '').strip(),
        str(supplemental_context or '').strip(), str(notice_date or '').strip())
    if len(note) > 12000 or len(supplement) > 1200 or len(notice) > 40:
        raise MaterialError('补充情况过长，请缩小材料。')
    fp = fingerprint(source.source_type, source.fingerprint, notice, supplement, note)
    old = inbox.draft
    if old and old.source_fingerprint == fp:
        return inbox
    return replace(inbox, draft=MaterialDraft(note, notice, fp, None, now,
        plan_fingerprint, campus_id, source_type=source.source_type,
        source_name=source.name, source_mime=source.mime, supplemental_context=supplement,
        source_basis=source.fingerprint,
        status='stale' if old and old.items else 'unprocessed',
        message='文件、页码或补充情况已变化，请重新整理。' if old and old.items else ''))


def _has_text_evidence(draft):
    return draft.source_type in ('text', 'docx', 'pdf_text')


def iso_date(value):
    if not isinstance(value, str) or len(value) != 10:
        raise MaterialError('日期请填写 YYYY-MM-DD。')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise MaterialError('日期无效，请检查年月日。')


def resolved_time(spec, reference_date):
    """Arithmetic only. Copy time is never an implicit message reference."""
    if not spec.text:
        return None
    day = iso_date(spec.date) if spec.date else None
    if day is None and reference_date:
        ref = iso_date(reference_date)
        if spec.offset_days is not None:
            day = ref + timedelta(days=spec.offset_days)
        elif spec.week_offset is not None and spec.weekday is not None:
            day = ref - timedelta(days=ref.weekday()) + timedelta(
                weeks=spec.week_offset, days=spec.weekday - 1)
    if day is None or not spec.clock:
        return None
    try:
        return datetime.fromisoformat(day.isoformat() + 'T' + spec.clock)
    except ValueError:
        raise MaterialError('时间无效，请检查实际钟点。')


def _short(value, limit=500, optional=False):
    if optional and value is None:
        return ''
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError('invalid text')
    return value.strip()


def _time(raw, source, require_verbatim=True):
    if raw is None:
        return MaterialTime()
    from src.material_output_schema import TIME_REQUIRED
    keys = TIME_REQUIRED
    if not isinstance(raw, dict) or set(raw) != keys:
        raise ValueError('invalid time structure')
    text = _short(raw['text'], 200)
    if not text or (require_verbatim and text not in source):
        raise ValueError('time evidence missing')
    if raw['date'] is not None:
        d = iso_date(raw['date'])
        representations = (d.isoformat(), d.strftime('%Y/%m/%d'),
            '{}年{}月{}日'.format(d.year, d.month, d.day),
            '{}年{:02d}月{:02d}日'.format(d.year, d.month, d.day))
        if require_verbatim and not any(token in text for token in representations):
            raise ValueError('absolute date is not in evidence')
    if raw['clock'] is not None:
        datetime.strptime(raw['clock'], '%H:%M')
    for key, lo, hi in (('offset_days', -366, 366), ('week_offset', -52, 52), ('weekday', 1, 7)):
        value = raw[key]
        if value is not None and (type(value) is not int or not lo <= value <= hi):
            raise ValueError('invalid relative date')
    if (raw['week_offset'] is None) != (raw['weekday'] is None):
        raise ValueError('incomplete week relation')
    if sum((raw['date'] is not None, raw['offset_days'] is not None,
            raw['week_offset'] is not None)) > 1:
        raise ValueError('conflicting dates')
    return MaterialTime(**raw)


def parse_extraction(raw, draft, existing_refs):
    from src.material_formal_validation import fields, checked, failure
    from src.material_output_schema import MATERIAL_REQUIRED,MATERIAL_OPTIONAL,ESTIMATE_REQUIRED,LEGACY_ESTIMATE_OPTIONAL
    data = extract_json_object(raw)
    if isinstance(data,dict):
        # Independent estimate evidence is not a formal task fact. Old payloads
        # remain valid; recovery validates these optional fields separately.
        data = {k:v for k,v in data.items() if k not in MATERIAL_OPTIONAL}
    fields(data,MATERIAL_REQUIRED,'$')
    if data['schema_version'] != SCHEMA:
        failure('invalid_enum','$.schema_version','campusflow.material-text.v2',data['schema_version'])
    reference = draft.notice_date or None
    if reference:
        iso_date(reference)
    elif data['reference_date']:
        # Only explicit dated evidence from the material; never the UI clock.
        spec = checked('$.reference_date','source-evidenced ISO date',data['reference_date'],
            lambda: _time(dict(text=data['reference_evidence'], date=data['reference_date'],
                clock=None, offset_days=None, week_offset=None, weekday=None), draft.original_text,
                require_verbatim=_has_text_evidence(draft)))
        reference = spec.date
    values = data['items']
    if not isinstance(values, list) or len(values) > 12:
        failure('wrong_type' if not isinstance(values,list) else 'invalid_constraint','$.items','array of at most 12 items',values)
    items = []
    from src.material_output_schema import ITEM_REQUIRED,ITEM_KINDS,CAMPUS_IDS,COMMITMENT_KINDS
    required = ITEM_REQUIRED
    for index, raw_item in enumerate(values):
        path='$.items[{}]'.format(index)
        fields(raw_item,required,path,{'estimate'})
        title = checked(path+'.title','string <=100',raw_item['title'],lambda: _short(raw_item['title'],100))
        evidence = checked(path+'.evidence','string <=600',raw_item['evidence'],lambda: _short(raw_item['evidence'],600))
        if draft.source_type in FILE_SOURCE_TYPES and evidence and evidence not in draft.original_text:
            from src.material_evidence import canonical_source_quote
            canonical=canonical_source_quote(evidence,draft.original_text)
            if canonical is not None:
                from src.material_estimate_diagnostics import record
                record('material_recognition','item_evidence_layout','canonicalized_to_source',
                    field=path+'.evidence',parsed=True,valid=True)
                evidence=canonical
        if not title or not evidence or (_has_text_evidence(draft) and evidence not in draft.original_text):
            from src.material_evidence import workload_quote_matches
            from src.material_estimate_diagnostics import record
            record('material_recognition','item_evidence_layout',
                'layout_equivalent' if evidence and workload_quote_matches(evidence,draft.original_text) else 'not_layout_equivalent',
                field=path+'.evidence',parsed=True,valid=False)
            failure('evidence_mismatch',path+'.evidence','nonempty title and verbatim source evidence',raw_item.get('evidence'))
        if raw_item['kind'] not in ITEM_KINDS:
            failure('invalid_enum',path+'.kind','task or fixed_commitment',raw_item.get('kind'))
        if raw_item['campus_id'] not in CAMPUS_IDS:
            failure('invalid_enum',path+'.campus_id','null or known campus',raw_item.get('campus_id'))
        if raw_item['commitment_kind'] not in COMMITMENT_KINDS:
            failure('invalid_enum',path+'.commitment_kind','null, class, meeting or other',raw_item.get('commitment_kind'))
        minutes = raw_item['minutes']
        if minutes is not None and (type(minutes) is not int or not 1 <= minutes <= 10080
                or not raw_item['duration_evidence'] or (_has_text_evidence(draft)
                    and raw_item['duration_evidence'] not in draft.original_text)):
            failure('invalid_constraint',path+'.minutes','source-evidenced integer 1..10080',raw_item.get('minutes'))
        ambiguities = raw_item['uncertainties']
        if not isinstance(ambiguities, list) or len(ambiguities) > 6:
            failure('wrong_type',path+'.uncertainties','array of at most six uncertainties',raw_item.get('uncertainties'))
        issues = []
        for issue_index,issue in enumerate(ambiguities):
            issue_path=path+'.uncertainties[{}]'.format(issue_index)
            fields(issue,{'field','status','message','evidence'},issue_path)
            if issue['field'] not in ISSUE_FIELDS or issue['status'] not in ('uncertain','missing'):
                failure('invalid_enum',issue_path,'known uncertainty field and status',issue)
            evidence = _short(issue['evidence'], 240)
            if not evidence or (_has_text_evidence(draft) and evidence not in draft.original_text):
                failure('evidence_mismatch',issue_path+'.evidence','verbatim source evidence',issue['evidence'])
            issues.append(dict(field=issue['field'], status=issue['status'],
                message=_short(issue['message'], 240), evidence=evidence))
        candidate = raw_item['possible_task_ref']
        if candidate is not None and (not isinstance(candidate,str) or candidate not in existing_refs):
            failure('unknown_task_ref',path+'.possible_task_ref','provided stable task ref or null',raw_item.get('possible_task_ref'))
        estimate = raw_item.get('estimate')
        estimate_min = estimate_max = estimate_recommended = None
        estimate_basis_text = ''
        estimate_assumptions = ()
        if raw_item['kind'] == 'task' and estimate is not None:
            fields(estimate,ESTIMATE_REQUIRED,path+'.estimate',LEGACY_ESTIMATE_OPTIONAL)
            estimate_min, estimate_max = estimate['min_focus_minutes'], estimate['max_focus_minutes']
            estimate_recommended = estimate['recommended_minutes']
            numbers = (estimate_min, estimate_max, estimate_recommended)
            question = _short(estimate['clarification_question'], 300, True)
            if any(value is not None for value in numbers):
                if any(type(value) is not int or not 1 <= value <= 10080 for value in numbers):
                    failure('invalid_constraint',path+'.estimate','integer duration fields 1..10080',raw_item.get('estimate'))
                if not estimate_min <= estimate_recommended <= estimate_max or question:
                    failure('invalid_constraint',path+'.estimate','min <= suggested <= max without clarification',raw_item.get('estimate'))
                estimate_basis_text = _short(estimate['basis'], 400)
                assumptions = estimate['assumptions'] or []
                if not isinstance(assumptions, list) or len(assumptions) > 6:
                    failure('wrong_type',path+'.estimate.assumptions','array of at most six strings',raw_item.get('estimate'))
                estimate_assumptions = tuple(_short(value, 200) for value in assumptions)
                if minutes is None:
                    from src.material_effort import ensure_effort,sources_for_draft,render_ledger
                    ensure_effort(estimate_recommended,estimate_min,estimate_max,estimate_basis_text,estimate_assumptions,
                        ledger=estimate.get('effort_ledger'),sources=sources_for_draft(draft),stage='material_recognition')
                    if estimate.get('effort_ledger'):
                        estimate_basis_text+='；'+render_ledger(estimate['effort_ledger'])
                        if len(estimate_basis_text)>400:
                            failure('invalid_constraint',path+'.estimate.basis','rendered basis <=400',estimate_basis_text)
            elif not question:
                failure('missing_field',path+'.estimate.clarification_question','question when minutes unavailable',raw_item.get('estimate'))
            if question and minutes is None:
                issues.append(dict(field='minutes',status='missing',message=question,evidence=evidence))
        # A duration explicitly written in the material is already the authoritative
        # planning fact.  Do not put a second AI estimate beside it and imply that
        # the model overruled the source.
        if minutes is not None:
            estimate_min = estimate_max = estimate_recommended = None
            estimate_basis_text = ''
            estimate_assumptions = ()
        effective_minutes = minutes if minutes is not None else estimate_recommended
        duration_source = ('material_explicit' if minutes is not None else
            'ai_estimated' if estimate_recommended is not None else None)
        scope_text = checked(path+'.scope','string <=500',raw_item['scope'],lambda: _short(raw_item['scope']))
        completion_text = checked(path+'.completion','string <=500',raw_item['completion'],lambda: _short(raw_item['completion']))
        quick_basis = fingerprint(title,scope_text,completion_text,draft.supplemental_context,
            'choose' if candidate else 'new') if estimate_recommended is not None else None
        items.append(MaterialItem(
            fingerprint(draft.source_fingerprint, index)[:24], raw_item['kind'], title,
            scope_text, completion_text, evidence,
            checked(path+'.deadline','source-evidenced MaterialTime or null',raw_item['deadline'],lambda: _time(raw_item['deadline'], draft.original_text, _has_text_evidence(draft))),
            checked(path+'.start','source-evidenced MaterialTime or null',raw_item['start'],lambda: _time(raw_item['start'], draft.original_text, _has_text_evidence(draft))),
            checked(path+'.end','source-evidenced MaterialTime or null',raw_item['end'],lambda: _time(raw_item['end'], draft.original_text, _has_text_evidence(draft))),
            checked(path+'.location_text','string <=200 or null',raw_item['location_text'],lambda: _short(raw_item['location_text'],200,True)), raw_item['campus_id'],
            raw_item['commitment_kind'], effective_minutes, duration_source,
            tuple(issues), candidate, estimate_basis_fingerprint=quick_basis,
            estimate_min_minutes=estimate_min,
            estimate_max_minutes=estimate_max, estimate_basis=estimate_basis_text,
            estimate_assumptions=estimate_assumptions))
    return replace(draft, reference_date=reference, items=tuple(items), status='ready',
        estimate_fallbacks=(), diagnostics={},
        extraction_revision=draft.extraction_revision + 1,
        message='' if items else '没有识别到需要安排的事情。补一句你准备做什么，我再帮你估。')


def extract_material(draft, caller, existing_tasks=(), image_caller=None,
                     image_mime=None, image_bytes=None, image_supported=False,
                     default_user_context='', file_source=None, images_caller=None, workload_caller=None):
    original_note = draft.original_text
    if draft.source_type in FILE_SOURCE_TYPES:
        if file_source is None:
            raise MaterialError('原文件不保存在档案中，请重新选择文件后再整理。')
        expected = fingerprint(file_source.source_type, file_source.fingerprint,
            draft.notice_date, draft.supplemental_context, original_note)
        if expected != draft.source_fingerprint:
            raise MaterialError('文件或页码已变化，请重新整理，不能采用旧结果。')
        if file_source.vision_pages and len(file_source.images) != len(file_source.vision_pages):
            raise MaterialError('PDF页面尚未准备完成，请重新选择文件。')
        document_text = file_source.text + ('\n用户补充：' + original_note if original_note else '')
        if len(document_text) > file_capacity.MAX_TEXT_CHARS:
            raise MaterialError('文件文字与补充合计超过{}字，请缩小材料。'.format(file_capacity.MAX_TEXT_CHARS))
        draft = replace(draft, original_text=document_text)
    if draft.source_type == 'text' and not draft.original_text:
        raise MaterialError('请先输入一件任务、通知或说明。')
    if draft.source_type == 'image' and image_bytes is None:
        raise MaterialError('原图未保存在档案中，请重新选择图片后再整理。')
    if draft.notice_date:
        iso_date(draft.notice_date)
    from src.material_workload import source_workload, workload_format
    local_work=source_workload(file_source.text if file_source else draft.original_text,
        draft.supplemental_context+' '+original_note)
    from src.material_scope import numbered_scope
    required_scope=numbered_scope(file_source.text if file_source else draft.original_text,
        draft.supplemental_context+' '+original_note)
    from src.material_recognition_prompts import recognition_system_prompt
    system = recognition_system_prompt()
    system += ARITHMETIC_INSTRUCTIONS
    from src.material_output_schema import estimate_example
    from src.material_output_schema import recognition_template,formal_item_template
    user = json.dumps({'material':draft.original_text if draft.source_type != 'image' else '[任务图片见本条消息]',
        'material_kind':draft.source_type,
        'entry_intent':'估计完成、填写或处理所上传材料的专注用时；可填写表单本身支持填写意图，但不是今天提交的承诺',
        'source_name':draft.source_name or None,
        'selected_pages':file_source.selected_pages if file_source else None,
        'attached_image_pages_in_order':file_source.vision_pages if file_source else None,
        'source_limits':file_source.warnings if file_source else None,
        'user_note_for_image':draft.original_text if draft.source_type == 'image' and draft.original_text else None,
        'user_confirmed_notice_date':draft.notice_date or None,
        'supplemental_context':draft.supplemental_context or None,
        'saved_default_context':str(default_user_context or '').strip() or None,
        'recognized_workload':[work.context() for work in local_work],
        'required_scope':required_scope.context() if required_scope else None,
        'existing_tasks':existing_tasks,'output':recognition_template(),
        'formal_item_format':formal_item_template()},ensure_ascii=False)
    vision_unavailable = False
    def request(request_system, request_user):
        nonlocal vision_unavailable
        if vision_unavailable:
            payload = json.loads(request_user)
            payload['attached_image_pages_in_order'] = []
            payload['source_limits'] = list(payload.get('source_limits') or []) + [
                '图片理解服务暂不可用；仅处理已读取文字，图片页未纳入。']
            return caller(request_system, json.dumps(payload, ensure_ascii=False))
        if file_source is not None and file_source.images:
            if not image_supported or not callable(images_caller):
                raise MaterialError('当前服务不能读取这份PDF的图片页，请改用文字材料。')
            from src.llm_errors import VisionUnavailable
            try:
                return images_caller(request_system, request_user, file_source.images)
            except VisionUnavailable:
                vision_unavailable = True
                raise
        elif draft.source_type == 'image':
            if not image_supported or not callable(image_caller):
                raise MaterialError('图片理解暂不可用，请改用文字输入。')
            return image_caller(request_system, request_user, image_mime, image_bytes)
        return caller(request_system, request_user)

    from src.material_intake_pipeline import run_material_intake
    return run_material_intake(draft,request,caller,system,user,
        {x['task_ref'] for x in existing_tasks},file_source,original_note,workload_caller)



def edited(item, **changes):
    allowed = {'title','scope','completion','deadline','start','end','location_text','minutes',
        'selected','target','reviewed','supplement','campus_id','deadline_decision',
        'start_decision','end_decision','location_decision','minutes_decision',
        'scope_conflict','completion_conflict','deadline_conflict','location_conflict',
        'minutes_conflict'}
    if set(changes) - allowed or any(not isinstance(x, (str, bool)) for x in changes.values()):
        raise MaterialError('草稿修改无效。')
    return replace(item, user_edits=dict(item.user_edits, **changes))


def item_values(item, draft):
    values = dict(title=item.title, scope=item.scope, completion=item.completion,
        location_text=item.location_text, minutes=str(item.minutes or ''),
        selected=True, target='choose' if item.possible_task_ref else 'new', reviewed=False,
        supplement=draft.supplemental_context, campus_id=item.campus_id or draft.campus_id,
        deadline_decision='choose', start_decision='choose', end_decision='choose',
        location_decision='choose', minutes_decision='choose', scope_conflict='choose',
        completion_conflict='choose',deadline_conflict='choose',location_conflict='choose',
        minutes_conflict='choose')
    for key in ('deadline','start','end'):
        moment = resolved_time(getattr(item,key), draft.reference_date)
        values[key] = moment.strftime('%Y-%m-%d %H:%M') if moment else ''
    values.update(item.user_edits)
    return values


def estimate_basis(values):
    return fingerprint(values['title'], values['scope'], values['completion'], values['supplement'], values['target'])


def estimate_item(item, draft, caller, default_context='', completed_minutes=0):
    values = item_values(item, draft)
    text = '{}\n范围：{}\n完成标准：{}'.format(values['title'], values['scope'], values['completion'])
    if completed_minutes:
        text += ('\n实际已投入{}分钟（不是计划时长）。本次只估计完成所确认范围的剩余专注工作，'
            '不再计入已完成部分；不能仅凭已投入分钟推断哪些题已完成。'
            '若缺少已完成范围会明显影响估计，可提出一个问题。').format(completed_minutes)
    material = make_material(text)
    result = run_task_estimation('material-' + item.item_id, material, values['supplement'], caller,
        confirmed_scope=values['scope'], confirmed_completion=values['completion'],
        previous_result=item.estimate.result if item.estimate else None,
        default_user_context=default_context).draft
    changes = dict(item.user_edits)
    if result.result.ready:
        changes['minutes'] = str(result.adopted_minutes + completed_minutes)
    return replace(item, estimate=result, estimate_basis_fingerprint=estimate_basis(values),
        estimate_revision=item.estimate_revision + 1, estimate_completed_minutes=completed_minutes,
        user_edits=changes)


def estimated_total(item):
    return item.estimate.adopted_minutes + item.estimate_completed_minutes


def material_issues(item):
    """Normalize current structured issues and old saved string ambiguities."""
    result = []
    for raw in item.ambiguities:
        if isinstance(raw, dict) and set(raw) == {'field','status','message','evidence'}:
            result.append(raw)
        elif isinstance(raw, str):
            result.append(dict(field='general',status='uncertain',message=raw,evidence=item.evidence))
    return tuple(result)


def issue_needs_decision(item, issue):
    """Only uncertain facts that would become scheduling constraints block."""
    field = issue['field']
    if field == 'identity':
        return True
    if field == 'deadline':
        return bool(item.deadline.text)
    if field == 'minutes':
        return item.minutes is not None
    if field == 'location':
        return bool(item.location_text)
    if item.kind == 'fixed_commitment' and field in ('start','end'):
        return bool(getattr(item,field).text)
    return False


def issue_user_override(item, draft, issue):
    field = issue['field']
    if field == 'identity':
        return any(key in item.user_edits and str(item.user_edits[key]).strip() and
            str(item.user_edits[key]).strip() != getattr(item,key)
            for key in ('title','scope'))
    key = 'location_text' if field == 'location' else field
    if key not in item.user_edits:
        return False
    if field in ('deadline','start','end'):
        moment = resolved_time(getattr(item,field),draft.reference_date)
        original = moment.strftime('%Y-%m-%d %H:%M') if moment else ''
    elif field == 'minutes':
        original = str(item.minutes or '')
    elif field == 'location':
        original = item.location_text
    else:
        return False
    return str(item.user_edits[key]).strip() != original


def unresolved_relative_fields(item, draft):
    result = []
    for field in ('deadline','start','end'):
        spec = getattr(item,field)
        has_reference_relation = spec.offset_days is not None or spec.week_offset is not None
        if spec.text and has_reference_relation and resolved_time(spec,draft.reference_date) is None:
            result.append(field)
    return tuple(result)


def apply_notice_date(inbox, value):
    """Attach a user-confirmed message date without another model call."""
    if inbox.draft is None or inbox.draft.status != 'ready':
        raise MaterialError('请先整理材料，再补通知日期。')
    confirmed = iso_date(str(value or '').strip()).isoformat()
    draft = inbox.draft
    items = []
    for item in draft.items:
        remaining = []
        for raw in item.ambiguities:
            # A missing calendar anchor is resolved by this explicit date only
            # when the extracted time now becomes a complete deterministic fact.
            # Semantic uncertainty (for example "好像") deliberately remains.
            if isinstance(raw,dict) and raw.get('status') == 'missing':
                field = raw.get('field')
                if field in ('deadline','start','end') and resolved_time(
                        getattr(item,field),confirmed) is not None:
                    continue
            remaining.append(raw)
        items.append(replace(item,ambiguities=tuple(remaining)))
    updated = replace(draft, notice_date=confirmed,
        source_fingerprint=(source_fingerprint(draft.original_text,confirmed,draft.supplemental_context)
            if draft.source_type == 'text' else
            fingerprint(draft.source_type,draft.source_basis,confirmed,draft.supplemental_context,draft.original_text)
            if draft.source_type in FILE_SOURCE_TYPES else fingerprint(draft.source_fingerprint,confirmed)),
        reference_date=confirmed, extraction_revision=draft.extraction_revision + 1,
        items=tuple(items),
        message='已按通知日期重新确定相对时间；其他修改均已保留。')
    return replace(inbox,draft=updated)


def checked_item(item, draft):
    v = item_values(item, draft)
    if not v['selected']:
        return None
    if not v['title'].strip():
        raise MaterialError('选中事项需要名称。')
    if any(isinstance(raw,str) for raw in item.ambiguities) and not v['reviewed']:
        raise MaterialError('{}：这是旧版草稿，请重新核对标出的不确定信息。'.format(v['title']))
    for issue in material_issues(item):
        if issue_needs_decision(item,issue):
            if issue['field'] == 'identity':
                if not issue_user_override(item,draft,issue):
                    raise MaterialError('{}：请先写清楚要处理的事情。'.format(v['title']))
                continue
            key = issue['field'] + '_decision'
            decision = 'use' if issue_user_override(item,draft,issue) else v.get(key,'choose')
            if decision == 'choose':
                raise MaterialError('{}：请确认“{}”。'.format(v['title'],issue['message']))
            if decision == 'omit':
                if issue['field'] == 'location':
                    v['location_text'] = ''
                elif issue['field'] == 'minutes':
                    v['minutes'] = ''
                else:
                    v[issue['field']] = ''
    if v['target'] == 'choose':
        raise MaterialError('{}：请选择补充已有任务还是作为新任务。'.format(v['title']))
    if v['target'] != 'new':
        for field,key in (('scope','scope'),('completion','completion'),('deadline','deadline'),
                          ('location','location_text'),('minutes','minutes')):
            if v.get(field+'_conflict') == 'existing':
                v[key] = ''
    estimate_value = (str(estimated_total(item)) if item.estimate and item.estimate.result.ready
        else str(item.minutes or '') if item.estimate_min_minutes is not None else None)
    if (estimate_value is not None and v['minutes'] == estimate_value
            and estimate_basis(v) != item.estimate_basis_fingerprint):
        raise MaterialError('{}：范围或补充已变化，请重新估时或清空暂估分钟。'.format(v['title']))
    minutes = v['minutes'].strip()
    if minutes and (not minutes.isdigit() or not 1 <= int(minutes) <= 10080):
        raise MaterialError('{}：用时请填1—10080的整数分钟，或留空待估算。'.format(v['title']))
    v['minutes'] = int(minutes) if minutes else None
    v['duration_source'] = None
    if v['minutes'] is not None:
        if item.estimate and item.estimate.result.ready and v['minutes'] == estimated_total(item):
            v['duration_source'] = 'ai_estimated'
        elif v['minutes'] == item.minutes:
            v['duration_source'] = item.duration_source
        else:
            v['duration_source'] = 'user_confirmed'
    for key in ('deadline','start','end'):
        raw = v[key].strip()
        if raw:
            try:
                value = datetime.strptime(raw, '%Y-%m-%d %H:%M')
            except ValueError:
                raise MaterialError('{}：{}请填 YYYY-MM-DD HH:MM。'.format(v['title'],
                    {'deadline':'截止时间','start':'开始时间','end':'结束时间'}[key]))
            v[key] = value
        elif (getattr(item,key).text and v.get(key+'_decision') != 'omit'
              and v.get(key+'_conflict') != 'existing'):
            raise MaterialError('{}：{}的具体日期/钟点待确认。'.format(v['title'],getattr(item,key).text))
        else:
            v[key] = None
    if item.kind == 'fixed_commitment':
        if v['start'] is not None and v['end'] is None and v['minutes']:
            v['end'] = v['start'] + timedelta(minutes=v['minutes'])
        if v['start'] is None or v['end'] is None or v['start'] >= v['end']:
            raise MaterialError('{}：固定安排需要有效起止时间。'.format(v['title']))
        if v['minutes'] and int((v['end'] - v['start']).total_seconds() // 60) != v['minutes']:
            raise MaterialError('{}：固定安排起止与时长冲突，请确认。'.format(v['title']))
    return v
