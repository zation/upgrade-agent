# upgrade-dependencies-agent

`upgrade-dependencies-agent` is an AI-powered dependency upgrade assistant for
JavaScript and TypeScript projects. It analyzes legacy package ecosystems, researches
breaking changes, applies focused dependency updates, adds targeted tests, and verifies
the result with the project's own commands.

The agent is built for real upgrade work: it combines a ReAct execution loop with
LangGraph workflow orchestration, provider-neutral LLM support, strict filesystem
boundaries, structured runtime state, and deterministic evaluation.

## Why It Stands Out

- **End-to-end dependency upgrades**: run baseline checks, research upgrade risks, edit
  dependency files, verify with tests, and self-heal failed upgrade attempts.
- **Batch upgrades with control**: upgrade direct dependencies one package at a time,
  verify each step, and produce a final structured report.
- **Source-backed research**: inspect npm metadata, GitHub releases, changelogs,
  migration guides, documentation pages, and project usage before recommending changes.
- **Test generation workflow**: find weak or missing test coverage, add a focused set of
  tests, and verify the result against the target project's test command.
- **CI-friendly output**: use JSON reports, dry-run planning, deterministic eval cases,
  and objective postcondition checks for automation.

## Core Capabilities

### Dependency Upgrade Workflows

`upgrade` handles a single dependency through a staged workflow:

```text
baseline -> research -> plan -> execute -> verify -> report
                         ^              |
                         |              v
                         +---- heal <---+
```

`upgrade-all` builds a queue from direct dependencies and upgrades them incrementally:

```text
baseline -> queue -> plan -> select package -> execute package -> verify package
                                                      ^                 |
                                                      +-----------------+
                                  -> final verify -> heal if needed -> report
```

Both workflows can write a structured `AgentReport` with success state, changed files,
remaining risks, failure reason, and recovery suggestions.

### Research and Retrieval

The research tools combine registry metadata with source discovery and lightweight
retrieval:

- npm package metadata, versions, dist-tags, repository, homepage, and README signals
- GitHub release notes with request caching
- changelog, migration guide, and docs URL fetching
- heading-based source chunks ranked by upgrade-risk keywords such as breaking changes,
  removals, deprecations, ESM/CJS, Node version requirements, peer dependencies, CLI
  changes, and config changes
- project usage search so generic breaking changes are only promoted when they matter
  to the target codebase

### Test and Coverage Support

The agent can run a read-only coverage analysis or generate a small, reviewable batch of
tests. It is instructed to follow the project's existing test style, establish a green
baseline first, and verify the final result with the available test and coverage signals.

## Safety Model

Dependency upgrades are risky, so the project includes guardrails at multiple layers.

- **Target project isolation**: filesystem tools resolve paths through `safe_resolve()`
  and cannot escape the target project directory.
- **Read-only modes**: analysis, research, and dry-run planning use a restricted toolset
  without file mutation or shell access where appropriate.
- **Baseline-before-mutation guardrail**: mutation tools and package-manager changes can
  be blocked until a green test baseline has been observed.
- **Mutation scope control**: workflow stages pass allowed file scopes to the runtime, so
  edits can be limited to expected files such as `package.json` and lockfiles.
- **Dirty worktree preflight**: upgrade mutation stages stop before touching a target
  project that already has uncommitted changes.
- **Dangerous revert protection**: broad commands such as `git reset --hard`,
  `git checkout .`, `git restore .`, and destructive clean operations are blocked.
- **Structured revert path**: `revert_files` restores explicitly requested tracked files
  and is subject to the same allowed-file guardrail.

## Technical Architecture

The codebase is organized around clean boundaries:

```text
src/upgrade_dependencies_agent/
  core/          provider-neutral ReAct loop, types, tracing, context, guardrails
  tools/         filesystem, shell, git, npm, changelog, and retrieval tools
  skills/        task prompts for analysis, upgrade, research, and test generation
  orchestrator/  LangGraph workflows and structured upgrade state
  cli/           Typer commands and Rich terminal UI
evals/           deterministic evaluation harness and cases
tests/           unit and workflow tests
```

