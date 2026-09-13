"""Offline safety for collection and execution; never load a local credential file."""
import dotenv
import pytest
import requests


# The legacy client loads .env at import time, before pytest fixtures run.
# Individual configuration tests may replace this with their own fake loader.
dotenv.load_dotenv = lambda *args, **kwargs: False


@pytest.fixture(autouse=True)
def offline_test_boundary(monkeypatch, tmp_path):
    monkeypatch.setenv("CAMPUSFLOW_DATA_DIR", str(tmp_path / "profile"))
    for name in ("TJU_LLM_BASE_URL", "TJU_LLM_API_KEY", "TJU_LLM_MODEL",
                 "CAMPUSFLOW_LLM_PROVIDER", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL",
                 "DEEPSEEK_MODEL", "DEEPSEEK_VISION_MODEL"):
        monkeypatch.delenv(name, raising=False)

    def no_network(*args, **kwargs):
        raise AssertionError("Tests must inject a fake model; network is disabled")

    monkeypatch.setattr(requests.sessions.Session, "request", no_network)
