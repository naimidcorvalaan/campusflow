"""CampusFlow P1 独立实验页面入口；导入时不执行页面。"""
from datetime import datetime

from src.p1_offline_demo import EXAMPLES, is_supported_example, run_offline_demo
from src.p1_page_state import (ERROR_KEY, INPUT_KEY, OPTION_KEY, TIME_KEY, VIEW_KEY,
                               initialize_state, load_example, save_option, start_new_plan)
from src.p1_streamlit_renderer import render_view


def _rerun(st):
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def handle_submission(state, submitted, demo_runner):
    """唯一允许执行离线演示的纯提交边界。"""
    if not submitted:
        return False
    text = state.get(INPUT_KEY, "")
    if not is_supported_example(text):
        state[ERROR_KEY] = "当前页面仅用于固定离线样例演示；真实模型实验请使用 P1 live 页面。"
        state.pop(VIEW_KEY, None)
        return False
    try:
        state[VIEW_KEY] = demo_runner(text, state[TIME_KEY])
        state.pop(ERROR_KEY, None)
        return True
    except Exception:
        state.pop(VIEW_KEY, None)
        state[ERROR_KEY] = "离线演示暂时无法运行，请重新载入样例后再试。"
        return False


def main(st=None, demo_runner=run_offline_demo, now_provider=None):
    if st is None:
        import streamlit as st
    now_provider = datetime.now if now_provider is None else now_provider
    st.set_page_config(page_title="CampusFlow P1 实验页面")
    initialize_state(st.session_state, now_provider().replace(second=0, microsecond=0))
    st.title("CampusFlow P1 实验页面")
    st.info("当前为离线固定样例演示，尚未连接真实模型。")
    with st.expander("更多设置"):
        st.write("默认预留 10 分钟。")
        text_key = "p1_assumed_datetime_text"
        st.session_state.setdefault(text_key, st.session_state[TIME_KEY].strftime("%Y-%m-%d %H:%M"))
        entered = st.text_input("假定当前日期时间", key=text_key)
        try:
            st.session_state[TIME_KEY] = datetime.strptime(entered, "%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            pass
    if st.button("开始新计划", key="p1_new"):
        start_new_plan(st.session_state, now_provider().replace(second=0, microsecond=0))
        _rerun(st)
        return
    for index, example in enumerate(EXAMPLES):
        if st.button("载入演示样例 %d" % (index + 1), key="p1_example_%d" % index):
            load_example(st.session_state, example)
    with st.form("p1_demo_form"):
        st.text_area("现在想做什么？", key=INPUT_KEY)
        submitted = st.form_submit_button("运行离线演示")
    handle_submission(st.session_state, submitted, demo_runner)
    if ERROR_KEY in st.session_state:
        st.warning(st.session_state[ERROR_KEY])
    if VIEW_KEY in st.session_state:
        def choose(option):
            save_option(st.session_state, option)
        render_view(st, st.session_state[VIEW_KEY], choose)
    if OPTION_KEY in st.session_state:
        st.write("已选择：%s" % st.session_state[OPTION_KEY]["label"])


if __name__ == "__main__":
    main()
