"""Tests for structured-output parsing helpers."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from upgrade_dependencies_agent.core.structured import StructuredParseError, parse_structured_text


class ExampleResult(BaseModel):
    ok: bool
    summary: str


def test_parse_structured_text_accepts_plain_json_object() -> None:
    result = parse_structured_text('{"ok": true, "summary": "28 passing"}', ExampleResult)

    assert result == ExampleResult(ok=True, summary="28 passing")


def test_parse_structured_text_accepts_fenced_json_object() -> None:
    result = parse_structured_text(
        'Here is the result:\n```json\n{"ok": false, "summary": "1 failing"}\n```',
        ExampleResult,
    )

    assert result == ExampleResult(ok=False, summary="1 failing")


def test_parse_structured_text_rejects_missing_json_object() -> None:
    with pytest.raises(StructuredParseError, match="No JSON object"):
        parse_structured_text("VERDICT: PASS", ExampleResult)


def test_parse_structured_text_rejects_schema_mismatch() -> None:
    with pytest.raises(StructuredParseError, match="does not match"):
        parse_structured_text('{"ok": true}', ExampleResult)
