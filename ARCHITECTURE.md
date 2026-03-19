# Architecture

## Purpose

`autoresearch-modal` exists to make the upstream `karpathy/autoresearch` workflow legible and repeatable on Modal:

1. seed a persistent workspace from the vendored `karpathy/autoresearch` project files
2. surface the human-controlled `program.md` inside that checkout
3. warm and reuse the upstream cache
4. run one direct GPU baseline smoke
5. run a Claude-driven agent loop that follows the upstream research contract
6. inspect the resulting logs, results, and git state through the repo-owned `autoresearch-modal` CLI

This repo now carries the upstream research files at the root and adds orchestration, guardrails, validation, and repository-local knowledge around them.

## Module Map

| Path | Responsibility |
| --- | --- |
| `cli/` | Developer-facing terminal surface with stable subcommands, live execution, and dry-run previews over the Modal runtime |
| `agent_sandbox/autoresearch_app.py` | Modal app, image, volumes, public entrypoints, and subprocess orchestration |
| `agent_sandbox/autoresearch/core.py` | Shared helpers for path layout, vendored-root seeding, results rows, prompt construction, and training-log parsing |
| `agent_sandbox/config/settings.py` | Typed runtime configuration and Modal secret wiring |
| `agent_sandbox/utils/cli.py` | Claude CLI environment and user helpers reused inside Modal |
| `tests/` | Deterministic coverage for settings and pure helpers |
| `docs/references/autoresearch-modal-runbook.md` | Exact operator commands and expected runtime behavior |

## Runtime Boundaries

- The repo root contains the vendored upstream research files plus the Modal-specific orchestration/docs layers.
- Each run tag gets a persistent workspace repo seeded from an explicit upstream-root allowlist:
  - `.gitignore`, `.python-version`, `README.md`, `analysis.ipynb`, `prepare.py`, `program.md`, `progress.png`, `pyproject.toml`, `train.py`, `uv.lock`
- Wrapper-owned top-level surfaces (`AGENTS.md`, `ARCHITECTURE.md`, `agent_sandbox/`, `docs/`, `scripts/`, `tests/`) are intentionally excluded from the seeded repo.
- Persistent state is file-based:
  - workspace volume at `/home/agent/workspaces/autoresearch`
  - cache volume at `/home/agent/.cache/autoresearch`
  - upstream `program.md`, `results.tsv`, and `run.log` inside each prepared checkout
  - wrapper-owned `prepare.log`, `agent.log`, and `modal-run-state.json` inside each run root
- There is no first-party application database in this repo. The generated schema artifact currently snapshots the typed runtime settings surface instead.

## Execution Flow

1. Load `Settings` from environment and Modal secrets.
2. Build the Modal image with Python, git, Node, and Claude CLI available.
3. Mount persistent workspace and cache volumes.
4. Resolve the run tag from an explicit user value or generate a sortable one when `prepare`, `baseline`, or `agent-loop` starts a brand-new run.
5. Seed `<workspace>/<run_tag>/repo` from the upstream-root allowlist when that run tag is first created.
6. Initialize git state for that workspace repo and ensure the branch `autoresearch/<run_tag>` exists from local `master`.
7. Surface `program.md` so the human can steer the agent loop for that run tag.
8. Warm the upstream cache with `uv run prepare.py` when needed.
9. Run `uv run train.py` directly for a deterministic smoke or via a Claude prompt that follows the upstream loop contract.
10. Persist inspection artifacts (`results.tsv`, logs, git state summary) so operators can resume or audit a run from the dedicated CLI.

## Knowledge System

- `AGENTS.md` is the table of contents.
- `docs/product-specs` holds current intent and workflow expectations.
- `docs/design-docs/` holds durable architecture and agent-operating beliefs.
- `docs/exec-plans/` holds active work, completed work, and tech debt.
- `docs/references/` holds operational runbooks and external reference pointers.
- `docs/generated/` holds code-derived artifacts that should be regenerated instead of hand-edited.

## Change Guidance

- If you change runtime behavior, update `ARCHITECTURE.md`, the product spec, and the runbook together.
- If you change durable process or governance, update `docs/design-docs/`, `docs/PLANS.md`, and `docs/exec-plans/index.md`.
- If you add persistence, replace the placeholder generated schema workflow with a real exporter and document the new source of truth in `docs/generated/db-schema.md`.
