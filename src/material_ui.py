"""Unified task-material surface and explicit actions on the existing live page."""
import html
import hashlib
import logging
import json
from dataclasses import replace

from src.material_inbox import (MATERIAL_INBOX_KEY, MaterialInbox, MaterialError,
    update_source, update_image_source, update_file_source, extract_material, estimate_item, item_values, edited, estimate_basis,
    material_issues, issue_needs_decision, issue_user_override, unresolved_relative_fields,
    apply_notice_date)
from src.material_planning import plan_fingerprint, matching_context, build_material_candidate
from src.personal_settings import load_personal_settings
from src.file_material import (read_file_material, FileMaterialError, FILE_SOURCE_TYPES,
    MAX_DOCX_BYTES, MAX_PDF_BYTES)
from src.task_estimation import MAX_IMAGE_BYTES
from src.material_estimate_recovery import confirm_minimal_task, reestimate_only



def _display_fact(value):
    return value.strftime('%Y-%m-%d %H:%M') if hasattr(value,'strftime') else str(value)


def _render_estimate_card(st, draft, value, waiting='', show_coverage=True, rough=False):
    partial = draft.estimate_coverage != 'whole'
    label = '专注用时' if partial else '预计专注用时'
    if rough:
        label = '专注用时 · 初估'
    recommendation = '这部分建议预留' if partial else '建议预留'
    esc = lambda text: html.escape(str(text))
    # The local estimate's scope/waiting constraints have dedicated lines below.
    assumptions = '；'.join(value['assumptions'][1:2] if rough else value['assumptions'])
    waiting_html = ('' if rough else
        '<div class="cf-material-assumptions">另需等待：{}</div>'.format(esc(waiting)) if waiting else '')
    note = '仅估算已识别内容'
    st.markdown('<section class="cf-material-estimate"><h3>{}</h3>'
        '<div class="cf-material-scope">{}</div>'
        '<div class="cf-estimate-result"><div class="cf-estimate-metrics">'
        '<div class="cf-estimate-metric">{}<strong>{}–{} <small>分钟</small></strong></div>'
        '<div class="cf-estimate-metric">{}<strong>{} <small>分钟</small></strong></div>'
        '</div></div><details class="cf-estimate-details"><summary>估时依据</summary>'
        '<div class="cf-material-basis">{}</div>{}</details>{}{}</section>'.format(
            esc(value['task_name']),esc(value['short_scope']),label,value['focused_minutes_min'],
            value['focused_minutes_max'],recommendation,value['recommended_minutes'],esc(value['rationale']),
            '<div class="cf-material-assumptions">前提：{}</div>'.format(esc(assumptions)) if assumptions else '',
            waiting_html,
            '<div class="cf-material-coverage">{}</div>'.format(esc(note)) if partial and show_coverage else ''),
        unsafe_allow_html=True)


def _compact_text(value, limit=96):
    text = ' '.join(str(value or '').split())
    return text if len(text) <= limit else text[:limit - 1].rstrip('，,；;。 ') + '…'


def _decision_copy(item, issue, displayed_value):
    field = issue['field']
    title = item.title or '这项任务'
    labels = {
        'deadline': ('“{}”的截止时间按“{}”处理吗？', '是，按这个截止时间', '暂时不设置截止'),
        'location': ('“{}”需要按“{}”这个地点安排吗？', '是，按这个地点', '不限定执行地点'),
        'minutes': ('“{}”按“{}分钟”安排吗？', '是，按这个用时', '先不设置用时'),
        'start': ('“{}”的开始时间按“{}”处理吗？', '是，按这个开始时间', '暂时不采用这个时间'),
        'end': ('“{}”的结束时间按“{}”处理吗？', '是，按这个结束时间', '暂时不采用这个时间'),
    }
    template, use_label, omit_label = labels.get(
        field, ('“{}”按“{}”处理吗？', '是，采用这项信息', '暂时不采用')
    )
    return template.format(title, _display_fact(displayed_value)), use_label, omit_label


def save_material_edit(st, inbox, reference):
    from src import p2_live_main as live
    if st.session_state.get(MATERIAL_INBOX_KEY) == inbox:
        return True
    before = live._business_snapshot(st.session_state)
    st.session_state[MATERIAL_INBOX_KEY] = inbox
    return live._persist_after_mutation(st,before,reference=reference)