Key implementation choices:

- **Provider-neutral LLM layer**: Anthropic and OpenAI-compatible APIs are isolated in
  `core/llm_client.py`.
- **Native structured output where available**: OpenAI-compatible providers can receive
  JSON Schema response formats; unsupported providers fall back to prompt-driven JSON
  plus local Pydantic validation.
- **Fail-closed verification**: workflow verification expects structured
  `VerificationResult` JSON. Invalid or unstructured output is treated as a failed
  verification, not a success.
- **Context and cost controls**: repeated file reads use per-run cache hits, large
  lockfiles and coverage reports default to summaries, long changelogs are summarized,
  and noisy npm output is reduced to the useful diagnostics.
- **Observable runs**: every run can write JSONL traces with model usage, tool calls,
  compaction events, and final outcomes.

## Installation

```bash
uv sync --extra dev
```

Configure an LLM provider:

```bash
cp .env.example .env
```

Open `.env` and set one of the supported provider configurations.

For OpenAI-compatible APIs:

```text
LLM_PROVIDER=openai-compat
LLM_API_KEY=sk-...
# optional: LLM_BASE_URL, LLM_MODEL
```

For Anthropic:

```text
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
# optional: LLM_MODEL
```

## Usage

Prepare a target JavaScript or TypeScript project, then run the agent from this
repository.

```bash
uv run upgrade-dependencies-agent analyze ../target-project
uv run upgrade-dependencies-agent analyze-coverage ../target-project
uv run upgrade-dependencies-agent improve-tests ../target-project "cover uncovered public APIs"
uv run upgrade-dependencies-agent research-upgrade ../target-project "mocha 4 -> 11"
uv run upgrade-dependencies-agent upgrade ../target-project "mocha 4 -> 11"
uv run upgrade-dependencies-agent upgrade ../target-project "mocha, nyc" --dry-run --json
uv run upgrade-dependencies-agent upgrade-all ../target-project --report-json agent-report.json
uv run upgrade-dependencies-agent ask ../target-project "inspect upgrade risks for test tooling"
```

## CLI Reference

| Command | Access | Purpose |
|---|---|---|
| `analyze <project>` | Read-only | Profile project structure, dependencies, and upgrade risks. |
| `analyze-coverage <project> [focus]` | Read-only | Identify missing or weak test coverage. |
| `improve-tests <project> [focus]` | Full tools | Repair a failing test baseline, add focused tests, and verify them. |
| `research-upgrade <project> "<dep>"` | Read-only | Research breaking changes before upgrading. |
| `upgrade <project> "<dep>"` | Full tools | Upgrade one dependency through the staged workflow. |
| `upgrade-all <project>` | Full tools | Upgrade direct dependencies one package at a time. |
| `ask <project> "<task>"` | Configurable | Run a free-form task; add `--read-only` to restrict tools. |

Common options:

- `--model` / `-m`: override the configured model.
- `--max-iters`: limit ReAct loop iterations.
- `--verbose` / `-v`: show fuller model output in the terminal UI.
- `--report-json <path>`: write a structured `AgentReport`.
- `--json`: print the report as machine-readable stdout.
- `--dry-run`: research and plan without executing mutation stages.

## Deterministic Evaluation

The eval runner copies a target project into an isolated workspace, runs a case command,
and checks objective postconditions. It does not rely on an LLM judge.

The bundled sample cases expect a compatible sibling target checkout at
`../target-project`.

```bash
uv run python -m evals.runner evals/cases/sample-mocha-upgrade.json
uv run python -m evals.runner evals/cases
```

Supported checks include package version assertions, command results, git diff scope,
trace ordering, trajectory policies, structured reports, research source coverage, and
budget limits for iterations, tool calls, tokens, wall time, and compaction count.

## Development Checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -v
```
