"""Runtime guardrails enforced by the ReAct loop."""

from __future__ import annotations

from pathlib import Path

import pytest

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
        self.ask_kwargs: list[dict[str, object]] = []

    def ask(self, **kwargs) -> LLMResponse:
        self.ask_kwargs.append(kwargs)
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


@pytest.mark.parametrize(
    "command",
    [
        "npm update mocha",
        "npm uninstall chai",
        "npm rm nyc",
        "pnpm add mocha@11",
        "pnpm remove chai",
        "yarn add mocha@11",
        "yarn remove chai",
        "yarn upgrade mocha",
    ],
)
def test_baseline_guardrail_blocks_package_mutation_commands_before_green_baseline(
    tmp_path: Path,
    command: str,
) -> None:
    client = FakeClient(
        [
            _tool_response("run_command", {"command": command}),
            _done_response(),
        ]
    )
    loop = ReActLoop(
        client=client,
        config=AgentConfig(system_prompt="", max_iterations=2, enforce_baseline_guardrail=True),
        tools=default_tools(),
        workdir=str(tmp_path),
    )

    result = loop.run("package mutation before baseline")

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


def test_allowed_files_guardrail_blocks_file_mutation_outside_scope(
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
        config=AgentConfig(
            system_prompt="",
            max_iterations=3,
            enforce_baseline_guardrail=True,
            allowed_files=("package.json", "package-lock.json"),
        ),
        tools=default_tools(),
        workdir=str(tmp_path),
    )

    result = loop.run("baseline then write outside scope")

    assert result.ok
    assert not (tmp_path / "src" / "new.js").exists()
    assert "outside the allowed mutation scope" in _last_tool_result_content(result)


def test_allowed_files_guardrail_blocks_revert_outside_scope(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"true"}}', encoding="utf-8")
    client = FakeClient(
        [
            _tool_response("run_command", {"command": "npm test"}),
            _tool_response("revert_files", {"paths": ["src.js"]}),
            _done_response(),
        ]
    )
    loop = ReActLoop(
        client=client,
        config=AgentConfig(
            system_prompt="",
            max_iterations=3,
            enforce_baseline_guardrail=True,
            allowed_files=("package.json", "package-lock.json"),
        ),
        tools=default_tools(),
        workdir=str(tmp_path),
    )

    result = loop.run("baseline then revert outside scope")

    assert result.ok
    assert "outside the allowed mutation scope" in _last_tool_result_content(result)


def test_package_revert_guardrail_blocks_whole_package_manifest_revert(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"true"}}', encoding="utf-8")
    client = FakeClient(
        [
            _tool_response("run_command", {"command": "npm test"}),
            _tool_response("revert_files", {"paths": ["package.json", "package-lock.json"]}),
            _done_response(),
        ]
    )
    loop = ReActLoop(
        client=client,
        config=AgentConfig(
            system_prompt="",
            max_iterations=3,
            enforce_baseline_guardrail=True,
            current_dependency="beeper",
        ),
        tools=default_tools(),
        workdir=str(tmp_path),
    )

    result = loop.run("baseline then package revert")

    assert result.ok
    assert "package-level revert" in _last_tool_result_content(result)


def test_allowed_files_guardrail_allows_file_mutation_inside_scope(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"true"}}', encoding="utf-8")
    client = FakeClient(
        [
            _tool_response("run_command", {"command": "npm test"}),
            _tool_response("write_file", {"path": "package.json", "content": "{}"}),
            _done_response(),
        ]
    )
    loop = ReActLoop(
        client=client,
        config=AgentConfig(
            system_prompt="",
            max_iterations=3,
            enforce_baseline_guardrail=True,
            allowed_files=("package.json", "package-lock.json"),
        ),
        tools=default_tools(),
        workdir=str(tmp_path),
    )

    result = loop.run("baseline then write inside scope")

    assert result.ok
    assert (tmp_path / "package.json").read_text(encoding="utf-8") == "{}"


