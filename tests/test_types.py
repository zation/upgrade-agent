"""Tests for core types and helpers (no network, no model)."""

from __future__ import annotations

from refactor_agent.core.types import (
    AgentConfig,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    parse_blocks,
    to_sdk_content,
)


def test_text_block_roundtrip() -> None:
    msg = Message(role="user", content=[TextBlock(text="hello")])
    assert msg.text() == "hello"
    sdk = to_sdk_content(msg)
    assert sdk == [{"type": "text", "text": "hello"}]


def test_parse_blocks_tool_use_and_result() -> None:
    raw = [
        {"type": "text", "text": "thinking..."},
        {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a.js"}},
        {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": [{"type": "text", "text": "file body"}],
        },
    ]
    blocks = parse_blocks(raw)
    assert isinstance(blocks[0], TextBlock)
    tu = blocks[1]
    assert isinstance(tu, ToolUseBlock)
    assert tu.name == "read_file" and tu.input == {"path": "a.js"}
    tr = blocks[2]
    assert isinstance(tr, ToolResultBlock)
    assert tr.tool_use_id == "t1" and tr.content == "file body"


def test_parse_blocks_unknown_type_falls_back_to_text() -> None:
    blocks = parse_blocks([{"type": "something_new", "foo": 1}])
    assert len(blocks) == 1
    assert isinstance(blocks[0], TextBlock)  # never silently dropped


def test_agent_config_defaults() -> None:
    cfg = AgentConfig()
    assert cfg.max_iterations > 0
    assert cfg.model
