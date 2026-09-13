"""P1j 的薄渲染器：只消费 P1ResultView。"""
def render_view(st, view, on_option=None):
    card = view.primary_card
    # 一个时刻只展示一个“当前方案”：主标题 + headline + 方案细节。
    st.subheader(view.status_title)
    st.write(view.headline)
    if card is not None:
        if card.rationale:
            st.write(card.rationale)
        for step in card.steps:
            st.write("%s：%d 分钟（%s）" % (step.title, step.planned_minutes, step.environment_label))
            if step.timing_note:
                st.write(step.timing_note)
        if card.total_walking_minutes is not None:
            st.write("步行：%d 分钟" % card.total_walking_minutes)
        if card.expected_finish_text:
            st.write("预计完成或到达：%s" % card.expected_finish_text)
        for segment in card.route_segments:
            st.write("%s → %s：%d 分钟" % (segment.start_name, segment.end_name, segment.walking_minutes))
        if card.total_task_minutes is not None:
            st.write("总任务：%d 分钟" % card.total_task_minutes)
        if card.free_window_task_minutes is not None:
            st.write("当前空档任务：%d 分钟" % card.free_window_task_minutes)
    # 方案之后才是选择/路线可信度提示。
    for notice in (view.selection_notice, view.route_data_notice):
        if notice is not None:
            (st.warning if notice.level == "warning" else st.info)(notice.message)
    # 先给方案，再问确认。
    if view.primary_question is not None:
        st.markdown("### 还需要确认")
        st.write(view.primary_question.text)
        for index, option in enumerate(view.primary_question.quick_options):
            if st.button(option.label, key="p1_option_%d_%s" % (index, option.action)) and on_option:
                on_option(option)
    if view.alternative_summary:
        st.write(view.alternative_summary)
    for index, option in enumerate(view.quick_actions):
        if st.button(option.label, key="p1_action_%d_%s" % (index, option.action)) and on_option:
            on_option(option)
    if view.show_details or view.tentative_notice is not None:
        with st.expander("查看详情"):
            if view.tentative_notice is not None:
                (st.warning if view.tentative_notice.level == "warning" else st.info)(view.tentative_notice.message)
            if card is not None:
                if card.rationale: st.write(card.rationale)
                for item in card.assumptions: st.write(item)
                for item in card.warnings: st.write(item)
            for question in view.other_questions:
                st.write(question)
            for note in view.source_notes:
                st.write(note)
    if view.diagnostics and card is None:
        with st.expander("安全诊断信息"):
            for item in view.diagnostics:
                text = "%s：%s，尝试%d次" % (
                    item.stage_label, item.status_label, item.attempts)
                if item.error_category_label:
                    text += "，安全错误类别：%s" % item.error_category_label
                st.write(text)
            if view.model_call_count is not None:
                st.write("本次模型调用：%d/10" % view.model_call_count)
