"""Helpers for the Modal autoresearch runner."""

from __future__ import annotations

import re
import shutil
import textwrap
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex

RESULTS_HEADER = "commit\tval_bpb\tmemory_gb\tstatus\tdescription\n"
RUN_TAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
VENDORED_PROJECT_ROOT_ENTRIES = (
    ".gitignore",
    ".python-version",
    "README.md",
    "analysis.ipynb",
    "prepare.py",
    "program.md",
    "progress.png",
    "pyproject.toml",
    "train.py",
    "uv.lock",
)
SEEDED_REPO_RUNTIME_ARTIFACTS = ("results.tsv",)


@dataclass(frozen=True)
class AutoresearchPaths:
    """Filesystem layout for one autoresearch run."""

    workspace_root: Path
    cache_dir: Path
    run_root: Path
    repo_dir: Path
    program_path: Path
    results_path: Path
    run_log_path: Path
    prepare_log_path: Path
    agent_log_path: Path
    state_path: Path


@dataclass(frozen=True)
class TrainingSummary:
    """Parsed training summary emitted by upstream train.py."""

    val_bpb: float
    training_seconds: float
    total_seconds: float
    peak_vram_mb: float
    mfu_percent: float
    total_tokens_m: float
    num_steps: int
    num_params_m: float
    depth: int


def copy_vendored_project_root(source_root: Path, destination_root: Path) -> None:
    """Copy only the vendored upstream root entries into a seeded workspace repo."""
    if destination_root.exists():
        raise FileExistsError(f"Destination already exists: {destination_root}")
    destination_root.mkdir(parents=True)
    for entry_name in VENDORED_PROJECT_ROOT_ENTRIES:
        source_path = source_root / entry_name
        if not source_path.exists():
            raise FileNotFoundError(f"Missing vendored root entry: {source_path}")
        destination_path = destination_root / entry_name
        if source_path.is_dir():
            shutil.copytree(source_path, destination_path)
        else:
            shutil.copy2(source_path, destination_path)


def validate_run_tag(run_tag: str) -> str:
    """Validate a user-provided run tag."""
    value = run_tag.strip()
    if not value:
        raise ValueError("run_tag must not be empty")
    if "/" in value:
        raise ValueError("run_tag must not contain '/'")
    if not RUN_TAG_PATTERN.fullmatch(value):
        raise ValueError("run_tag may only contain letters, numbers, dot, underscore, and dash")
    return value


def generate_run_tag(
    purpose: str,
    *,
    now: datetime | None = None,
    entropy: str | None = None,
) -> str:
    """Generate a sortable run tag that still satisfies the validation contract."""
    label = re.sub(r"[^A-Za-z0-9]+", "", purpose.lower())
    if not label:
        raise ValueError("purpose must include at least one letter or number")
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%d-%H%M%S")
    suffix = entropy if entropy is not None else token_hex(3)
    return validate_run_tag(f"{timestamp}-{label}-{suffix}")


def resolve_run_tag(run_tag: str | None, *, purpose: str) -> str:
    """Validate an explicit run tag or generate one when the caller omits it."""
    if run_tag is None:
        return generate_run_tag(purpose)
    return validate_run_tag(run_tag)


def branch_name(run_tag: str) -> str:
    """Return the upstream branch name for a run tag."""
    return f"autoresearch/{validate_run_tag(run_tag)}"


def build_paths(
    workspace_root: str | Path, cache_root: str | Path, run_tag: str
) -> AutoresearchPaths:
    """Build the persistent path layout for a run."""
    tag = validate_run_tag(run_tag)
    workspace_root_path = Path(workspace_root)
    run_root = workspace_root_path / tag
    repo_dir = run_root / "repo"
    return AutoresearchPaths(
        workspace_root=workspace_root_path,
        cache_dir=Path(cache_root),
        run_root=run_root,
        repo_dir=repo_dir,
        program_path=repo_dir / "program.md",
        results_path=repo_dir / "results.tsv",
        run_log_path=repo_dir / "run.log",
        prepare_log_path=run_root / "prepare.log",
        agent_log_path=run_root / "agent.log",
        state_path=run_root / "modal-run-state.json",
    )


def ensure_results_file(results_path: Path) -> None:
    """Create the upstream results TSV if it does not exist yet."""
    results_path.parent.mkdir(parents=True, exist_ok=True)
    if not results_path.exists() or not results_path.read_text(encoding="utf-8").strip():
        results_path.write_text(RESULTS_HEADER, encoding="utf-8")


def append_result_row(
    results_path: Path,
    *,
    commit: str,
    val_bpb: float,
    memory_gb: float,
    status: str,
    description: str,
) -> None:
    """Append one upstream-compatible results.tsv row."""
    ensure_results_file(results_path)
    clean_description = description.replace("\t", " ").replace("\n", " ").strip()
    row = f"{commit}\t{val_bpb:.6f}\t{memory_gb:.1f}\t{status}\t{clean_description}\n"
    with results_path.open("a", encoding="utf-8") as handle:
        handle.write(row)


def is_data_ready(cache_dir: Path) -> bool:
    """Return True when enough upstream cache artifacts exist to run train.py."""
    data_dir = cache_dir / "data"
    tokenizer_dir = cache_dir / "tokenizer"
    token_files_ready = (tokenizer_dir / "tokenizer.pkl").exists() and (
        tokenizer_dir / "token_bytes.pt"
    ).exists()
    shard_count = len(list(data_dir.glob("shard_*.parquet")))
    return token_files_ready and shard_count >= 2


