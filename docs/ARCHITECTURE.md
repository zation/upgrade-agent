# Architecture

> Deep-dive for contributors, future agents, and resume/interview reference.
> For day-to-day rules see [AGENTS.md](../AGENTS.md); for usage see [README.md](../README.md).

## Design goals

1. **Real tooling** — genuinely upgrade `chai-like`'s deps and verify with tests.
2. **Maximal learning coverage** — touch every generic AI-Agent technique once,
   so the author can study and discuss each.
3. **Idiomatic modern Python** — uv, ruff, pydantic, strict typing.

## Techniques → where they live

| Technique | File / concept | Status |
|-----------|----------------|--------|
| ReAct (Think–Act–Observe) | `core/react_loop.py` (hand-written loop) | ✅ M1 |
| Function Calling / Tool Use | native `tool_use` / `tool_calls` | ✅ M1 |
| Multi-provider abstraction | `core/llm_client.py` (Protocol + impls) | ✅ |
| Structured output | pydantic schemas (planned for plan/report) | 🚧 |
| Multi-step planning | `orchestrator/nodes/plan.py` | ⏳ M3 |
| State-graph orchestration | LangGraph `StateGraph` | ⏳ M3 |
| Self-healing / reflection | `verify → self-heal` edge | ⏳ M3 |
| RAG | changelog/release-notes retrieval (`tools/rag.py`) | ⏳ M4 |
| Sub-agent | "breaking-change researcher" | ⏳ M4 |
| Context engineering | `core/context.py` (budget + compaction) | ✅ M1 |
| Evals | `evals/` | ⏳ M5 |
| Observability | `core/trace.py` (JSONL) + rich CLI | ✅ M1 |

## The ReAct loop (the heart)

`core/react_loop.py` implements the loop by hand against the provider SDK, with
no "agent framework" mediating. Pseudocode of the core:

```
messages = [user task]
repeat up to max_iterations:
    if messages near token budget: compact older history
    resp = llm.ask(system, messages, tools)
    messages.append(resp.assistant)
    surface resp text to UI (callbacks)
    tool_uses = [blocks of type tool_use]
    if no tool_uses:                      # model is done reasoning
        return final_text
    for each tool_use:
        result = tool.run(args)           # errors captured, not fatal
        append tool_result to a new user message
```

Three concerns are layered on top **without obscuring** that core:
- **Tracing** — `Tracer.event(...)` per step → replayable JSONL.
- **Callbacks** — `LoopCallbacks` Protocol; the rich UI implements it with zero
  coupling (the loop never imports rich).
- **Compaction** — `context.py` keeps head (task) + tail (working memory),
  replaces the dropped middle with a summary stub.

**Why hand-written, not a framework?** This is the learning objective. ~120
lines of loop teaches how *every* tool-calling agent works. LangGraph arrives
later for *orchestration* (multi-node state machine), not to replace this loop.

## Model-agnostic layering

```
        ┌──────────────────────────────────────────────┐
        │  cli / skills   (compose core + tools)        │  task-specific
        ├──────────────────────────────────────────────┤
        │  tools          (fs/shell/git/npm)            │  ← depends on core only
        ├──────────────────────────────────────────────┤
        │  core           (react_loop, types, context)  │  MODEL + TASK agnostic
        ├──────────────────────────────────────────────┤
        │  llm_client     (Protocol + provider impls)   │  ← only place knowing wire format
        └──────────────────────────────────────────────┘
```

**Inviolable rule:** `core/` imports nothing project- or provider-specific.
Swapping Claude → DeepSeek → a local model touches *only* `llm_client.py`.
This was proven: switching to DeepSeek mid-build required zero changes to the
loop, tools, or CLI.

## Data flow (one upgrade task)

```
user: upgrade chai-like "mocha 4 -> 11"
  │
  ▼  ReActLoop.run(task)
  ┌─ iter 1: read package.json, run tests (baseline) ──► 28 passing
  ├─ iter 2: npm_releases mocha; read changelog  (research breaking changes)
  ├─ iter 3: edit_file package.json (4 → ^11); run_command npm install
  ├─ iter 4: run_command npm test  ──► FAIL? diagnose → edit code → retry
  └─ iter N: git_diff (confirm change); report from/to + final test result
```

At M3 the top level becomes a LangGraph `StateGraph` with nodes
`analyze → research → plan → execute → verify`, and a `verify`→`self-heal`
conditional edge. Each node internally may run a ReActLoop. The loop itself is
unchanged.

## Tools

All tools implement `Tool` (Protocol in `core/types.py`): `.name`,
`.description`, `.input_schema` (JSON Schema), `.run(args, ctx) -> ToolResult`.

**Safety:** every filesystem tool resolves paths through `safe_resolve()`
(`tools/_common.py`), which refuses any path escaping the target workdir. An
agent can never read/write outside the project it operates on.

**Cost control:** `read_file` caps lines (paged via offset/limit);
`run_command` caps captured output (head+tail); `glob`/`grep` cap match counts.
This keeps one noisy tool call from consuming the context budget.

## Observability

`core/trace.py` writes one JSONL file per run at `traces/<run_id>.jsonl` — one
JSON object per line per event (task, turn_start, tool_call, assistant, finish).
Eager appends mean a crash still leaves a usable trace. Replay with `cat | jq`.
(LangSmith export is a planned opt-in.)

The CLI's `RichUI` is a `LoopCallbacks` implementation: it renders the run as
streaming colored output (iteration markers, tool calls with ✓/✗, token counts,
trace path, final result panel). The loop calls it; it never imports the loop.

## Decisions & rationale (interview-ready)

- **Python over TS** — chosen to target AI/Agent Engineer roles where Python is
  the default; LangGraph/CrewAI/LlamaIndex are Python-first.
- **uv + ruff** — fastest modern Python toolchain; ruff replaces
  black+isort+flake8 in one tool.
- **Protocol-based LLMClient** — duck typing means a tool or a plain function
  can be a tool, and a provider impl can be a dataclass; no heavy ABC hierarchy.
- **edit_file uses exact-unique-match** — fails loudly on ambiguity instead of
  guessing; more reliable than asking the model to rewrite whole files.
- **run_command treats non-zero exit as data, not error** — failing test output
  must be *readable* by the model to self-heal; flagging it as a tool error
  would hide the very signal the agent needs.

## Roadmap

- **M1 ✅** ReAct core + fs/shell/git/npm tools; verified end-to-end.
- **M2 🚧** real single-dependency upgrade (mocha 4→11) with baseline/verify.
- **M3** LangGraph StateGraph + `verify → self-heal` edge.
- **M4** RAG changelog retrieval + "breaking-change researcher" sub-agent.
- **M5** eval harness (`evals/`) + CLI polish.
- **M6** add-tests skill.
