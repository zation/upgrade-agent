"""Structured-output parsing helpers.

This module is intentionally provider-agnostic. Native JSON / response-format
support can be added in ``llm_client`` later; graph nodes can already validate
model text against Pydantic schemas through these helpers.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError


class StructuredParseError(ValueError):
    """Raised when model text cannot be parsed into the requested schema."""


def parse_structured_text[SchemaT: BaseModel](text: str, schema: type[SchemaT]) -> SchemaT:
    """Extract a JSON object from model text and validate it with ``schema``."""
    raw = _extract_json_object(text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise StructuredParseError(f"Invalid JSON object: {e.msg}") from e
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        raise StructuredParseError(f"JSON object does not match {schema.__name__}") from e


def response_format_for_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Build an OpenAI-compatible JSON Schema response format for a Pydantic model."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": _schema_name(schema),
            "strict": True,
            "schema": _strict_json_schema(schema.model_json_schema()),
        },
    }


def _extract_json_object(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        return fenced.group(1)

    start = text.find("{")
    if start < 0:
        raise StructuredParseError("No JSON object found in model output")
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text[start:], start=start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise StructuredParseError("No complete JSON object found in model output")


def _schema_name(schema: type[BaseModel]) -> str:
    name = re.sub(r"(?<!^)(?=[A-Z])", "_", schema.__name__).lower()
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)


def _strict_json_schema(value: Any) -> Any:
    """Recursively mark object schemas as closed for provider strict mode."""
    if isinstance(value, dict):
        out = {key: _strict_json_schema(item) for key, item in value.items()}
        if out.get("type") == "object" and "additionalProperties" not in out:
            out["additionalProperties"] = False
        return out
    if isinstance(value, list):
        return [_strict_json_schema(item) for item in value]
    return value
