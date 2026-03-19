# Reliability

## Validation Matrix

Run these in the repo virtualenv after changes:

```bash
source .venv/bin/activate
uv run ruff check --fix .
uv run ruff format .
uv run pytest
uv run autoresearch-modal probe
```

Run these when the Modal runtime, settings, prompt, or orchestration path changes:

```bash
uv run autoresearch-modal prepare --run-tag <tag> --num-shards 10
uv run autoresearch-modal baseline --run-tag <tag>
uv run autoresearch-modal program get --run-tag <tag>
uv run autoresearch-modal inspect --run-tag <tag> --lines 20
uv run autoresearch-modal run --run-tag <tag> --max-experiments 1 --max-turns 40
```

## Expectations

- Keep Python 3.11 for local CLI calls.
- Treat the direct baseline as the fastest end-to-end smoke.
- Treat the agent loop as the primary workflow proof when secrets are present.
- Treat the legacy one-shot Claude baseline as a support/debug surface, not the product-defining path.
- Record new evidence in the relevant completed plan or runbook update.

## Failure Handling

- Separate local environment failures from repo regressions.
- Preserve exact failing commands and the smallest useful tail of output.
- If remote validation is skipped, say so explicitly and do not imply current proof.
