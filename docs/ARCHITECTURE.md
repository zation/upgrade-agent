# Architecture

> Contributor-facing design reference. For daily operating rules, see
> [AGENTS.md](../AGENTS.md). For user-facing setup and CLI usage, see
> [README.md](../README.md).

## Design Goals

1. **Run real upgrade workflows**: analyze JavaScript and TypeScript projects, upgrade
   dependencies, add focused tests, and verify results with the target project's own
   commands.
2. **Keep core boundaries clear**: separate the provider-neutral loop, provider adapters,
   tools, prompts, orchestration, and CLI.
3. **Make runs observable and reproducible**: write JSONL traces, collect structured
   reports, and evaluate behavior with deterministic checks.
4. **Protect target projects**: constrain filesystem access, stop unsafe mutation
   sequences, and avoid broad destructive revert commands.

## Implementation Map

| Capability | Location | Status |
|---|---|---|
| ReAct loop | `core/react_loop.py` | Implemented |
| Function calling / tool use | `core/types.py`, `core/llm_client.py` | Implemented |
| Provider adapters | `core/llm_client.py` | Implemented |
| Context compaction | `core/context.py` | Implemented |
| JSONL tracing | `core/trace.py` | Implemented |
| Tool protocol | `core/types.py` | Implemented |
| Path isolation | `tools/_common.py` | Implemented |
| npm and source research | `tools/npm.py`, `tools/changelog.py` | Implemented |
| Lightweight retrieval | `tools/changelog.py` | Implemented |
| LangGraph workflows | `orchestrator/upgrade_backbone.py`, `orchestrator/upgrade_workflow.py` | Implemented |
| Self-healing verify loop | `upgrade`, `upgrade-all` workflows | Implemented |
| Structured output | `core/structured.py`, `orchestrator/state.py` | Implemented |
| Runtime guardrails | `core/runtime_state.py`, `orchestrator/preflight.py` | Implemented |
| Deterministic evals | `evals/runner.py` | Implemented |

## Layering

```text
cli / skills       Compose core and tools for concrete tasks.
tools              Target-project operations; depend on core types only.
core               Provider-neutral agent infrastructure.
llm_client         Provider wire-format adapters.
orchestrator       LangGraph state machines and workflow artifacts.
```

Rules:

- `core/` must not import `cli/`, `skills/`, or task-specific project logic.
- New model providers should be added in `core/llm_client.py`.
- Tools expose `.name`, `.description`, `.input_schema`, and `.run()`.
- Filesystem tools must go through `safe_resolve()` instead of joining paths directly.

## ReAct Loop

`core/react_loop.py` implements a hand-written Think -> Act -> Observe loop.

```text
messages = [user task]
repeat up to max_iterations:
    compact older history when near the input budget
    call llm.ask(system, messages, tools)
    trace the assistant message
    if no tool_use blocks exist, return the final text
    execute requested tools
    append tool_result blocks as the next user message
```

The loop also handles:

- **Tracing**: model turns, usage, tool calls, guardrail blocks, and final outcomes.
- **Callbacks**: the CLI renders progress through callbacks; core does not depend on
  Rich.
- **Compaction**: long runs keep the initial task and recent turns while replacing older
  history with a compact summary stub.
- **Runtime guardrails**: mutation can be blocked before a green baseline, outside the
  allowed file scope, or when a broad revert command is attempted.

## LLM Providers

`core/llm_client.py` defines the `LLMClient` protocol and implements:

- `AnthropicClient`
- `OpenAICompatibleClient`
- `create_client()`

Provider differences are isolated here. The most important difference is tool-result
mapping:

- Anthropic accepts multiple `tool_result` blocks in one user message.
- OpenAI-compatible APIs require one `role: tool` message for each `tool_call_id`.

OpenAI-compatible providers can receive native JSON Schema response formats. When a
provider rejects JSON Schema, the client falls back to JSON object mode and local
Pydantic validation remains the final gate.

## Tool System

Tool groups:

- `read_only_tools()`: file reads, search, git status/diff, npm metadata, release/source
  fetching, and retrieval.
- `default_tools()`: read-only tools plus file mutation, shell commands, and scoped file
  revert.

Current tools:

- Filesystem: `read_file`, `write_file`, `edit_file`, `grep`, `glob`
- Shell: `run_command`
- Git: `git_status`, `git_diff`, `revert_files`
- npm: `npm_outdated`, `npm_view`, `npm_releases`, `dependency_research`
- Source research: `fetch_releases`, `fetch_url`, `retrieve_source_chunks`

