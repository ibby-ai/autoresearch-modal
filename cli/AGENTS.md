# CLI Agent Map

Keep this directory narrowly focused on the developer-facing `autoresearch-modal` CLI.

## Read Order

1. `cli/AGENTS.md`
2. `cli/README.md`
3. `cli/main.py`
4. `cli/commands.py`
5. Root `AGENTS.md`
6. Root `ARCHITECTURE.md`
7. `docs/references/autoresearch-modal-runbook.md`
8. Relevant tests

## Canonical Path Map

- CLI parser and flags: `cli/main.py`
- Command planning and execution: `cli/commands.py`
- CLI tests: `tests/test_cli.py`
- Runtime implementation behind the CLI: `agent_sandbox/autoresearch_app.py`

## Working Rules

- Keep the public command surface stable unless the user asks for a breaking change.
- Prefer adding subcommands or flags here rather than exposing raw Modal commands to developers.
- Treat `--dry-run` as a first-class safety feature: it must resolve the same payload as live execution, show compact file metadata instead of full content, and avoid touching Modal.
- Keep CLI docs in this directory and the root runbook synchronized when behavior changes.

## Compatibility

- `cli/CLAUDE.md` must stay a symlink to `cli/AGENTS.md`.
