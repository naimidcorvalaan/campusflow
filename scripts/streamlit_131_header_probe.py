"""Development-only probe for Streamlit 1.31 WebSocket headers.

This deliberately uses an unsupported private Streamlit API to measure the
current version. CampusFlow production code must not import this module.
"""

import os

import streamlit as st
from streamlit.web.server.websocket_headers import _get_websocket_headers


def main():
    headers = _get_websocket_headers() or {}
    expected = os.environ.get("CAMPUSFLOW_HEADER_PROBE_EXPECTED", "")
    received = next(
        (value for key, value in headers.items() if key.lower() == "x-campusflow-probe"),
        None,
    )
    st.title("Streamlit 1.31 header compatibility probe")
    st.write("probe_header_received={}".format(str(bool(expected and received == expected)).lower()))
    st.write("probe_header_present={}".format(str(received is not None).lower()))
    st.write("probe_expected_configured={}".format(str(bool(expected)).lower()))
    st.caption("Development measurement only; not an authentication adapter.")


if __name__ == "__main__":
    main()