def handle_material_action(st, session, adapter, action, reference, item_id=None,
                           image_mime=None, image_bytes=None, file_source=None,
                           confirmed_title=None, confirmed_minutes=None, simple_confirmed=False,
                           estimate_supplement='', source_draft=None):
    """Real submit handler, callable offline. Model responses are the only mocks."""
    from src import p2_live_main as live
    inbox = st.session_state.get(MATERIAL_INBOX_KEY, MaterialInbox())
    draft = source_draft if source_draft is not None else inbox.draft
    if draft is None:
        st.warning('请先粘贴通知。')
        return False
    try:
        if action == 'extract':
            refreshed = replace(draft, plan_fingerprint=plan_fingerprint(session.store,session.campus_id),
                campus_id=session.campus_id, created_at=reference)
            updated = extract_material(
                refreshed,adapter.agent_caller,matching_context(session.store),
                image_caller=getattr(adapter,'task_estimation_caller',None),
                image_mime=image_mime,image_bytes=image_bytes,
                image_supported=bool(getattr(adapter,'supports_image_inputs',False)),
                default_user_context=load_personal_settings(session.store).estimation_context(),
                file_source=file_source, images_caller=getattr(adapter,'material_images_caller',None),
                workload_caller=getattr(adapter,'workload_estimation_caller',None),
            )
            if (source_draft is not None and inbox.draft
                    and (inbox.draft.estimate_fallbacks or any(item.minutes for item in inbox.draft.items))
                    and (not (updated.estimate_fallbacks or any(item.minutes for item in updated.items))
                        or (updated.diagnostics.get('estimate_origins')==['local_workload']
                            and inbox.draft.diagnostics.get('estimate_origins')!=['local_workload']))):
                raise MaterialError('估时未更新 · 原估算和补充已保留，请重试。')
            logging.getLogger(__name__).info('material_result %s',json.dumps(updated.diagnostics,sort_keys=True))
            return save_material_edit(st,replace(inbox,draft=updated),reference)
        if action == 'reference_date':
            return save_material_edit(st,apply_notice_date(inbox,item_id),reference)
        if action == 'prepare_estimate':
            updated = confirm_minimal_task(draft,item_id,confirmed_title,confirmed_minutes,simple_confirmed)
            return save_material_edit(st,replace(inbox,draft=updated),reference)
        if action == 'estimate_only':
            updated=reestimate_only(draft,item_id,adapter.agent_caller,estimate_supplement,
                load_personal_settings(session.store).estimation_context())
            return save_material_edit(st,replace(inbox,draft=updated),reference)
        if action == 'estimate':
            if draft.status != 'ready':
                raise MaterialError('材料已变化，请先重新整理。')
            row = next(i for i in draft.items if i.item_id == item_id)
            from src.p2_session import load_live_final_turn
            target = item_values(row,draft)['target']
            if target == 'choose':
                raise MaterialError('请先选择补充已有任务还是作为新任务，再估时。')
            bundle = load_live_final_turn(session.store)
            old = next((t for t in bundle.state.tasks if t.task_ref == target),None) if bundle else None
            updated = estimate_item(row,draft,adapter.agent_caller,
                load_personal_settings(session.store).estimation_context(),
                completed_minutes=old.completed_minutes if old else 0)
            return save_material_edit(st,replace(inbox,draft=replace(draft,
                items=tuple(updated if i.item_id == item_id else i for i in draft.items))),reference)
        if action == 'discard':
            return save_material_edit(st,replace(inbox,draft=replace(draft,status='discarded')),reference)
        if action != 'confirm':
            raise MaterialError('请使用草稿中的确认操作。')
        working, refs = build_material_candidate(session,inbox,reference)
        working[MATERIAL_INBOX_KEY] = replace(inbox,draft=replace(draft,status='confirmed'),
            receipts=dict(inbox.receipts, **{draft.source_fingerprint:list(refs)}))
        # Disk transaction first, then publish only business keys. A failed save
        # never exposes half a task batch or destroys an unadopted preview.
        live._persist_local_profile(working,reference=reference,campus_id=session.campus_id)
        for key in live._LOCAL_BUSINESS_KEYS + (live.LOCAL_PROFILE_REVISION_KEY,):
            if key in working:
                st.session_state[key] = working[key]
            else:
                st.session_state.pop(key,None)
        st.session_state[live.LOCAL_PROFILE_STALE_KEY] = False
        return True
    except MaterialError as exc:
        from src.llm_errors import VisionUnavailable
        if action == 'extract' and isinstance(exc.__cause__, VisionUnavailable):
            flow = st.session_state.get('cf_material_flow')
            if isinstance(flow, dict):
                flow['message'] = '图片识别暂不可用 · 材料已保留，请重试。'
        if action == 'extract' and source_draft is None:
            save_material_edit(st,replace(inbox,draft=replace(draft,status='failed',message=str(exc),
                diagnostics=dict(source_type=draft.source_type,parse_status='request_failed',
                    result_level='needs_input',repair_used=False,estimate_available=False,
                    task_count=0,safe_error_category='request_or_source_unavailable'))),reference)
        st.warning(str(exc))
    except Exception as exc:
        # Never expose raw model/SQL messages or materials to logs/the page.
        from src.local_persistence import LocalPersistenceError
        if isinstance(exc,LocalPersistenceError):
            st.warning(str(exc))
        else:
            messages = {'estimate':'估时未完成 · 材料和原方案已保留，请重试。',
                'confirm':'事项未加入 · 原方案和草稿已保留。'}
            st.warning(messages.get(action,'处理未完成 · 材料已保留，请重试。'))
    return False


