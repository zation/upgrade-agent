"""Structured prompt rendering primitives.

These helpers keep prompts maintainable before runtime guardrails exist. They
render prompt contracts into text for the LLM; they do not enforce behavior at
runtime. Programmatic enforcement belongs in the graph/state/tool layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from .fragments import shared_contracts


@dataclass(frozen=True)
class PromptSection:
    """A titled section inside a rendered skill prompt."""

    title: str
    body: str

    def render(self) -> str:
        return f"## {self.title}\n\n{self.body.strip()}"


@dataclass(frozen=True)
class SkillPrompt:
    """Structured representation of a skill prompt before rendering to text."""

    base: str
    contracts: tuple[str, ...]
    sections: tuple[PromptSection, ...]

    def render(self) -> str:
        parts = [self.base.strip()]
        if self.contracts:
            parts.append(shared_contracts(*self.contracts).strip())
        parts.extend(section.render() for section in self.sections)
        return "\n\n".join(parts)
