# AGENTS.md

> Operating manual for any AI agent (or human contributor) working on this repo.
> Read this first before touching code.

## What this project is

`refactor-agent` is a ReAct + LangGraph AI agent that **upgrades dependencies**
and **adds tests** for legacy JS/TS projects. It is written in Python, calls an
LLM (Anthropic Claude or any OpenAI-compatible API such as DeepSeek), and
operates on a *target* project (e.g. `chai-like`) checked out elsewhere on disk.

The author's dual goal: a working tool **and** a learning vehicle covering the
broadest set of generic AI-Agent techniques (for study + resume).

## Current status (as of this commit)

- **M1 ✅** — hand-written ReAct loop + tools, verified end-to-end (agent
  analyzed `chai-like` over 10 iterations and produced a real report).
- **M2 ✅** — `upgrade` CLI command completed for real single-dependency
  upgrades. Baseline confirmed: `chai-like` = 28 passing, 100% cov.
- **M3 🚧** — `upgrade-all` CLI command is the next workflow: upgrade direct
  dependencies one at a time with baseline/per-package/final verification.
- **M4 🚧** — LangGraph `upgrade-graph` orchestration is being added as a thin
  workflow layer around the hand-written ReAct loop.
- M5–M6 not started (see Roadmap in README.md).

## Ground rules — READ BEFORE ACTING

1. **This repo edits the agent itself**, NOT the target project. The target
   (`chai-like`) lives at `/Users/liuyang/Projects/chai-like` and is operated on
   *by* the agent at runtime — do not commit it or edit it from here.
2. **Never commit secrets.** `.env` is gitignored; only `.env.example` is tracked.
3. **Every logical change = one commit** with a conventional-commit message
   (`feat(scope):`, `fix(scope):`, `chore:`, `docs:`). The author wants a
   granular, reviewable history.
4. **Code must pass `ruff` + `pytest` before committing.** No exceptions.

## How to work in this repo

```bash
# install deps (uses uv — fast)
uv sync --extra dev

# lint + format (MUST be clean)
uv run ruff check . && uv run ruff format --check .

# run unit tests
uv run pytest -v

# run the agent against a target project (needs .env with LLM_API_KEY)
uv run refactor-agent analyze   /Users/liuyang/Projects/chai-like
uv run refactor-agent upgrade   /Users/liuyang/Projects/chai-like "mocha 4 -> 11"
uv run refactor-agent upgrade-graph /Users/liuyang/Projects/chai-like "mocha 4 -> 11"
uv run refactor-agent upgrade-all /Users/liuyang/Projects/chai-like
uv run refactor-agent ask       /Users/liuyang/Projects/chai-like "your task"
```

## Architecture (the parts that matter)

```
src/refactor_agent/
  core/        hand-written agent base — MODEL-AGNOSTIC + TASK-AGNOSTIC
  tools/       the agent's tool belt — TASK-SPECIFIC but model-agnostic
  skills/      domain prompts (analyze / upgrade) + later sub-graphs
  orchestrator/ LangGraph workflows that compose ReAct runs
  cli/         typer entrypoint + rich live UI (LoopCallbacks)
```

**Layering rule (inviolable):** `core/` depends on nothing project-specific.
`tools/` depends on `core/` only. `skills/` and `cli/` compose them. Never
import `cli` or `skills` from `core`.

### The three load-bearing files
- `core/react_loop.py` — the hand-written Think-Act-Observe loop. This is the
  heart and the main learning artifact. Read it top-to-bottom.
- `core/llm_client.py` — the ONLY module that knows provider wire formats.
  `LLMClient` is a Protocol; `AnthropicClient` and `OpenAICompatibleClient`
  implement it; `create_client()` picks one via `LLM_PROVIDER`. Adding a new
  provider = one new class here, zero changes elsewhere.
- `tools/_common.py` → `safe_resolve()` — confines ALL file paths to the
  target workdir. Every fs tool routes through it. Do not bypass.

### How a tool is defined
A tool is anything with `.name`, `.description`, `.input_schema` (JSON Schema),
and `.run(args, ctx) -> ToolResult`. Inherit `ToolImpl` (in `core/types.py`)
for the common case. Register it in `tools/__init__.py`'s `default_tools()` /
`read_only_tools()`.

## Known gotchas (learned the hard way)

- **OpenAI vs Anthropic tool-result mapping**: Anthropic packs multiple
  `tool_result` blocks into one user message; OpenAI requires each as a separate
  `role:"tool"` message keyed by `tool_call_id`. `_user_to_sdk` returns a *list*
  and `ask()` does `extend` (not `append`). If you touch LLM translation, re-test
  multi-tool-call turns end-to-end.
- **Python 3.13 `Path.write_text`** does NOT auto-create parent dirs — `mkdir`
  first (the tests hit this).
- **Token cost**: the M1 analyze run burned ~120k input tokens, mostly from
  repeated reads of the large `package-lock.json`. Future tool improvements
  (dedup/cache) should target this.
- **`.env` is NOT `.env.example`** — the SDK loads from `.env`. A common setup
  mistake is editing the example file instead.

## Provider config (`.env`)

```
LLM_PROVIDER=openai-compat      # or "anthropic"
LLM_API_KEY=sk-...              # (or ANTHROPIC_API_KEY for anthropic)
# optional: LLM_BASE_URL, LLM_MODEL
```

Default model per provider: `deepseek-chat` (openai-compat), `claude-sonnet-4-5`
(anthropic). Override per-run with `--model`.

## What NOT to do

- Don't add a new "agent framework" dependency just to avoid writing loop logic —
  the hand-written ReAct loop is intentional (it's the point of the project).
  LangGraph arrives at M3 for *orchestration*, not to replace the loop.
- Don't make `core/` depend on a specific provider, target project, or skill.
- Don't edit `chai-like` from this repo directly — let the agent do it at runtime.
