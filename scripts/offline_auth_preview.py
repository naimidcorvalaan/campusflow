"""Offline fake-provider demo for the external identity/profile boundary.

The buttons simulate a trusted gateway *after* authentication. They are not a
login mechanism and the script ignores URL/header identity input. Production
use must replace this loader with a verified OIDC/CAS adapter.
"""

import os
import tempfile
from datetime import datetime
from pathlib import Path

import requests
import streamlit as st

from scripts.offline_product_model import OfflineProductModel
from src.external_auth import streamlit_oidc_identity
from src.local_persistence import LocalProfileStore
from src.p2_live_main import main as live_main


_SUBJECT_KEY = "campusflow_offline_gateway_subject"


class _OfflineOIDCUser(dict):
    @property
    def is_logged_in(self):
        return bool(self.get("is_logged_in"))


def _preview_directory():
    configured = os.environ.get("CAMPUSFLOW_AUTH_PREVIEW_DATA_DIR")
    if configured:
        directory = Path(configured).resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
        if directory.parent != temp_root or not directory.name.startswith(
            "campusflow-auth-rehearsal-"
        ):
            raise ValueError("Auth preview data must be an isolated temporary directory")
        directory.mkdir(parents=True, exist_ok=True)
        return directory
    return Path(tempfile.mkdtemp(prefix="campusflow-auth-rehearsal-"))


@st.cache_resource
def _resources():
    return _preview_directory(), OfflineProductModel()


def _deny_network(*args, **kwargs):
    raise RuntimeError("Offline auth preview blocks external model requests")


def main():
    st.set_page_config(page_title="CampusFlow · 离线认证原型", layout="wide")
    requests.sessions.Session.request = _deny_network
    directory, adapter = _resources()
    st.warning("离线认证原型 · 合成身份 · 不是生产登录")
    left, middle, right = st.columns(3)
    if left.button("模拟 Alice 已认证"):
        st.session_state[_SUBJECT_KEY] = "alice-subject"
        st.rerun()
    if middle.button("模拟 Bob 已认证"):
        st.session_state[_SUBJECT_KEY] = "bob-subject"
        st.rerun()
    if right.button("退出模拟认证"):
        st.session_state[_SUBJECT_KEY] = None
        st.rerun()
    st.caption(
        "按钮只模拟认证服务已完成验证后的服务端断言；URL 参数和浏览器自填身份不会被读取。"
    )

    def trusted_identity_loader():
        subject = st.session_state.get(_SUBJECT_KEY)
        if not subject:
            return None
        # Match the production adapter shape without contacting an IdP.
        return streamlit_oidc_identity(
            _OfflineOIDCUser(is_logged_in=True, sub=subject), "offline-mock"
        )

    live_main(
        st=st,
        adapter_factory=lambda: adapter,
        now_provider=lambda: datetime(2026, 9, 6, 14, 0),
        configuration_loader=lambda: (),
        persistence_factory=lambda: LocalProfileStore(directory / "campusflow.sqlite3"),
        identity_assertion_loader=trusted_identity_loader,
        configure_page=False,
    )
    with st.expander("开发诊断", expanded=False):
        st.caption("mock 模型调用：{} 次".format(len(adapter.calls)))
        st.caption("数据目录：隔离的系统临时目录（路径不在页面展示）。")


if __name__ == "__main__":
    main()
