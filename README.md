# refactor-agent

A ReAct + LangGraph AI agent that **upgrades dependencies** and **adds tests**
for legacy JS/TS projects.

First target: [`zation/chai-like`](https://github.com/zation/chai-like) — an old
chai plugin (CommonJS, mocha 4, nyc 11, Travis-era tooling).

> This project is also a learning vehicle: it deliberately covers the broadest
> set of generic AI-Agent techniques so the author can study them and use this
> on a resume. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the
> technique→file map and design rationale.

## Status

- **M1 ✅** — hand-written ReAct loop + fs/shell/git/npm tools. Verified
  end-to-end: the agent analyzed `chai-like` over 10 iterations and produced a
  real dependency-upgrade-risk report.
- **M2 ✅** — `upgrade` command completed and verified for real single-dependency upgrades.
- **M3 🚧** — `upgrade-all` command added for direct dependency upgrades, one package at a time.

## Techniques covered

| Technique | Where it lives | Status |
|-----------|----------------|--------|
| ReAct (Think–Act–Observe) | hand-written loop `core/react_loop.py` | ✅ |
| Function Calling / Tool Use | native tool-use protocol | ✅ |
| Multi-provider abstraction | `core/llm_client.py` (Claude + OpenAI-compat) | ✅ |
| Structured output | pydantic schemas (planned) | 🚧 |
| Multi-step planning (Plan-and-Execute) | `orchestrator/nodes/plan.py` | ⏳ M3 |
| State-graph orchestration | LangGraph `StateGraph` | ⏳ M3 |
| Self-healing / reflection | `verify → self-heal` edge | ⏳ M3 |
| RAG | changelog / release-notes retrieval | ⏳ M4 |
| Sub-agent | "breaking-change researcher" | ⏳ M4 |
| Context engineering | `core/context.py` (budget + compaction) | ✅ |
| Evals | `evals/` | ⏳ M5 |
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
uv run refactor-agent analyze ../chai-like
uv run refactor-agent upgrade ../chai-like "mocha 4 -> 11"
uv run refactor-agent upgrade-all ../chai-like
uv run refactor-agent ask       ../chai-like "any free-form task"
```

## Commands

| Command | Tools | Purpose |
|---------|-------|---------|
| `analyze <project>` | read-only | Profile a project; report upgrade risks. |
| `upgrade <project> "<dep>"` | full | Upgrade ONE dep: baseline → change → verify. |
| `upgrade-all <project>` | full | Upgrade all direct deps one at a time: baseline → queue → per-dep verify → final verify. |
| `ask <project> "<task>"` | full | Run the agent on an arbitrary task. |

Add `--verbose` for full model output, `--model` to override the provider default.

## Project layout

```
src/refactor_agent/
  core/          hand-written agent base — model-agnostic, task-agnostic
  tools/         fs / shell / git / npm (the agent's tool belt)
  skills/        domain prompts: analyze / upgrade (+ later sub-graphs)
  cli/           typer entrypoint + rich live UI
docs/            ARCHITECTURE.md (deep-dive)
evals/           (M5) fixed tasks + runner + golden answers
```

Day-to-day rules for contributors/agents: see [AGENTS.md](AGENTS.md).

## Roadmap

- **M1 ✅** ReAct core + fs/shell/git/npm tools; verified end-to-end.
- **M2 ✅** real single-dependency upgrade (mocha 4→11) with baseline/verify.
- **M3 🚧** real all-dependencies upgrade with baseline/verify.
- **M4** LangGraph `StateGraph` + `verify → self-heal` edge.
- **M5** RAG changelog retrieval + "breaking-change researcher" sub-agent.
- **M6** eval harness + CLI polish.
- **M7** add-tests skill.
