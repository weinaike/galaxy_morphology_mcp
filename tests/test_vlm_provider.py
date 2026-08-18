"""Tests for the OpenAI-compatible shadow VLM callback."""

import base64
from types import SimpleNamespace

from component_analysis import OpenAICompatibleVLM


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\x0dIDAT\x08\xd7c\xf8\xcf\xc0"
    b"\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"parse_status":"OK"}')
                )
            ]
        )


class FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FakeCompletions())


def test_openai_compatible_callback_sends_inline_image_and_json_mode(tmp_path):
    image = tmp_path / "comparison.png"
    image.write_bytes(PNG_1X1)
    client = FakeClient()
    callback = OpenAICompatibleVLM(
        api_key="test-key",
        base_url="https://gateway.example/v1",
        model="gemini-test",
        client=client,
    )

    assert callback(str(image), "return JSON") == '{"parse_status":"OK"}'
    request = client.chat.completions.kwargs
    assert request["model"] == "gemini-test"
    assert request["temperature"] == 0
    assert request["response_format"] == {"type": "json_object"}
    content = request["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "return JSON"}
    image_url = content[1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")
    assert base64.b64decode(image_url.split(",", 1)[1]) == PNG_1X1


def test_openai_compatible_callback_uses_openai_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return FakeClient()

    callback = OpenAICompatibleVLM(client_factory=factory)
    assert callback.api_key == "env-key"
    assert callback.base_url == "https://env.example/v1"
    assert callback.model_id == "env-model"
    assert captured == {
        "api_key": "env-key",
        "base_url": "https://env.example/v1",
        "timeout": 360.0,
    }