Safety and cost controls:

- File paths are restricted to the target project directory.
- `read_file`, `run_command`, `grep`, and `glob` bound their output.
- Repeated `read_file` calls for the same slice return cache hits instead of repeating
  full content.
- `package-lock.json`, `lcov.info`, long changelogs, `npm test`, and `npm install`
  outputs are summarized by default when appropriate.
- Test failures are returned as normal tool output so the agent can diagnose and repair
  them.

## CLI Workflows

| Command | Purpose |
|---|---|
| `analyze` | Read-only project and dependency risk analysis. |
| `analyze-coverage` | Read-only test-gap analysis. |
| `generate-tests` | Add focused tests and verify them. |
| `research-upgrade` | Read-only dependency upgrade research. |
| `upgrade` | LangGraph-backed single-dependency upgrade. |
| `upgrade-all` | Batch upgrade of direct dependencies. |
| `ask` | Free-form task; supports `--read-only`. |

`upgrade` and `upgrade-all` support `--report-json`, `--json`, and `--dry-run`.
`upgrade <project> "mocha, nyc"` runs explicit dependencies sequentially and combines
their reports.

## LangGraph Workflows

`orchestrator/upgrade_backbone.py` contains a reusable backbone. Concrete workflow logic
lives in `orchestrator/upgrade_workflow.py`.

Implemented stages:

- `baseline`: establish the pre-upgrade test state.
- `research`: read-only source-backed dependency research.
- `queue`: build a structured direct-dependency queue for batch upgrades.
- `plan`: build a minimal executable plan and allowed-file scope.
- `execute`: apply a single dependency upgrade.
- `select_package`, `execute_package`, `verify_package`: advance batch upgrades one
  package at a time.
- `verify` / `final_verify`: validate the resulting project state.
- `heal`: attempt a bounded repair after failed verification.
- `report`: collect changed files and produce an `AgentReport`.

Structured artifacts include `BaselineState`, `ResearchBrief`, `UpgradePlan`,
`UpgradeQueue`, `VerificationResult`, `PackageUpgradeRecord`, and `AgentReport`.

Verification stages fail closed: invalid or unstructured verification output is treated
as failed verification rather than success.

## Research and Retrieval

The project uses lightweight retrieval rather than a vector database:

- `dependency_research` reports current, target, latest, major span, candidate sources,
  and risk hints.
- Source discovery includes GitHub releases, changelogs, migration guides, docs sites,
  and npm README signals.
- `fetch_releases` reads GitHub release notes and caches repeated repo/tag requests.
- `fetch_url` reads changelogs, migration guides, and docs pages as text.
- `retrieve_source_chunks` splits source text by headings or release sections and ranks
  chunks by upgrade-risk keywords.
- `research-upgrade` combines source evidence with project usage search. If sources are
  unavailable or weak, it reports the gap and falls back to test-driven verification.

The interface can support semantic retrieval later, but the current implementation stays
dependency-light and deterministic-test friendly.

## Eval Design

`evals/runner.py` is a deterministic eval harness:

- Copy the target project to an isolated workspace.
- Initialize a git baseline.
- Run the case command.
- Execute objective checks.
- Print JSON for single-case or batch results.

Supported case features:

- `timeout`, `env`, `setup`, `teardown`, `budgets`
- `package_json_version`
- `command`
- `git_diff`
- `trace_sequence`
- `structured_report`
- `research_report`
- `trajectory_policy: baseline_before_mutation`
- `trajectory_policy: single_dependency_at_a_time`

Failure reasons include `timeout`, `wrong_diff`, `test_failed`, `llm_error`,
`postcondition_failed`, `trajectory_violation`, `baseline_missing`,
`multi_dependency_upgrade`, `structured_report_failed`, `research_report_failed`, and
`budget_exceeded`.

## Key Tradeoffs

- **Hand-written loop, explicit orchestration**: the ReAct loop remains transparent while
  LangGraph handles workflow state and conditional routing.
- **Tests as observations**: failing test output is data for diagnosis, not a tool crash.
- **Precise file edits**: `edit_file` requires one exact match to reduce accidental broad
  rewrites.
- **Deterministic evals first**: objective outcomes and trajectory checks are preferred
  over LLM-as-judge scoring.
