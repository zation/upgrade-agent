# refactor-agent

A ReAct + LangGraph AI agent that **upgrades dependencies** and **adds tests** for legacy JS/TS projects.

First target: [`zation/chai-like`](https://github.com/zation/chai-like) — an old chai plugin (ES5, chai 3.x, Travis CI).

> This project is also a learning vehicle: it deliberately covers the broadest set of generic AI-Agent techniques so the author can study them and use this on a resume.

## Techniques covered

| Technique | Where it lives |
|-----------|----------------|
| ReAct (Think–Act–Observe) | hand-written tool loop in `core/react_loop.py` |
| Function Calling / Tool Use | native Claude `tool_use` protocol |
| Structured output | Pydantic schemas for upgrade plans / reports |
| Multi-step planning (Plan-and-Execute) | `orchestrator/nodes/plan.py` |
| State-graph orchestration | LangGraph `StateGraph` |
| Self-healing / reflection | `verify → self-heal` edge |
| RAG | changelog / release-notes retrieval |
| Sub-agent | "breaking-change researcher" |
| Context engineering | `core/context.py` |
| Evals | `evals/` |
| Observability | JSONL traces + rich CLI output |

## Quickstart

```bash
# 1. Install (uses uv — fast)
uv sync --extra dev

# 2. Add your key
cp .env.example .env
# edit .env -> set ANTHROPIC_API_KEY

# 3. Clone the target project somewhere on disk
git clone https://github.com/zation/chai-like ../chai-like

# 4. Run the agent against it
uv run refactor-agent analyze ../chai-like
```

## Project layout

```
src/refactor_agent/
  core/          hand-written agent base (LLM-agnostic, task-agnostic)
  orchestrator/  LangGraph state graph + nodes
  tools/         fs / shell / git / npm / changelog / rag
  skills/        domain skill packs (upgrade-dependencies, add-tests)
  cli/           typer entrypoint + rich UI
evals/           fixed tasks + runner + golden answers
```

## Roadmap

- **M1** ReAct core + fs/shell tools ← _current_
- **M2** upgrade a single dependency (chai 3 → 5) end-to-end
- **M3** LangGraph orchestration + self-heal
- **M4** RAG + research sub-agent
- **M5** eval runner + CLI polish
- **M6** add-tests skill
