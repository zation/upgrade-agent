"""Runtime guardrails enforced by the ReAct loop."""

from __future__ import annotations

from pathlib import Path

from upgrade_dependencies_agent.core import (
    AgentConfig,
    LLMResponse,
    LoopResult,
    Message,
    ReActLoop,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from upgrade_dependencies_agent.tools import default_tools


class FakeClient:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses
        self.calls = 0

    def ask(self, **kwargs) -> LLMResponse:
        response = self.responses[self.calls]
        self.calls += 1
        return response


def _tool_response(name: str, input_: dict[str, object]) -> LLMResponse:
    return LLMResponse(
        assistant=Message(
            role="assistant",
            content=[ToolUseBlock(id="call-1", name=name, input=input_)],
        ),
        stop_reason="tool_use",
    )


def _done_response() -> LLMResponse:
    return LLMResponse(
        assistant=Message(role="assistant", content=[TextBlock(text="done")]),
        stop_reason="end_turn",
    )


def _last_tool_result_content(result: LoopResult) -> str:
    block = result.messages[-2].content[0]
    assert isinstance(block, ToolResultBlock)
    return block.content


def test_baseline_guardrail_blocks_file_mutation_before_green_baseline(tmp_path: Path) -> None:
    client = FakeClient(
        [
            _tool_response("write_file", {"path": "src/new.js", "content": "x"}),
            _done_response(),
        ]
    )
    loop = ReActLoop(
        client=client,
        config=AgentConfig(system_prompt="", max_iterations=2, enforce_baseline_guardrail=True),
        tools=default_tools(),
        workdir=str(tmp_path),
    )

    result = loop.run("write before baseline")

    assert result.ok
    assert not (tmp_path / "src" / "new.js").exists()
    assert "blocked by runtime guardrail" in _last_tool_result_content(result)


def test_baseline_guardrail_blocks_package_install_before_green_baseline(
    tmp_path: Path,
) -> None:
    client = FakeClient(
        [
            _tool_response("run_command", {"command": "npm install mocha@11"}),
            _done_response(),
        ]
    )
    loop = ReActLoop(
        client=client,
        config=AgentConfig(system_prompt="", max_iterations=2, enforce_baseline_guardrail=True),
        tools=default_tools(),
        workdir=str(tmp_path),
    )

    result = loop.run("install before baseline")

    assert result.ok
    assert "blocked by runtime guardrail" in _last_tool_result_content(result)


def test_baseline_guardrail_blocks_file_mutation_after_red_test_baseline(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"false"}}', encoding="utf-8")
    client = FakeClient(
        [
            _tool_response("run_command", {"command": "npm test"}),
            _tool_response("write_file", {"path": "src/new.js", "content": "x"}),
            _done_response(),
        ]
    )
    loop = ReActLoop(
        client=client,
        config=AgentConfig(system_prompt="", max_iterations=3, enforce_baseline_guardrail=True),
        tools=default_tools(),
        workdir=str(tmp_path),
    )

    result = loop.run("red baseline then write")

    assert result.ok
    assert not (tmp_path / "src" / "new.js").exists()
    assert "blocked by runtime guardrail" in _last_tool_result_content(result)


def test_baseline_guardrail_allows_file_mutation_after_green_test_baseline(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"true"}}', encoding="utf-8")
    client = FakeClient(
        [
            _tool_response("run_command", {"command": "npm test"}),
            _tool_response("write_file", {"path": "src/new.js", "content": "x"}),
            _done_response(),
        ]
    )
    loop = ReActLoop(
        client=client,
        config=AgentConfig(system_prompt="", max_iterations=3, enforce_baseline_guardrail=True),
        tools=default_tools(),
        workdir=str(tmp_path),
    )

    result = loop.run("baseline then write")

    assert result.ok
    assert (tmp_path / "src" / "new.js").read_text(encoding="utf-8") == "x"
