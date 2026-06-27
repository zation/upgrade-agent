"""Tests for structured-output parsing helpers."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from upgrade_dependencies_agent.core.structured import (
    StructuredParseError,
    parse_structured_text,
    response_format_for_schema,
)
from upgrade_dependencies_agent.orchestrator.state import (
    AgentReport,
    BaselineState,
    ResearchBrief,
    UpgradeQueue,
    VerificationResult,
)


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


def test_response_format_for_schema_builds_openai_json_schema_format() -> None:
    response_format = response_format_for_schema(ExampleResult)

    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "example_result"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"]["title"] == "ExampleResult"
    assert response_format["json_schema"]["schema"]["properties"]["ok"]["type"] == "boolean"


@pytest.mark.parametrize(
    ("schema", "name"),
    [
        (BaselineState, "baseline_state"),
        (ResearchBrief, "research_brief"),
        (UpgradeQueue, "upgrade_queue"),
        (VerificationResult, "verification_result"),
        (AgentReport, "agent_report"),
    ],
)
def test_response_format_for_schema_supports_upgrade_artifacts(
    schema: type[BaseModel],
    name: str,
) -> None:
    response_format = response_format_for_schema(schema)

    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == name
    assert response_format["json_schema"]["schema"]["type"] == "object"
