"""Small helpers for tracking prompt size."""

from __future__ import annotations


def prompt_char_counts() -> dict[str, int]:
    """Return character counts for the main task prompts."""
    from .add_tests.prompts import ADD_TESTS_ANALYZE, ADD_TESTS_IMPROVE
    from .prompts import ANALYZE, BREAKING_CHANGE_RESEARCHER, UPGRADE, UPGRADE_ALL

    prompts = {
        "analyze": ANALYZE,
        "research_upgrade": BREAKING_CHANGE_RESEARCHER,
        "upgrade": UPGRADE,
        "upgrade_all": UPGRADE_ALL,
        "analyze_coverage": ADD_TESTS_ANALYZE,
        "improve_tests": ADD_TESTS_IMPROVE,
    }
    return {name: len(prompt) for name, prompt in prompts.items()}
