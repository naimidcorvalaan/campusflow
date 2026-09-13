import json
from types import SimpleNamespace


def test_one_shot_text_diagnostic_keeps_configuration_secret(tmp_path, monkeypatch, capsys):
    import src.tju_llm_client as client
    import scripts.verify_tju_image_capability as check

    calls = []
    def fake_call(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return "CampusFlow文本基线通过"

    monkeypatch.setattr(client, "call_tju_llm", fake_call)
    monkeypatch.setattr(check, "OUTPUT", tmp_path)
    monkeypatch.setenv("TJU_LLM_MODEL", "safe-model-id")
    monkeypatch.setenv("TJU_LLM_API_KEY", "NEVER_PERSIST_THIS_KEY")
    monkeypatch.setenv("TJU_LLM_BASE_URL", "https://private-endpoint.invalid")

    assert check.run("text") == 0
    assert len(calls) == 1
    written = (tmp_path / "text-diagnostic.json").read_text(encoding="utf-8")
    console = capsys.readouterr().out
    assert json.loads(written)["semantic_check"] is True
    assert "safe-model-id" in written
    assert "NEVER_PERSIST_THIS_KEY" not in written + console
    assert "private-endpoint.invalid" not in written + console


def test_image_answer_missing_visible_fact_is_reported_as_model_output(tmp_path, monkeypatch):
    import src.tju_llm_client as client
    import scripts.verify_tju_image_capability as check

    image_path = tmp_path / "synthetic.png"
    image_path.write_bytes(b"synthetic-png")
    monkeypatch.setattr(check, "OUTPUT", tmp_path)
    monkeypatch.setattr(check, "_synthetic_png", lambda: (
        image_path, SimpleNamespace(width=900, height=420)
    ))
    monkeypatch.setattr(client, "call_tju_llm_messages", lambda *args, **kwargs: "我看到了一张图片。")
    monkeypatch.setenv("TJU_LLM_MODEL", "safe-model-id")
    monkeypatch.setenv("TJU_LLM_API_KEY", "fake-key")
    monkeypatch.setenv("TJU_LLM_BASE_URL", "https://example.invalid")

    assert check.run("image") == 1
    result = json.loads((tmp_path / "image-diagnostic.json").read_text(encoding="utf-8"))
    assert result["http_status"] == 200
    assert result["response_text_received"] is True
    assert result["error_layer"] == "model_output"


def test_one_shot_image_uses_live_message_client_and_reports_visible_facts(tmp_path, monkeypatch, capsys):
    import src.tju_llm_client as client
    import scripts.verify_tju_image_capability as check

    image_path = tmp_path / "synthetic.png"
    image_path.write_bytes(b"synthetic-png")
    monkeypatch.setattr(check, "OUTPUT", tmp_path)
    monkeypatch.setattr(check, "_synthetic_png", lambda: (
        image_path, SimpleNamespace(width=900, height=420)
    ))
    monkeypatch.setenv("TJU_LLM_MODEL", "safe-model-id")
    monkeypatch.setenv("TJU_LLM_API_KEY", "NEVER_PERSIST_THIS_KEY")
    monkeypatch.setenv("TJU_LLM_BASE_URL", "https://private-endpoint.invalid")
    calls = []
    def fake_messages(messages, **kwargs):
        calls.append((messages, kwargs))
        return "图片文字是 CampusFlow 图片测试 314159，图形是蓝色圆形。"
    monkeypatch.setattr(client, "call_tju_llm_messages", fake_messages)

    assert check.run("image") == 0
    assert len(calls) == 1
    messages, options = calls[0]
    assert options["timeout"] == 120
    assert messages[1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    written = (tmp_path / "image-diagnostic.json").read_text(encoding="utf-8")
    console = capsys.readouterr().out
    result = json.loads(written)
    assert result["http_response_received"] is True
    assert result["semantic_components"] == {"text_marker": True, "circle": True, "blue": True}
    assert "314159" in result["response_excerpt"]
    assert "NEVER_PERSIST_THIS_KEY" not in written + console
    assert "private-endpoint.invalid" not in written + console
