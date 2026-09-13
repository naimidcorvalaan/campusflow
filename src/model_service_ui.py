"""Local-only first-run service setup; uses native widgets, no business state."""
import os

from src.model_service_config import (
    FIELDS, ModelConfigError, effective_configuration, read_local_config,
    save_local_config, clear_local_config, test_saved_connection, local_config_exists,
)

PREFIX = "cf_model_"


def editor_allowed(st):
    # The desktop launcher binds loopback. Never expose a server credential
    # editor to Community Cloud or a manually network-bound Streamlit server.
    try:
        from src.llm_provider import provider_name
        return provider_name() == "tju" and st.get_option("server.address") in ("127.0.0.1", "localhost", "::1")
    except Exception:
        return False


def _save(st):
    state = st.session_state
    try:
        saved = read_local_config()
    except ModelConfigError:
        saved = {}
    values = {FIELDS[0]: state.get(PREFIX + "address", ""),
              FIELDS[2]: state.get(PREFIX + "model", ""),
              FIELDS[1]: state.get(PREFIX + "key", "") or saved.get(FIELDS[1], "")}
    try:
        save_local_config(values)
    except ModelConfigError as exc:
        state[PREFIX + "error"] = str(exc)
        return
    state.pop(PREFIX + "error", None)
    state.pop(PREFIX + "test", None)
    state[PREFIX + "key"] = ""
    state[PREFIX + "skip"] = True
    state[PREFIX + "notice"] = "模型配置已保存在本机，可直接使用。"


def _clear(st):
    try:
        clear_local_config()
    except ModelConfigError as exc:
        st.session_state[PREFIX + "error"] = str(exc)
        return
    for name in ("key", "address", "model", "test", "error", "notice"):
        st.session_state.pop(PREFIX + name, None)
    st.session_state[PREFIX + "notice"] = "已清除页面保存的配置；环境变量和 .env 保持原样。"
    st.session_state[PREFIX + "clear_confirm"] = False
    st.session_state[PREFIX + "skip"] = True


def open_service_settings(store, settings_key):
    store[settings_key] = True
    store[PREFIX + "expand"] = True


def render_editor(st):
    values = effective_configuration()
    try:
        read_local_config()
    except ModelConfigError as exc:
        st.warning(str(exc))
    if st.session_state.get(PREFIX + "notice"):
        st.success(st.session_state[PREFIX + "notice"])
    if st.session_state.get(PREFIX + "error"):
        st.warning(st.session_state[PREFIX + "error"])
    external = {name: bool(os.environ.get(name, "").strip()) for name in FIELDS}
    if any(external.values()):
        st.caption("已有环境变量或 .env 管理的字段保留原值；如需修改，请在原配置来源调整。")
    with st.form("cf_model_service_form"):
        st.text_input("API 地址", value=values.get(FIELDS[0], ""), key=PREFIX + "address", max_chars=4096,
                      disabled=external[FIELDS[0]], placeholder="请填写你获得的 TJU 模型服务地址")
        st.text_input("模型名称", value=values.get(FIELDS[2]) or "tju-llm", key=PREFIX + "model", max_chars=4096,
                      disabled=external[FIELDS[2]])
        st.text_input("API Key", type="password", key=PREFIX + "key", max_chars=4096, disabled=external[FIELDS[1]],
                      placeholder="已配置；留空保留，输入新值可更新" if values.get(FIELDS[1]) else "填写自己的 API Key")
        st.caption("配置保存在本机用户目录，与个人档案分开。保存不会发起模型请求。")
        st.form_submit_button("保存配置", type="primary", on_click=_save, args=(st,),
                              disabled=all(external.values()))
    if all(values.get(name) for name in FIELDS):
        st.caption("测试使用已保存配置，将发起一次短模型请求。")
        if st.button("测试已保存的连接", key=PREFIX + "test_button"):
            with st.spinner("正在测试连接…"):
                st.session_state[PREFIX + "test"] = test_saved_connection()
        result = st.session_state.get(PREFIX + "test")
        if result:
            (st.success if result[0] else st.warning)(result[1])
    if local_config_exists():
        st.caption("清除仅影响在此页面保存的模型配置，不影响任务、课表和偏好。")
        if st.checkbox("确认清除本机模型配置", key=PREFIX + "clear_confirm"):
            st.button("清除配置", key=PREFIX + "clear_button", on_click=_clear, args=(st,))


def render_first_run(st, missing):
    if not editor_allowed(st):
        return False
    if missing and not st.session_state.get(PREFIX + "skip"):
        with st.container():
            st.markdown('<span class="cf-model-setup-marker"></span>', unsafe_allow_html=True)
            st.markdown('<div class="cf-page-title">连接 TJU 模型服务</div>', unsafe_allow_html=True)
            st.write("CampusFlow 使用模型服务理解任务、估计工作量并生成规划。首次使用请填写自己的 TJU 模型配置。")
            render_editor(st)
            st.button("暂时跳过，先看看", key=PREFIX + "skip_button",
                      on_click=_skip, args=(st,))
        return True
    return False


def render_settings_service(st):
    if not editor_allowed(st):
        return
    values = effective_configuration()
    configured = all(values.get(name) for name in FIELDS)
    with st.expander("TJU 模型服务 · " + ("已配置" if configured else "未配置"),
                     expanded=bool(st.session_state.get(PREFIX + "expand"))):
        render_editor(st)


def _skip(st):
    st.session_state[PREFIX + "skip"] = True
