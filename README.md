# upgrade-dependencies-agent

A ReAct + LangGraph AI agent that **upgrades dependencies** and **adds tests**
for legacy JS/TS projects.

First target: [`zation/chai-like`](https://github.com/zation/chai-like) — an old
chai plugin (CommonJS, mocha 4, nyc 11, Travis-era tooling).

> This project is also a learning vehicle: it deliberately covers the broadest
> set of generic AI-Agent techniques so the author can study them and use this
> on a resume. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the
> technique→file map and design rationale.

## Status

- **M1–M2 ✅** — ReAct core and real single-dependency upgrades are complete.
- **M3–M5 🚧** — orchestration, RAG/research, evals, and CLI polish are in progress.
- **M6 ✅** — add-tests workflow v1 is complete (`analyze-coverage`, `generate-tests`).

See [docs/ROADMAP.md](docs/ROADMAP.md) for the canonical milestone status and
next implementation order.

## Techniques covered

| Technique | Where it lives | Status |
|-----------|----------------|--------|
| ReAct (Think–Act–Observe) | hand-written loop `core/react_loop.py` | ✅ |
| Function Calling / Tool Use | native tool-use protocol | ✅ |
| Multi-provider abstraction | `core/llm_client.py` (Claude + OpenAI-compat) | ✅ |
| Structured output | pydantic schemas (planned) | 🚧 |
| Multi-step planning (Plan-and-Execute) | `orchestrator/` | 🚧 |
| State-graph orchestration | LangGraph `StateGraph` in `orchestrator/upgrade_graph.py` | ✅ |
| Self-healing / reflection | `verify → self-heal` edge in `orchestrator/upgrade_graph.py` | ✅ |
| RAG | `dependency_research` seeds changelog / release-note sources | 🚧 |
| Sub-agent | `research-upgrade` read-only breaking-change researcher | ✅ |
| Context engineering | `core/context.py` (budget + compaction) | ✅ |
| Evals | deterministic runner in `evals/runner.py` | ✅ v1 / 🚧 trajectory |
| Observability | JSONL traces (`core/trace.py`) + rich CLI | ✅ |

## Quickstart

```bash
# 1. Install deps (uses uv — fast)
uv sync --extra dev

# 2. Configure your LLM provider
cp .env.example .env
#   For DeepSeek (or any OpenAI-compatible API):
#     LLM_PROVIDER=openai-compat
#     LLM_API_KEY=sk-...
#   For Anthropic Claude:
#     LLM_PROVIDER=anthropic
#     ANTHROPIC_API_KEY=sk-ant-...

# 3. Clone the target project somewhere on disk
git clone https://github.com/zation/chai-like ../chai-like
cd ../chai-like && npm install   # establish a working baseline

# 4. Run the agent against it
uv run upgrade-dependencies-agent analyze ../chai-like
uv run upgrade-dependencies-agent analyze-coverage ../chai-like
uv run upgrade-dependencies-agent generate-tests ../chai-like "cover uncovered public APIs"
uv run upgrade-dependencies-agent research-upgrade ../chai-like "mocha 4 -> 11"
uv run upgrade-dependencies-agent upgrade ../chai-like "mocha 4 -> 11"
uv run upgrade-dependencies-agent upgrade-graph ../chai-like "mocha 4 -> 11"
uv run upgrade-dependencies-agent upgrade-all ../chai-like
uv run upgrade-dependencies-agent ask       ../chai-like "any free-form task"
```

## Commands

| Command | Tools | Purpose |
|---------|-------|---------|
| `analyze <project>` | read-only | Profile a project; report upgrade risks. |
| `analyze-coverage <project> [focus]` | read-only | Inspect tests/coverage and report prioritized test gaps. |
| `generate-tests <project> [focus]` | full | Add focused tests, then verify `npm test` and coverage if available. |
| `research-upgrade <project> "<dep>"` | read-only | Research relevant breaking changes before editing. |
| `upgrade <project> "<dep>"` | full | Upgrade ONE dep: baseline → change → verify. |
| `upgrade-graph <project> "<dep>"` | full | Upgrade ONE dep via LangGraph: execute → verify → self-heal. |
| `upgrade-all <project>` | full | Upgrade all direct deps one at a time: baseline → queue → per-dep verify → final verify. |
| `ask <project> "<task>"` | full | Run the agent on an arbitrary task. |

Add `--verbose` for full model output, `--model` to override the provider default.

## Evals

Run deterministic eval cases from an isolated copy of the target project:

```bash
uv run python -m evals.runner evals/cases/chai-like-mocha-upgrade.json
```

The first eval harness does not use an LLM judge. It runs the case command, then
checks objective postconditions such as `package.json` dependency versions,
test-command success, and whether the changed paths stay inside an allowed set.

## Project layout

```
src/upgrade_dependencies_agent/
  core/          hand-written agent base — model-agnostic, task-agnostic
  tools/         fs / shell / git / npm (the agent's tool belt)
  skills/        domain prompts: analyze / upgrade
  orchestrator/  LangGraph workflows that compose ReAct runs
  cli/           typer entrypoint + rich live UI
docs/            ARCHITECTURE.md (deep-dive)
evals/           deterministic eval cases + runner
```

Day-to-day rules for contributors/agents: see [AGENTS.md](AGENTS.md).
