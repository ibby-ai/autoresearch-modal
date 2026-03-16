# ExecPlan: harness-knowledge-migration

## Purpose

Migrate the repo to a Harness-style in-repo knowledge system so agent context, product intent, architecture, and plan/task history all live in canonical repo paths.

## Outcomes

- Replaced the long-form root instructions with a concise map-style `AGENTS.md`
- Added the required top-level architecture, design, product, quality, reliability, and security docs
- Moved the legacy hidden plan/task pack into `docs/exec-plans/completed/`
- Moved the runbook into `docs/references/`
- Seeded `docs/generated/db-schema.md` from the typed runtime settings model

## Decision Log

- Decision: Treat the existing Modal runbook as a reference document, not a product spec.
  Rationale: It contains exact operator commands and runtime caveats rather than user intent or roadmap decisions.

- Decision: Keep the prior `autoresearch_modal` plan pack as completed history.
  Rationale: It captures a finished migration and still provides useful provenance once its links are repaired.

- Decision: Generate the schema artifact from `agent_sandbox/config/settings.py` until the repo owns a real database layer.
  Rationale: The repo currently has no first-party DB schema, but agents still need a code-derived view of the typed runtime contract.

## Validation

- Verified the canonical layout exists under `docs/`
- Verified tracked-file searches for deprecated hidden plan paths return zero
- Verified top-level markdown links and moved-file targets resolve locally
- Re-ran Ruff, pytest, and repo-wide path checks after the migration

## Follow-Ups

- Add a mechanical doc/link/path check to CI
- Replace the placeholder schema workflow if a real persistence layer is introduced