def render_material_inbox(st, session, adapter, missing, reference):
    if not callable(getattr(st,'checkbox',None)):
        return False
    # Keep the expander and input footer at stable positions across reruns.
    # Clearing the entire surface also resets the user's disclosure state.
    return _render_material_surface(st,session,adapter,missing,reference)


def _render_material_surface(st, session, adapter, missing, reference):
    from src.workspace_ui import quiet_button
    from src import p2_live_main as live
    from src.p2_session import load_live_final_turn
    inbox = st.session_state.get(MATERIAL_INBOX_KEY,MaterialInbox())
    draft = inbox.draft
    flow=st.session_state.setdefault('cf_material_flow',dict(
        phase='result' if draft and (draft.items or draft.estimate_fallbacks) else 'input',
        supplement=draft.supplemental_context if draft else '',
        extra_open=st.session_state.pop('cf_material_extra_open',False),
        source_open=st.session_state.pop('cf_material_source_open',False),message=''))
    processing=flow['phase']=='processing'
    if processing and 'request' not in flow:
        flow.update(phase='error',message='处理已中断 · 材料已保留，请重试。')
        processing=False
    with st.container():
        st.markdown('<span class="cf-add-task-marker"></span>',unsafe_allow_html=True)
        st.markdown('<h1 class="cf-estimator-title">任务估时</h1>', unsafe_allow_html=True)
        ready = bool(draft and draft.status == 'ready' and (draft.items or draft.estimate_fallbacks))
        source_open = flow['source_open']
        compact = ready and not source_open
        if compact:
            st.markdown('<span class="cf-material-result-ready"></span>',unsafe_allow_html=True)
        uploaded = None
        file_source = None
        page_range = ''
        if callable(getattr(st,'file_uploader',None)):
            uploaded = st.file_uploader('上传任务材料',type=('jpg','jpeg','png','docx','pdf','doc'),key='cf_material_image',disabled=processing)
        is_document = uploaded is not None and uploaded.name.lower().endswith(('.docx','.pdf','.doc'))
        text = draft.original_text if draft else ''
        with st.container():
            st.markdown('<span class="{}"></span>'.format(
                'cf-material-source-hidden' if compact else 'cf-material-source-slot'),unsafe_allow_html=True)
            if uploaded is not None:
                limit = (MAX_PDF_BYTES if uploaded.name.lower().endswith('.pdf') else MAX_DOCX_BYTES) if is_document else MAX_IMAGE_BYTES
                if uploaded.size > limit:
                    st.warning('文件超过{}MB，请缩小后重试。'.format(limit // (1024*1024)))
                    return False
                if uploaded.name.lower().endswith('.pdf'):
                    page_range = st.text_input('要分析的页码（可选，留空读取整份）',
                        key='cf_material_pdf_pages_' + hashlib.sha256(uploaded.getvalue()).hexdigest()[:16],
                        placeholder='1-3, 7',disabled=processing)
            elif not (draft and draft.source_type != 'text'):
                st.session_state.setdefault('cf_material_text',text)
                text = st.text_area('任务、通知或说明',
                    key='cf_material_text',height=100,disabled=processing,
                    placeholder='完成高数第三章作业，或把老师发来的原通知粘贴在这里')
        # Allocate the result above its single optional-input footer. Widgets
        # still execute in source order and keep their native Streamlit state.
        result_area = st.container()
        # Keep this block mounted even before there is a result/spinner.
        # Otherwise Streamlit temporarily reconciles it with the old footer.
        result_area.markdown('<span class="cf-material-result-slot"></span>',unsafe_allow_html=True)
        if compact and uploaded is None and draft.source_name:
            result_area.caption(draft.source_name)
        if not processing and 'cf_material_supplement' in st.session_state:
            flow['supplement']=st.session_state['cf_material_supplement']
        supplement = flow['supplement']
        with st.container():
            actions = st.columns((1,1))
            if quiet_button(actions[0], '补充我的情况',key='cf_material_extra_toggle',disabled=processing):
                flow['extra_open'] = not flow['extra_open']
            if ready and quiet_button(actions[1], '更换材料' if not source_open else '收起材料',key='cf_material_source_toggle',disabled=processing):
                flow['source_open'] = not source_open
                live._rerun(st)
                return True
            extra_open = flow['extra_open']
            if extra_open:
                st.session_state.setdefault('cf_material_supplement',supplement)
                supplement = st.text_area('补充说明',key='cf_material_supplement',height=86,disabled=processing,
                    placeholder='例如：还要写一段自我评价；分数已经算好，只需抄入表中。')
                if not processing:
                    flow['supplement']=supplement
            recognize = False
            if not ready or source_open or extra_open:
                recognize = st.button('重新估算' if ready else '帮我看看',key='cf_material_extract',
                    type='secondary' if ready else 'primary',
                    disabled=processing or bool(missing) or (uploaded is None and not text.strip()))
                if ready and uploaded is None and draft.source_type != 'text':
                    st.caption('重新估算整份材料时，请先重新选择原文件。')
        notice_date = draft.notice_date if draft else ''
        # Typing a supplement keeps the visible estimate. Only an explicit
        # reestimate applies it; failure leaves the previous result untouched.
        context = supplement if recognize or not ready else draft.supplemental_context
        try:
            if processing:
                current = inbox
            elif is_document:
                file_source = read_file_material(uploaded.name,uploaded.type,uploaded.getvalue(),page_range)
                if not ready:
                    st.caption('已读取材料' + (' · 所选{}页'.format(len(file_source.selected_pages)) if file_source.page_count else ''))
                current = update_file_source(inbox,file_source,notice_date,reference,
                    plan_fingerprint(session.store,session.campus_id),session.campus_id,context,text)
            elif uploaded is not None:
                current = update_image_source(inbox,uploaded.name,uploaded.type,uploaded.getvalue(),notice_date,
                    reference,plan_fingerprint(session.store,session.campus_id),session.campus_id,context,
                    source_note=text)
            elif text.strip() and not (draft and draft.source_type != 'text'
                    and text.strip() == draft.original_text):
                current = update_source(inbox,text,notice_date,reference,
                    plan_fingerprint(session.store,session.campus_id),session.campus_id,context)
            else:
                current = inbox
        except (MaterialError, FileMaterialError) as exc:
            st.warning(str(exc))
            return False
        retry = ready and recognize
        if not retry:
            if current != inbox:
                if not save_material_edit(st,current,reference):
                    return False
                flow.update(phase='input',message='')
            inbox, draft = current, current.draft
        if draft is None:
            return False
        if draft.status in ('confirmed','discarded'):
            st.caption('已加入计划 · 可更换材料。' if draft.status == 'confirmed'
                else '草稿已丢弃 · 可更换材料。')
            return False
        notice = flow.get('message') or (draft.message if not draft.diagnostics.get('estimate_available') else '')
        if notice and not processing:
            with result_area:
                st.caption(notice)
        if recognize:
            # Freeze exactly the values submitted by this click. No model work
            # occurs until the next run has rendered the processing phase.
            flow.update(phase='processing',message='',request=dict(
                source_draft=current.draft,file_source=file_source,
                image_mime=uploaded.type if uploaded is not None else None,
                image_bytes=uploaded.getvalue() if uploaded is not None and not is_document else None,
                file_bytes=uploaded.getvalue() if file_source and file_source.vision_pages else None,
                page_range=page_range))
            live._rerun(st)
            return True
        if processing:
            submitted=flow.pop('request')  # consume once; an interrupted run never resubmits
            with result_area:
                with st.spinner('THINKING.......'):
                    submitted_source=submitted['file_source']
                    try:
                        if submitted_source is not None and submitted_source.vision_pages:
                            submitted_source=read_file_material(submitted_source.name,submitted_source.mime,
                                submitted['file_bytes'],submitted['page_range'],render=True)
                        ok = handle_material_action(st,session,adapter,'extract',reference,
                            image_mime=submitted['image_mime'],image_bytes=submitted['image_bytes'],
                            file_source=submitted_source,source_draft=submitted['source_draft'])
                    except FileMaterialError:
                        ok=False
            if ok:
                completed=st.session_state[MATERIAL_INBOX_KEY].draft
                flow.update(phase=completed.diagnostics.get('request_outcome','result'),
                    source_open=False,message='')
            else:
                flow.update(phase='result' if ready else 'error',message=flow.get('message') or (
                    '估时未更新 · 原估算和补充已保留，请重试。' if ready else
                    '还缺任务内容 · 请补充要做的事。'))
            # Preserve both the submitted supplement and the user's disclosure
            # choice. Finishing a request is not a request to close their editor.
            live._rerun(st)
            return True
        with result_area:
            if draft.status == 'ready' and draft.estimate_fallbacks:
                for index,entry in enumerate(draft.estimate_fallbacks):
                    value = entry['estimate']
                    key = 'cf_material_estimate_only_' + entry['item_id']
                    _render_estimate_card(st,draft,value,entry.get('waiting_note',''),show_coverage=index==0,
                        rough=entry.get('origin')=='local_workload')
                    if entry['simple_confirmation_allowed']:
                        if st.button('加入计划',key=key+'_edit'):
                            st.session_state[key+'_editing']=not st.session_state.get(key+'_editing',False)
                        if st.session_state.get(key+'_editing',False):
                            title = st.text_input('要做什么',value=value['task_name'],key=key+'_title')
                            minutes = st.number_input('采用分钟',min_value=1,max_value=1440,
                                value=value['recommended_minutes'],step=1,key=key+'_minutes')
                            consent = st.checkbox('这是新任务，不是固定安排，没有截止时间或指定执行地点',key=key+'_consent')
                            if st.button('按{}分钟准备加入'.format(minutes),key=key+'_prepare',disabled=not consent):
                                if handle_material_action(st,session,adapter,'prepare_estimate',reference,
                                        item_id=entry['item_id'],confirmed_title=title,confirmed_minutes=minutes,
                                        simple_confirmed=consent):
                                    live._rerun(st)
                                    return True
                            st.caption('下一步：核对任务后加入计划。')
            if draft.status != 'ready' or not draft.items:
                return False
            review_key = 'cf_material_review_' + draft.source_fingerprint
            if not st.session_state.get(review_key,False):
                for index,item in enumerate(draft.items):
                    if item.estimate_min_minutes is not None:
                        _render_estimate_card(st,draft,dict(task_name=item.title,short_scope=item.scope,
                            focused_minutes_min=item.estimate_min_minutes,focused_minutes_max=item.estimate_max_minutes,
                            recommended_minutes=item.minutes,rationale=item.estimate_basis,
                            assumptions=item.estimate_assumptions),item.estimate_waiting_note,
                            show_coverage=not draft.estimate_fallbacks and index==0)
                    else:
                        st.markdown('### ' + html.escape(item.title))
                        st.write(item.scope)
                        if item.minutes:
                            st.caption('预计专注用时：{}分钟'.format(item.minutes))
                if st.button('核对并加入计划',key=review_key+'_open'):
                    st.session_state[review_key]=True
                    live._rerun(st)
                    return True
                return False
            needs_reference = any(unresolved_relative_fields(item,draft) for item in draft.items)
            if needs_reference and not draft.reference_date:
                st.warning('请填写通知日期，以确定相对时间。')
                received = st.text_input('这条通知是什么时候收到的？',key='cf_material_notice_date',
                    placeholder='YYYY-MM-DD')
                if st.button('按这个日期确定时间',key='cf_material_reference_date',disabled=not received.strip()):
                    if handle_material_action(st,session,adapter,'reference_date',reference,received):
                        live._rerun(st)
                        return True
            st.markdown('**{} 项待确认**'.format(len(draft.items)))
            old_bundle = load_live_final_turn(session.store)
            existing = {t.task_ref:t for t in old_bundle.state.tasks} if old_bundle else {}
            estimate_ref = None
            rows = []
            for index,item in enumerate(draft.items,1):
                prefix = 'cf_material_' + item.item_id + '_' + str(draft.extraction_revision) + '_'
                v = item_values(item,draft)
                selected = st.checkbox('选择第{}项：{}'.format(index,v['title']),value=v['selected'],key=prefix+'selected')
                if not selected:
                    rows.append(edited(item,selected=False))
                    continue
                values = dict(selected=selected)
                st.caption('任务' if item.kind == 'task' else '固定安排')
                st.markdown('#### ' + v['title'])
                details = []
                full_details = ''
                if v['scope']:
                    details.append(v['scope'])
                if v['completion'] and v['completion'] != v['scope']:
                    details.append(v['completion'])
                if details:
                    full_details = ' · '.join(details)
                    st.markdown('<div class="cf-material-summary">{}</div>'.format(
                        html.escape(_compact_text(full_details))), unsafe_allow_html=True)
                if item.kind == 'task':
                    facts = []
                    deadline = v['deadline'] or (item.deadline.text + '（具体日期待确认）' if item.deadline.text else '')
                    if deadline:
                        facts.append('截止：' + deadline)
                    if v['location_text']:
                        facts.append('地点：' + v['location_text'])
                    facts.append('建议用于规划：{}'.format(
                        '{}分钟'.format(v['minutes']) if v['minutes'] else '还不知道'))
                    st.caption('  ·  '.join(facts))
                    if item.estimate_min_minutes is not None:
                        estimate_note = (item.estimate_assumptions[0] if item.estimate_assumptions
                            else item.estimate_basis)
                        st.markdown(
                            '<section class="cf-estimate-result"><div class="cf-estimate-metrics">'
                            '<div class="cf-estimate-metric">{}<strong>{}–{} <small>分钟</small></strong></div>'
                            '<div class="cf-estimate-metric">建议用于规划<strong>{} <small>分钟</small></strong></div>'
                            '</div>{}</section>'.format(
                                '专注用时' if draft.estimate_coverage!='whole' else '预计专注用时',
                                item.estimate_min_minutes,item.estimate_max_minutes,item.minutes,
                                '<div class="cf-estimate-note">{}</div>'.format(
                                    html.escape(_compact_text(estimate_note, 130))) if estimate_note else ''),
                            unsafe_allow_html=True)
                        if draft.estimate_coverage!='whole':
                            st.caption('仅估算已识别内容')
                        st.caption('估时依据：' + item.estimate_basis)
                        for assumption in item.estimate_assumptions:
                            st.caption('假设：' + assumption)
                    if item.estimate_waiting_note:
                        st.caption('外部等待（不计入专注用时）：' + item.estimate_waiting_note)
                    if full_details != _compact_text(full_details):
                        st.caption('完整范围可在“修改”中查看。')
                else:
                    start = v['start'] or (item.start.text + '（待确认）' if item.start.text else '待确认')
                    end = v['end'] or (item.end.text + '（待确认）' if item.end.text else '待确认')
                    st.caption('{} → {}{}'.format(start,end,
                        ' · '+v['location_text'] if v['location_text'] else ''))
                for issue in material_issues(item):
                    if issue_needs_decision(item,issue):
                        field = issue['field']
                        if issue_user_override(item,draft,issue):
                            st.caption('已按你修改后的{}处理。'.format(
                                {'identity':'事项内容','deadline':'截止时间','start':'开始时间','end':'结束时间',
                                 'location':'地点','minutes':'用时'}[field]))
                        elif field == 'identity':
                            st.warning('{} 请点“修改”写清楚要处理的事情。'.format(issue['message']))
                        else:
                            fact_key = 'location_text' if field == 'location' else field
                            if not v.get(fact_key):
                                st.warning('{} 可点“修改”填写准确值。'.format(issue['message']))
                                omit_copy = {
                                    'deadline':'暂时不设置截止时间', 'location':'不限定执行地点',
                                    'minutes':'先不设置用时', 'start':'暂时不采用开始时间',
                                    'end':'暂时不采用结束时间',
                                }.get(field, '暂时不采用这项信息')
                                omit = st.checkbox(omit_copy,
                                    value=v[field+'_decision']=='omit',key=prefix+field+'_omit')
                                values[field+'_decision'] = 'omit' if omit else 'choose'
                            else:
                                options = ('choose','use','omit')
                                question, use_label, omit_label = _decision_copy(item, issue, v[fact_key])
                                labels = {'choose':'还没有确认','use':use_label,'omit':omit_label}
                                values[field+'_decision'] = st.radio(question,options,
                                    index=options.index(v[field+'_decision']),key=prefix+field+'_decision',
                                    format_func=lambda x:labels[x],horizontal=True)
                    else:
                        st.caption('{}（不影响现在加入）'.format(issue['message']))
                if any(isinstance(raw,str) for raw in item.ambiguities):
                    values['reviewed'] = st.checkbox('这是旧版草稿，我已重新核对其中的不确定信息',
                        value=v['reviewed'],key=prefix+'legacy_reviewed')
                editing_key = prefix+'editing'
                editing = bool(st.session_state.get(editing_key,False))
                if st.button('收起修改' if editing else '修改',key=prefix+'edit_toggle'):
                    st.session_state[editing_key] = not editing
                    live._rerun(st)
                    return True
                if editing:
                    for key,label in (('title','名称'),('scope','范围'),('completion','完成标准')):
                        values[key] = st.text_input(label,value=v[key],key=prefix+key)
                    for key,label in ((('deadline','截止时间'),) if item.kind=='task' else (('start','开始时间'),('end','结束时间'))):
                        values[key] = st.text_input(label,value=v[key],key=prefix+key,
                            placeholder='YYYY-MM-DD HH:MM；不参与安排可留空')
                    values['location_text'] = st.text_input('执行地点（可选）',value=v['location_text'],key=prefix+'location')
                    if values['location_text']:
                        from src.p3_location_resolver import resolve_location
                        resolution = resolve_location(session.map_data,session._personal_location_text(values['location_text']))
                        if not resolution.usable:
                            st.warning('地点未匹配 · 请修改或选择校园地点。')
                            places = {node.id:node.name for node in session.map_data.nodes if node.node_kind == 'poi'}
                            selected_place = st.selectbox('选择校园地点',[None]+list(places),key=prefix+'place_choice',
                                format_func=lambda x: '暂不选择' if x is None else places[x])
                            if selected_place:
                                values['location_text'] = places[selected_place]
                    values['campus_id'] = st.selectbox('校区',('beiyangyuan','weijinlu'),
                        index=0 if v['campus_id']=='beiyangyuan' else 1,key=prefix+'campus',
                        format_func=lambda x:'北洋园' if x=='beiyangyuan' else '卫津路')
                if item.kind == 'task':
                    if editing:
                        values['minutes'] = st.text_input('采用分钟（可留空）',value=v['minutes'],
                            key=prefix+'minutes_'+str(item.estimate_revision))
                        st.caption('填写任务总用时')
                        values['supplement'] = st.text_input('有什么影响完成速度？（可选）',value=v['supplement'],key=prefix+'supplement')
                    if item.estimate:
                        r = item.estimate.result
                        if estimate_basis(dict(v,**values)) != item.estimate_basis_fingerprint:
                            st.warning('范围已变化 · 请重新估时或修改采用分钟。')
                        if r.ready:
                            if item.estimate_completed_minutes:
                                st.caption('剩余工作估时 · 已完成{}分钟已计入总用时。'.format(
                                    item.estimate_completed_minutes))
                            st.caption('专注用时 {}–{}分钟；建议{}分钟。{}'.format(
                                r.min_focus_minutes,r.max_focus_minutes,r.recommended_minutes,r.basis or ''))
                            for assumption in r.assumptions:
                                st.caption(assumption)
                            if r.adjustment_basis:
                                st.caption(r.adjustment_basis)
                        elif r.clarification_question:
                            st.warning(r.clarification_question)
                    if st.button('帮我估时' if not (item.estimate or item.estimate_min_minutes) else '重新估算',key=prefix+'estimate'):
                        estimate_ref = item.item_id
                    if item.possible_task_ref in existing:
                        st.warning('可能与“{}”重复 · 请选择如何处理。'.format(existing[item.possible_task_ref].title))
                    if item.possible_task_ref:
                        options = ['choose',item.possible_task_ref,'new']
                        target = v['target'] if v['target'] in options else 'choose'
                        values['target'] = st.selectbox('这项任务怎么加入？',options,index=options.index(target),key=prefix+'target',
                            format_func=lambda x: '还没有决定' if x=='choose' else '作为新任务' if x=='new' else '补充现有任务：'+existing[x].title)
                    else:
                        values['target'] = 'new'
                    if values['target'] in existing:
                        old = existing[values['target']]
                        binding = old_bundle.execution_context.binding_for(old.task_ref)
                        st.caption('已有任务：总用时{}；实际完成{}分钟。'.format(old.total_minutes or '未知',old.completed_minutes))
                        conflicts = []
                        if binding:
                            st.caption('已有范围：{}；已有截止：{}；已有地点：{}'.format(binding.scope_summary or '未记录',
                                binding.deadline_at or binding.latest_end_time or '未记录',
                                binding.execution_location.display_name if binding.execution_location else '未指定'))
                            for field,label,old_value,new_value in (
                                ('scope','范围',binding.scope_summary,values.get('scope',v['scope'])),
                                ('completion','完成标准',binding.completion_criteria,values.get('completion',v['completion'])),
                                ('deadline','截止',binding.deadline_at or binding.latest_end_time,values.get('deadline',v['deadline'])),
                                ('location','地点',binding.execution_location.display_name if binding.execution_location else None,
                                    values.get('location_text',v['location_text']))):
                                if old_value and new_value and _display_fact(old_value) != _display_fact(new_value):
                                    conflicts.append((field,label,old_value,new_value))
                        if old.total_minutes and values.get('minutes',v['minutes']) and str(old.total_minutes) != str(values.get('minutes',v['minutes'])):
                            conflicts.append(('minutes','总用时','{}分钟'.format(old.total_minutes),
                                '{}分钟'.format(values.get('minutes',v['minutes']))))
                        st.caption('新材料：范围{}；截止{}；地点{}。'.format(values.get('scope',v['scope']) or '未补充',
                            values.get('deadline',v['deadline']) or '未补充',values.get('location_text',v['location_text']) or '未补充'))
                        if conflicts:
                            st.warning('信息有冲突 · 请选择采用值。')
                            for field,label,old_value,new_value in conflicts:
                                choices=('choose','existing','new')
                                choice_labels={'choose':'还没有决定','existing':'保留已有：'+_display_fact(old_value),
                                    'new':'采用新材料：'+_display_fact(new_value)}
                                values[field+'_conflict'] = st.radio(label,choices,
                                    index=choices.index(v[field+'_conflict']),key=prefix+field+'_conflict',
                                    format_func=lambda x,labels=choice_labels:labels[x],horizontal=True)
                if editing:
                    st.caption('原文依据：'+item.evidence)
                    if item.estimate_basis:
                        st.caption('估算依据：' + item.estimate_basis)
                    for assumption in item.estimate_assumptions:
                        st.caption(assumption)
                rows.append(edited(item,**values))
                st.markdown('---')
            updated = replace(inbox,draft=replace(draft,items=tuple(rows)))
            if not save_material_edit(st,updated,reference):
                return False
            if estimate_ref:
                if missing:
                    st.warning('请连接模型服务 · 草稿已保留。')
                else:
                    with st.spinner('THINKING.......'):
                        ok = handle_material_action(st,session,adapter,'estimate',reference,estimate_ref)
                    if ok:
                        live._rerun(st)
                        return True
            confirm = st.button('确认并加入计划',key='cf_material_confirm',type='primary',disabled=not any(i.user_edits.get('selected',True) for i in rows))
            discard = st.button('丢弃这份草稿',key='cf_material_discard')
            if confirm or discard:
                if confirm and missing:
                    st.warning('请连接模型服务 · 草稿已保留。')
                else:
                    with st.spinner('THINKING.......'):
                        ok = handle_material_action(st,session,adapter,'confirm' if confirm else 'discard',reference)
                    if ok:
                        live._rerun(st)
                        return True
        return False
