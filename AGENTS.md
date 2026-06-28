# AGENTS.md

> Operating guide for AI agents and human contributors. Read this before changing code.

## Project Overview

`upgrade-dependencies-agent` is a ReAct + LangGraph agent for upgrading dependencies
and adding tests in legacy JavaScript and TypeScript projects. The agent itself is
implemented in Python. At runtime it calls an LLM provider, such as Anthropic Claude or
an OpenAI-compatible API, and operates on a separate target project directory.

The repository contains the agent implementation only. Target projects are external
workspaces that the agent may inspect or modify while a CLI command is running.

## Ground Rules

1. **Only change this agent repository directly.**  
   Do not edit, commit, or clean target project directories from this repository. Let the
   agent operate on target projects through the CLI at runtime.
2. **Do not commit secrets.**  
   `.env` is gitignored. Commit `.env.example` only.
3. **Use one logical commit per change.**  
   Prefer conventional commits such as `feat(scope):`, `fix(scope):`, `docs:`, or
   `chore:`.
4. **Run checks before committing.**

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -v
```

## Common Commands

```bash
# Install dependencies
uv sync --extra dev

# Lint, format, and test
uv run ruff check .
uv run ruff format --check .
uv run pytest -v

# Run the agent. Replace ../target-project with the target project directory.
uv run upgrade-dependencies-agent analyze ../target-project
uv run upgrade-dependencies-agent analyze-coverage ../target-project
uv run upgrade-dependencies-agent improve-tests ../target-project "cover uncovered behavior"
uv run upgrade-dependencies-agent research-upgrade ../target-project "mocha 4 -> 11"
uv run upgrade-dependencies-agent upgrade ../target-project "mocha 4 -> 11"
uv run upgrade-dependencies-agent upgrade-all ../target-project
uv run upgrade-dependencies-agent ask ../target-project "your task"

# Run deterministic evals
uv run python -m evals.runner evals/cases
```

## Directory Layout

```text
src/upgrade_dependencies_agent/
  core/          provider-neutral agent core, runtime state, tracing, context
  tools/         filesystem, shell, git, npm, changelog, and retrieval tools
  skills/        task prompts for analysis, upgrades, research, and test generation
  orchestrator/  LangGraph workflows and structured upgrade state
  cli/           Typer entrypoints and Rich terminal UI
evals/           deterministic eval harness and cases
docs/            architecture, CI guidance, and completed milestone archive
tests/           unit and workflow tests
```

Layering rules:

- `core/` must not import `cli/`, `skills/`, or task-specific project logic.
- `tools/` should depend only on core types.
- `skills/` and `cli/` compose core and tools for concrete workflows.
- Add new LLM providers in `core/llm_client.py`.

## Key Files

- `core/react_loop.py`: hand-written Think -> Act -> Observe loop.
- `core/llm_client.py`: the only module that knows provider wire formats.
- `core/types.py`: provider-neutral `Message`, `Tool`, `ToolResult`, and block types.
- `core/runtime_state.py`: runtime guardrails for baseline, mutation scope, and broad
  reverts.
- `tools/_common.py`: `safe_resolve()`, required for target-project filesystem access.
- `tools/git.py`: git status, diff, and scoped `revert_files`.
- `orchestrator/upgrade_backbone.py`: reusable LangGraph backbone.
- `orchestrator/upgrade_workflow.py`: single-dependency and batch upgrade workflows.
- `evals/runner.py`: deterministic eval runner.
- `docs/ROADMAP.md`: completed milestone archive and historical planning record.

## Tool Definition Pattern

Tools expose:

- `.name`
- `.description`
- `.input_schema`
- `.run(args, ctx) -> ToolResult`

Most tools inherit `ToolImpl`. New tools should be registered in `tools/__init__.py`
through `read_only_tools()` or `default_tools()`.

## Known Gotchas

- **OpenAI-compatible and Anthropic tool results map differently.**  
  Anthropic can put multiple `tool_result` blocks in one user message.
  OpenAI-compatible APIs require one `role: tool` message per `tool_call_id`. After
  changing `llm_client.py`, rerun multi-tool-call tests.
- **Python 3.13 `Path.write_text` does not create parent directories.**  
  Create parents with `mkdir(parents=True, exist_ok=True)` before writing new files.
- **Token cost is driven by repeated large-file reads.**  
  `read_file` has a per-run cache and summarizes large lockfiles and coverage reports by
  default. Preserve those protections when changing file tools.
- **`.env` is not `.env.example`.**  
  The SDK reads `.env`; update the real local file when testing provider settings.

## Provider Configuration

```text
LLM_PROVIDER=openai-compat      # or anthropic
LLM_API_KEY=sk-...              # for OpenAI-compatible providers
ANTHROPIC_API_KEY=sk-ant-...    # for Anthropic
# optional: LLM_BASE_URL, LLM_MODEL
```

Default models:

- `openai-compat`: `deepseek-chat`
- `anthropic`: `claude-sonnet-4-5`

The CLI accepts `--model` to override the configured default.

## Do Not

- Do not directly edit, commit, or clean target project directories.
- Do not replace the hand-written ReAct loop with a new agent framework.
- Do not make `core/` depend on a specific provider, target project, skill, or CLI.
- Do not bypass `safe_resolve()` for target-project filesystem access.
