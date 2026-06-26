"""Prompt metric helpers."""

from __future__ import annotations

from upgrade_dependencies_agent.skills.metrics import prompt_char_counts


def test_prompt_char_counts_cover_all_task_prompts() -> None:
    counts = prompt_char_counts()

    assert set(counts) == {
        "analyze",
        "research_upgrade",
        "upgrade",
        "upgrade_all",
        "analyze_coverage",
        "generate_tests",
    }
    assert all(count > 0 for count in counts.values())
