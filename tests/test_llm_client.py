"""Tests for provider wire-format adapters."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from upgrade_dependencies_agent.core.llm_client import OpenAICompatibleClient
from upgrade_dependencies_agent.core.types import AgentConfig, Message, TextBlock


def test_openai_compatible_client_passes_response_format(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"ok": true}', tool_calls=None),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2),
            )

    class FakeOpenAI:
        def __init__(self, *, api_key: str, base_url: str) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=FakeOpenAI, APIStatusError=RuntimeError),
    )

    client = OpenAICompatibleClient(api_key="test-key", base_url="https://example.test")
    response = client.ask(
        system="",
        messages=[Message(role="user", content=[TextBlock(text="return json")])],
        tools=[],
        config=AgentConfig(model="test-model"),
        response_format={"type": "json_object"},
    )

    assert calls[0]["response_format"] == {"type": "json_object"}
    assert response.assistant.text() == '{"ok": true}'
