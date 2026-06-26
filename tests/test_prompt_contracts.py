"""Prompt contract tests for skill quality."""

from __future__ import annotations

from upgrade_dependencies_agent.skills import (
    ADD_TESTS_GENERATE,
    BREAKING_CHANGE_RESEARCHER,
    UPGRADE,
    UPGRADE_ALL,
)
from upgrade_dependencies_agent.skills.fragments import (
    BASELINE_RULE,
    MINIMAL_CHANGE_RULE,
    ONE_DEPENDENCY_RULE,
    READ_ONLY_RULE,
    SOURCE_EVIDENCE_RULE,
    TEST_STYLE_RULE,
    VERIFY_RULE,
    shared_contracts,
)


def test_upgrade_prompt_keeps_baseline_verify_and_minimal_change_contracts() -> None:
    assert BASELINE_RULE in UPGRADE
    assert VERIFY_RULE in UPGRADE
    assert MINIMAL_CHANGE_RULE in UPGRADE


def test_upgrade_all_prompt_keeps_one_dependency_contract() -> None:
    assert BASELINE_RULE in UPGRADE_ALL
    assert VERIFY_RULE in UPGRADE_ALL
    assert ONE_DEPENDENCY_RULE in UPGRADE_ALL


def test_research_prompt_keeps_read_only_source_and_verdict_contracts() -> None:
    assert READ_ONLY_RULE in BREAKING_CHANGE_RESEARCHER
    assert SOURCE_EVIDENCE_RULE in BREAKING_CHANGE_RESEARCHER
    assert "VERDICT: LOW" in BREAKING_CHANGE_RESEARCHER
    assert "VERDICT: MEDIUM" in BREAKING_CHANGE_RESEARCHER
    assert "VERDICT: HIGH" in BREAKING_CHANGE_RESEARCHER


def test_generate_tests_prompt_keeps_style_baseline_and_verify_contracts() -> None:
    assert TEST_STYLE_RULE in ADD_TESTS_GENERATE
    assert BASELINE_RULE in ADD_TESTS_GENERATE
    assert VERIFY_RULE in ADD_TESTS_GENERATE


def test_shared_contracts_render_with_consistent_heading_and_bullets() -> None:
    section = shared_contracts(BASELINE_RULE, VERIFY_RULE)

    assert section.startswith("Shared contracts:\n")
    assert f"- {BASELINE_RULE}" in section
    assert f"- {VERIFY_RULE}" in section
    assert section.endswith("\n\n")
