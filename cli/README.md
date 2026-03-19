# CLI README

`cli/` is the developer-facing command surface for `autoresearch-modal`.

Use the installed console script from the repo root:

```bash
uv run autoresearch-modal --help
```

## What Lives Here

- `main.py` defines the argparse surface and top-level execution flow.
- `commands.py` resolves CLI inputs into executable or dry-run command plans and dispatches live calls through `python -m modal run -q -m agent_sandbox.autoresearch_app`.
- `__main__.py` supports `python -m cli` for local debugging.

## Command Surface

- `probe`
- `prepare`
- `program get`
- `program set --file`
- `baseline`
- `run`
- `inspect`
- `tail`
- `claude-baseline`

`program get`, `inspect`, and `tail` are read-only follow-up commands over existing run tags. They should fail on an unknown tag instead of bootstrapping a new workspace.

## Dry Run

Append `--dry-run` before or after a subcommand to preview the resolved Modal target, exact subprocess argv, scalar kwargs, and compact file metadata without calling Modal.

```bash
uv run autoresearch-modal --dry-run prepare --num-shards 10
uv run autoresearch-modal program set --dry-run --run-tag smoke --file ./program.md
```

Dry run resolves local file-backed inputs first, but it shows metadata (`path`, `bytes`, `line_count`, `sha256_12`) instead of dumping file contents.

For `run` and `claude-baseline`, a live failure with an explicit `--run-tag` now triggers a best-effort follow-up `inspect` so the CLI error includes current run-state and recent artifact tails when available.