def parse_training_summary(log_text: str) -> TrainingSummary:
    """Parse the fixed summary block emitted at the end of train.py."""
    patterns: dict[str, tuple[str, type[float] | type[int]]] = {
        "val_bpb": (r"^val_bpb:\s+([0-9.]+)$", float),
        "training_seconds": (r"^training_seconds:\s+([0-9.]+)$", float),
        "total_seconds": (r"^total_seconds:\s+([0-9.]+)$", float),
        "peak_vram_mb": (r"^peak_vram_mb:\s+([0-9.]+)$", float),
        "mfu_percent": (r"^mfu_percent:\s+([0-9.]+)$", float),
        "total_tokens_m": (r"^total_tokens_M:\s+([0-9.]+)$", float),
        "num_steps": (r"^num_steps:\s+([0-9]+)$", int),
        "num_params_m": (r"^num_params_M:\s+([0-9.]+)$", float),
        "depth": (r"^depth:\s+([0-9]+)$", int),
    }
    parsed: dict[str, float | int] = {}
    for field, (pattern, caster) in patterns.items():
        match = re.search(pattern, log_text, flags=re.MULTILINE)
        if not match:
            raise ValueError(f"Missing {field} in train.py summary output")
        parsed[field] = caster(match.group(1))

    return TrainingSummary(
        val_bpb=float(parsed["val_bpb"]),
        training_seconds=float(parsed["training_seconds"]),
        total_seconds=float(parsed["total_seconds"]),
        peak_vram_mb=float(parsed["peak_vram_mb"]),
        mfu_percent=float(parsed["mfu_percent"]),
        total_tokens_m=float(parsed["total_tokens_m"]),
        num_steps=int(parsed["num_steps"]),
        num_params_m=float(parsed["num_params_m"]),
        depth=int(parsed["depth"]),
    )


def build_claude_baseline_prompt(run_tag: str, prepare_num_shards: int) -> str:
    """Build a bounded Claude prompt that performs one baseline run and stops."""
    branch = branch_name(run_tag)
    return textwrap.dedent(
        f"""
        You are operating inside a workspace seeded from the vendored karpathy/autoresearch project.

        Follow the repository's `program.md` setup contract, but this session is intentionally bounded:

        1. Verify the current branch is `{branch}`. If it does not exist yet, create it from the current `master`.
        2. Read `README.md`, `program.md`, `prepare.py`, and `train.py` before taking action.
        3. If `.venv` is missing, run `uv sync`.
        4. If `~/.cache/autoresearch` is not ready, run `uv run prepare.py --num-shards {prepare_num_shards}`.
        5. Ensure `results.tsv` exists with the exact upstream header row.
        6. Do exactly one baseline training run with the code as-is:
           `uv run train.py > run.log 2>&1`
        7. Inspect the summary with:
           `grep "^val_bpb:\\|^peak_vram_mb:" run.log`
        8. Record the baseline in `results.tsv` as a `keep` row with description `baseline`.
        9. Do not modify `train.py`, do not start a second experiment, and do not ask for confirmation.
        10. Finish by printing a short summary that includes the branch name, current commit, and baseline `val_bpb`.

        Use git CLI commands directly for branch/status/commit inspection. Keep all output concise.
        """
    ).strip()


def build_autoresearch_agent_prompt(
    run_tag: str,
    prepare_num_shards: int,
    max_experiments: int,
) -> str:
    """Build the primary Claude prompt for the upstream-style research loop."""
    branch = branch_name(run_tag)
    return textwrap.dedent(
        f"""
        You are operating inside a Modal workspace seeded from the vendored karpathy/autoresearch project.

        Treat `README.md` and `program.md` as the primary contract. The human controls `program.md`;
        you are the autonomous researcher who edits only `train.py`.

        Session setup:

        1. Verify the current branch is `{branch}`. If it does not exist yet, create it from the current `master`.
        2. Read `README.md`, `program.md`, `prepare.py`, and `train.py` before acting.
        3. If `.venv` is missing, run `uv sync`.
        4. If `~/.cache/autoresearch` is not ready, run `uv run prepare.py --num-shards {prepare_num_shards}`.
        5. Ensure `results.tsv` exists with the exact upstream header row.
        6. If `results.tsv` only has the header row or no baseline entry yet, establish the baseline first:
           `uv run train.py > run.log 2>&1`
           then inspect `run.log`, record the row as `keep` with description `baseline`, and continue.

        Autonomous experiment loop:

        - Perform up to {max_experiments} completed experiment attempts after the baseline in this session.
        - Do not ask the human for confirmation once the loop begins.
        - Modify only `train.py`. Do not modify `prepare.py`, `program.md`, `pyproject.toml`, or the evaluation harness.
        - Use git CLI directly. Keep `results.tsv`, `run.log`, and any local scratch logs uncommitted.
        - For each experiment:
          1. Inspect git state and note the starting commit.
          2. Edit `train.py` with one concrete idea.
          3. Commit the `train.py` change.
          4. Run `uv run train.py > run.log 2>&1`.
          5. Read `grep "^val_bpb:\\|^peak_vram_mb:" run.log`.
          6. If the run crashed, inspect `tail -n 50 run.log`, decide whether to retry a small obvious fix or log a `crash` row and move on.
          7. Append the outcome to `results.tsv` using the upstream tab-separated format.
          8. If the result improved, keep the commit and continue from it.
          9. If the result is equal or worse, reset the branch back to the starting commit for that experiment and continue.

        Research goals:

        - Optimize for lower `val_bpb`.
        - Respect the fixed runtime budget encoded by upstream.
        - Prefer simpler wins over hacky complexity when the metric change is marginal.
        - Keep VRAM increases reasonable unless the gain is clearly worthwhile.

        Finish by printing a short summary with:

        - experiments attempted in this session
        - best `val_bpb` observed in this session
        - current branch and commit
        - any unresolved crash or limitation
        """
    ).strip()
