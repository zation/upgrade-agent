# CI usage

This project has two CI-friendly surfaces:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -v
uv run python -m evals.runner evals/cases
```

The eval runner always prints JSON, exits non-zero when any case fails, and includes
deterministic failure reasons plus cost metrics when traces are available. For upgrade commands
that should be consumed by automation, use stdout JSON:

```bash
uv run upgrade-dependencies-agent upgrade ../target-project "mocha 4 -> 11" --json
uv run upgrade-dependencies-agent upgrade ../target-project "mocha, nyc" --dry-run --json
uv run upgrade-dependencies-agent upgrade-all ../target-project --dry-run --json
```

Use `--report-json <path>` when a later CI step needs a durable artifact file. `--json` is better
for direct shell parsing, while `--report-json` is better for uploaded artifacts.