def test_revert_guardrail_blocks_dangerous_git_reset_after_green_baseline(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"true"}}', encoding="utf-8")
    client = FakeClient(
        [
            _tool_response("run_command", {"command": "npm test"}),
            _tool_response("run_command", {"command": "git reset --hard HEAD"}),
            _done_response(),
        ]
    )
    loop = ReActLoop(
        client=client,
        config=AgentConfig(system_prompt="", max_iterations=3, enforce_baseline_guardrail=True),
        tools=default_tools(),
        workdir=str(tmp_path),
    )

    result = loop.run("baseline then dangerous reset")

    assert result.ok
    assert "dangerous revert command" in _last_tool_result_content(result)


def test_shell_guardrail_blocks_git_stash_after_green_baseline(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"true"}}', encoding="utf-8")
    client = FakeClient(
        [
            _tool_response("run_command", {"command": "npm test"}),
            _tool_response("run_command", {"command": "git stash"}),
            _done_response(),
        ]
    )
    loop = ReActLoop(
        client=client,
        config=AgentConfig(system_prompt="", max_iterations=3, enforce_baseline_guardrail=True),
        tools=default_tools(),
        workdir=str(tmp_path),
    )

    result = loop.run("baseline then stash")

    assert result.ok
    assert "unsafe shell command" in _last_tool_result_content(result)


def test_shell_guardrail_blocks_writes_outside_workdir_after_green_baseline(
    tmp_path: Path,
) -> None:
    outside_file = tmp_path.parent / "outside-agent-write.txt"
    outside_file.unlink(missing_ok=True)
    (tmp_path / "package.json").write_text('{"scripts":{"test":"true"}}', encoding="utf-8")
    client = FakeClient(
        [
            _tool_response("run_command", {"command": "npm test"}),
            _tool_response("run_command", {"command": f"echo pwned > {outside_file}"}),
            _done_response(),
        ]
    )
    loop = ReActLoop(
        client=client,
        config=AgentConfig(system_prompt="", max_iterations=3, enforce_baseline_guardrail=True),
        tools=default_tools(),
        workdir=str(tmp_path),
    )

    result = loop.run("baseline then write outside")

    assert result.ok
    assert not outside_file.exists()
    assert "outside the target project" in _last_tool_result_content(result)
    assert ".upgrade-agent/tmp/" in _last_tool_result_content(result)


def test_revert_guardrail_allows_read_only_git_diff_after_green_baseline(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"true"}}', encoding="utf-8")
    client = FakeClient(
        [
            _tool_response("run_command", {"command": "npm test"}),
            _tool_response("run_command", {"command": "git diff --name-only"}),
            _done_response(),
        ]
    )
    loop = ReActLoop(
        client=client,
        config=AgentConfig(system_prompt="", max_iterations=3, enforce_baseline_guardrail=True),
        tools=default_tools(),
        workdir=str(tmp_path),
    )

    result = loop.run("baseline then diff")

    assert result.ok
    assert "$ git diff --name-only" in _last_tool_result_content(result)


def test_shell_guardrail_allows_project_local_temp_output_after_green_baseline(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"true"}}', encoding="utf-8")
    client = FakeClient(
        [
            _tool_response("run_command", {"command": "npm test"}),
            _tool_response(
                "run_command",
                {
                    "command": (
                        "mkdir -p .upgrade-agent/tmp && "
                        "npx mocha --reporter spec > .upgrade-agent/tmp/mocha_output.txt"
                    )
                },
            ),
            _done_response(),
        ]
    )
    loop = ReActLoop(
        client=client,
        config=AgentConfig(system_prompt="", max_iterations=3, enforce_baseline_guardrail=True),
        tools=default_tools(),
        workdir=str(tmp_path),
    )

    result = loop.run("baseline then project local temp output")

    assert result.ok
    assert "unsafe shell command" not in _last_tool_result_content(result)


def test_react_loop_passes_response_format_to_client(tmp_path: Path) -> None:
    client = FakeClient([_done_response()])
    loop = ReActLoop(
        client=client,
        config=AgentConfig(
            system_prompt="",
            max_iterations=1,
            response_format={"type": "json_object"},
        ),
        tools=default_tools(),
        workdir=str(tmp_path),
    )

    result = loop.run("return json")

    assert result.ok
    assert client.ask_kwargs[0]["response_format"] == {"type": "json_object"}
