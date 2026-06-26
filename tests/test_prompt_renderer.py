"""Structured prompt rendering tests."""

from __future__ import annotations

from upgrade_dependencies_agent.skills.fragments import BASELINE_RULE, VERIFY_RULE
from upgrade_dependencies_agent.skills.rendering import PromptSection, SkillPrompt


def test_skill_prompt_renders_base_contracts_and_sections() -> None:
    prompt = SkillPrompt(
        base="base text",
        contracts=(BASELINE_RULE, VERIFY_RULE),
        sections=(
            PromptSection("Task", "Do the task."),
            PromptSection("Report", "Report the result."),
        ),
    )

    rendered = prompt.render()

    assert rendered.startswith("base text\n\n")
    assert "Shared contracts:" in rendered
    assert f"- {BASELINE_RULE}" in rendered
    assert "## Task\n\nDo the task." in rendered
    assert "## Report\n\nReport the result." in rendered
