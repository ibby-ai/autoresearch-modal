"""Dedicated Modal entrypoints for running karpathy/autoresearch."""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import modal

from agent_sandbox.autoresearch import (
    append_result_row,
    branch_name,
    build_autoresearch_agent_prompt,
    build_claude_baseline_prompt,
    build_paths,
    copy_vendored_project_root,
    ensure_results_file,
    is_data_ready,
    parse_training_summary,
    resolve_run_tag,
)
from agent_sandbox.config.settings import get_modal_secrets, get_settings
from agent_sandbox.utils.cli import (
    RUNTIME_APP_ROOT,
    RUNTIME_HOME,
    RUNTIME_USER,
    demote_to_runtime_user,
    maybe_chown_for_runtime_user,
    require_claude_cli_auth,
    runtime_user_env,
)

_settings = get_settings()
_logger = logging.getLogger(__name__)

AUTORESEARCH_APP_NAME = "autoresearch-modal"
AUTORESEARCH_WORKSPACE_VOLUME = modal.Volume.from_name(
    _settings.autoresearch_workspace_vol_name,
    create_if_missing=True,
)
AUTORESEARCH_CACHE_VOLUME = modal.Volume.from_name(
    _settings.autoresearch_cache_vol_name,
    create_if_missing=True,
)
AUTORESEARCH_VOLUMES = {
    _settings.autoresearch_workspace_root: AUTORESEARCH_WORKSPACE_VOLUME,
    _settings.autoresearch_cache_root: AUTORESEARCH_CACHE_VOLUME,
}
AUTORESEARCH_IMAGE_IGNORE = [
    ".claude",
    ".git",
    ".github",
    ".mypy_cache",
    ".pre-commit-config.yaml",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".serena",
    ".vscode",
    "__pycache__",
    "*.egg-info",
    "*.pyc",
    ".DS_Store",
    "results.tsv",
    "run.log",
    "prepare.log",
    "agent.log",
    "modal-run-state.json",
]
AUTORESEARCH_WORKSPACE_SEED_SOURCE = "vendored-project-root-allowlist"


def _build_autoresearch_image() -> modal.Image:
    """Build the Modal image used for autoresearch preparation and execution."""
    return (
        modal.Image.debian_slim(python_version="3.11")
        .apt_install("curl", "git")
        .uv_pip_install("uv")
        .run_commands(
            "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
            "apt-get install -y nodejs",
            f"useradd -m -s /bin/bash -U {RUNTIME_USER}",
            f"su -l {RUNTIME_USER} -c 'curl -fsSL https://claude.ai/install.sh | bash'",
        )
        .env(
            {
                "PATH": (
                    "/root/.local/bin:/root/.claude/bin:"
                    f"{RUNTIME_HOME}/.local/bin:{RUNTIME_HOME}/.claude/bin:"
                    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
                ),
                "PYTHONUNBUFFERED": "1",
            }
        )
        .workdir(str(RUNTIME_APP_ROOT))
        .add_local_dir(
            ".",
            remote_path=str(RUNTIME_APP_ROOT),
            copy=True,
            ignore=AUTORESEARCH_IMAGE_IGNORE,
        )
        .run_commands(
            f"chown -R {RUNTIME_USER}:{RUNTIME_USER} {RUNTIME_APP_ROOT}",
            f"cd {RUNTIME_APP_ROOT} && uv pip install -e . --system --no-cache",
        )
    )


app = modal.App(AUTORESEARCH_APP_NAME)
autoresearch_image = _build_autoresearch_image()


def _subprocess_kwargs(as_runtime_user: bool, env: dict[str, str] | None = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"text": True}
    if as_runtime_user:
        kwargs["env"] = env or runtime_user_env()
        kwargs["preexec_fn"] = demote_to_runtime_user()
    elif env is not None:
        kwargs["env"] = env
    return kwargs


def _autoresearch_env(cache_dir: Path | None = None) -> dict[str, str]:
    """Build the runtime environment for repo-local autoresearch commands."""
    env = runtime_user_env()
    active_cache_dir = cache_dir or Path(_settings.autoresearch_cache_root)
    env["TRITON_CACHE_DIR"] = str(active_cache_dir / "triton-cache")
    env["TORCHINDUCTOR_CACHE_DIR"] = str(active_cache_dir / "inductor-cache")
    env["UV_CACHE_DIR"] = str(active_cache_dir / "uv-cache")
    return env


def _run_command(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int,
    as_runtime_user: bool = False,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command in the autoresearch workspace."""
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        timeout=timeout,
        **_subprocess_kwargs(as_runtime_user, env),
    )
    if check and result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(cmd)}\n{message}")
    return result


def _run_command_to_log(
    cmd: list[str],
    *,
    cwd: Path,
    log_path: Path,
    timeout: int,
    as_runtime_user: bool = False,
    env: dict[str, str] | None = None,
) -> None:
    """Run a command and redirect stdout/stderr to a log file."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=handle,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            **_subprocess_kwargs(as_runtime_user, env),
        )
    if result.returncode != 0:
        tail = _tail_file(log_path)
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(cmd)}\nLast log lines:\n{tail}"
        )


def _tail_file(path: Path, lines: int = 80) -> str:
    """Return the last N lines of a file for error reporting."""
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(content[-lines:])


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    maybe_chown_for_runtime_user(path)


def _git(repo_dir: Path, *args: str, timeout: int = 300, check: bool = True) -> str:
    result = _run_command(
        ["git", *args],
        cwd=repo_dir,
        timeout=timeout,
        as_runtime_user=True,
        check=check,
    )
    return (result.stdout or "").strip()


def _commit_volumes() -> None:
    for volume in (AUTORESEARCH_WORKSPACE_VOLUME, AUTORESEARCH_CACHE_VOLUME):
        try:
            volume.commit()
        except RuntimeError as exc:
            if "running function" not in str(exc):
                _logger.warning("Failed to commit volume: %s", exc)


def _seed_repo_from_vendored_project(paths: Any) -> None:
    copy_vendored_project_root(RUNTIME_APP_ROOT, paths.repo_dir)
    _run_command(
        ["chown", "-R", f"{RUNTIME_USER}:{RUNTIME_USER}", str(paths.repo_dir)],
        cwd=paths.run_root,
        timeout=300,
    )
    _run_command(
        ["git", "init", "-b", _settings.autoresearch_base_branch],
        cwd=paths.repo_dir,
        timeout=300,
        as_runtime_user=True,
    )
    _git(paths.repo_dir, "config", "user.name", _settings.autoresearch_git_user_name)
    _git(paths.repo_dir, "config", "user.email", _settings.autoresearch_git_user_email)
    _git(paths.repo_dir, "add", ".", timeout=600)
    _git(
        paths.repo_dir,
        "commit",
        "-m",
        "Seed vendored autoresearch snapshot",
        timeout=600,
    )


def _bootstrap_workspace(run_tag: str) -> tuple[Any, str]:
    paths = build_paths(
        _settings.autoresearch_workspace_root,
        _settings.autoresearch_cache_root,
        run_tag,
    )
    _ensure_dir(paths.workspace_root)
    _ensure_dir(paths.run_root)
    _ensure_dir(paths.cache_dir)
    _ensure_dir(paths.cache_dir / "triton-cache")
    _ensure_dir(paths.cache_dir / "inductor-cache")
    _ensure_dir(paths.cache_dir / "uv-cache")

    if not paths.repo_dir.exists():
        _seed_repo_from_vendored_project(paths)

    _git(paths.repo_dir, "config", "user.name", _settings.autoresearch_git_user_name)
    _git(paths.repo_dir, "config", "user.email", _settings.autoresearch_git_user_email)

    target_branch = branch_name(run_tag)
    existing_branch = _git(paths.repo_dir, "branch", "--list", target_branch)
    if existing_branch:
        _git(paths.repo_dir, "checkout", target_branch)
    else:
        _git(
            paths.repo_dir,
            "checkout",
            "-B",
            _settings.autoresearch_base_branch,
        )
        _git(paths.repo_dir, "checkout", "-b", target_branch)

    ensure_results_file(paths.results_path)
    maybe_chown_for_runtime_user(paths.results_path)
    _commit_volumes()
    return paths, target_branch


def _prepare_if_needed(paths: Any, num_shards: int) -> bool:
    if is_data_ready(paths.cache_dir):
        return False
    _run_command_to_log(
        ["uv", "run", "prepare.py", "--num-shards", str(num_shards)],
        cwd=paths.repo_dir,
        log_path=paths.prepare_log_path,
        timeout=_settings.autoresearch_prepare_timeout,
        as_runtime_user=True,
        env=_autoresearch_env(paths.cache_dir),
    )
    _commit_volumes()
    return True


def _current_commit(repo_dir: Path) -> str:
    return _git(repo_dir, "rev-parse", "--short", "HEAD")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_run_state(paths: Any, **payload: Any) -> None:
    body = {
        "updated_at": _utc_now(),
        "repo_dir": str(paths.repo_dir),
        "program_path": str(paths.program_path),
        "results_path": str(paths.results_path),
        "run_log_path": str(paths.run_log_path),
        "prepare_log_path": str(paths.prepare_log_path),
        "agent_log_path": str(paths.agent_log_path),
        **payload,
    }
    paths.state_path.write_text(json.dumps(body, indent=2, sort_keys=True), encoding="utf-8")
    maybe_chown_for_runtime_user(paths.state_path)
    _commit_volumes()


def _git_status(repo_dir: Path) -> tuple[list[dict[str, str]], list[str]]:
    output = _git(repo_dir, "status", "--short", "--untracked-files=all")
    tracked_changes: list[dict[str, str]] = []
    untracked_files: list[str] = []
    for line in output.splitlines():
        if not line:
            continue
        status = line[:2]
        path = line[3:]
        if status == "??":
            untracked_files.append(path)
        else:
            tracked_changes.append({"status": status.strip(), "path": path})
    return tracked_changes, untracked_files


def _recent_lines(path: Path, *, lines: int = 20) -> list[str]:
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return content[-lines:]


def _repo_root_files(repo_dir: Path) -> list[str]:
    return sorted(path.name for path in repo_dir.iterdir() if path.name != ".git")


def _inspect_run(paths: Any, target_branch: str, *, tail_lines: int = 20) -> dict[str, Any]:
    tracked_changes, untracked_files = _git_status(paths.repo_dir)
    allowed_dirty_paths = {
        "program.md",
        "results.tsv",
        "run.log",
    }
    unexpected_dirty_paths = sorted(
        {entry["path"] for entry in tracked_changes if entry["path"] not in allowed_dirty_paths}
        | {path for path in untracked_files if path not in allowed_dirty_paths}
    )
    return {
        "run_tag": paths.run_root.name,
        "branch": target_branch,
        "repo_dir": str(paths.repo_dir),
        "repo_root_files": _repo_root_files(paths.repo_dir),
        "workspace_seed_source": AUTORESEARCH_WORKSPACE_SEED_SOURCE,
        "program_path": str(paths.program_path),
        "results_path": str(paths.results_path),
        "run_log_path": str(paths.run_log_path),
        "prepare_log_path": str(paths.prepare_log_path),
        "agent_log_path": str(paths.agent_log_path),
        "state_path": str(paths.state_path),
        "current_commit": _current_commit(paths.repo_dir),
        "data_ready": is_data_ready(paths.cache_dir),
        "tracked_changes": tracked_changes,
        "untracked_files": untracked_files,
        "unexpected_dirty_paths": unexpected_dirty_paths,
        "results_tail": _recent_lines(paths.results_path, lines=tail_lines),
        "run_log_tail": _recent_lines(paths.run_log_path, lines=tail_lines),
        "prepare_log_tail": _recent_lines(paths.prepare_log_path, lines=tail_lines),
        "agent_log_tail": _recent_lines(paths.agent_log_path, lines=tail_lines),
        "program_preview": _recent_lines(paths.program_path, lines=tail_lines),
        "run_state": _read_json_if_exists(paths.state_path),
    }


def _train_baseline(paths: Any, description: str) -> dict[str, Any]:
    _run_command_to_log(
        ["uv", "run", "train.py"],
        cwd=paths.repo_dir,
        log_path=paths.run_log_path,
        timeout=_settings.autoresearch_train_timeout,
        as_runtime_user=True,
        env=_autoresearch_env(paths.cache_dir),
    )
    summary = parse_training_summary(
        paths.run_log_path.read_text(encoding="utf-8", errors="ignore")
    )
    commit = _current_commit(paths.repo_dir)
    append_result_row(
        paths.results_path,
        commit=commit,
        val_bpb=summary.val_bpb,
        memory_gb=summary.peak_vram_mb / 1024,
        status="keep",
        description=description,
    )
    maybe_chown_for_runtime_user(paths.results_path)
    _commit_volumes()
    return {
        "run_log_path": str(paths.run_log_path),
        "results_path": str(paths.results_path),
        "commit": commit,
        "summary": {
            "val_bpb": summary.val_bpb,
            "training_seconds": summary.training_seconds,
            "total_seconds": summary.total_seconds,
            "peak_vram_mb": summary.peak_vram_mb,
            "mfu_percent": summary.mfu_percent,
            "total_tokens_m": summary.total_tokens_m,
            "num_steps": summary.num_steps,
            "num_params_m": summary.num_params_m,
            "depth": summary.depth,
        },
    }


def _run_claude(prompt: str, *, cwd: Path, max_turns: int, timeout: int) -> str:
    env = _autoresearch_env(Path(_settings.autoresearch_cache_root))
    require_claude_cli_auth(env)
    result = _run_command(
        [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "text",
            "--dangerously-skip-permissions",
            "--max-turns",
            str(max_turns),
        ],
        cwd=cwd,
        timeout=timeout,
        as_runtime_user=True,
        env=env,
    )
    return (result.stdout or result.stderr or "").strip()


def _run_claude_to_log(
    prompt: str,
    *,
    cwd: Path,
    max_turns: int,
    timeout: int,
    log_path: Path,
) -> None:
    env = _autoresearch_env(Path(_settings.autoresearch_cache_root))
    require_claude_cli_auth(env)
    _run_command_to_log(
        [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "text",
            "--dangerously-skip-permissions",
            "--max-turns",
            str(max_turns),
        ],
        cwd=cwd,
        log_path=log_path,
        timeout=timeout,
        as_runtime_user=True,
        env=env,
    )


def _summary_from_run_log(paths: Any) -> dict[str, Any] | None:
    if not paths.run_log_path.exists():
        return None
    try:
        summary = parse_training_summary(
            paths.run_log_path.read_text(encoding="utf-8", errors="ignore")
        )
    except ValueError:
        return None
    return {
        "val_bpb": summary.val_bpb,
        "training_seconds": summary.training_seconds,
        "total_seconds": summary.total_seconds,
        "peak_vram_mb": summary.peak_vram_mb,
        "mfu_percent": summary.mfu_percent,
        "total_tokens_m": summary.total_tokens_m,
        "num_steps": summary.num_steps,
        "num_params_m": summary.num_params_m,
        "depth": summary.depth,
    }


@app.function(image=autoresearch_image, volumes=AUTORESEARCH_VOLUMES, timeout=600)
def probe_autoresearch_environment() -> dict[str, str]:
    """Verify the runtime image has the required CLI surface."""
    versions = {
        "python": _run_command(
            ["python", "--version"], cwd=RUNTIME_APP_ROOT, timeout=30
        ).stdout.strip(),
        "git": _run_command(["git", "--version"], cwd=RUNTIME_APP_ROOT, timeout=30).stdout.strip(),
        "claude": _run_command(
            ["claude", "--version"],
            cwd=RUNTIME_APP_ROOT,
            timeout=30,
            as_runtime_user=True,
        ).stdout.strip(),
    }
    versions["workspace_root"] = _settings.autoresearch_workspace_root
    versions["cache_root"] = _settings.autoresearch_cache_root
    return versions


@app.function(
    image=autoresearch_image,
    volumes=AUTORESEARCH_VOLUMES,
    timeout=_settings.autoresearch_prepare_timeout,
)
def prepare_autoresearch_run(
    run_tag: str | None = None,
    num_shards: int | None = None,
) -> dict[str, Any]:
    """Seed the workspace repo, create the run branch, and prepare data if needed."""
    run_tag = resolve_run_tag(run_tag, purpose="prepare")
    paths, target_branch = _bootstrap_workspace(run_tag)
    _write_run_state(
        paths,
        mode="prepare",
        status="running",
        run_tag=run_tag,
        branch=target_branch,
        current_commit=_current_commit(paths.repo_dir),
    )
    try:
        prepared = _prepare_if_needed(
            paths,
            _settings.autoresearch_prepare_num_shards if num_shards is None else num_shards,
        )
    except Exception as exc:
        _write_run_state(
            paths,
            mode="prepare",
            status="failed",
            run_tag=run_tag,
            branch=target_branch,
            current_commit=_current_commit(paths.repo_dir),
            error=str(exc),
        )
        raise
    _write_run_state(
        paths,
        mode="prepare",
        status="completed",
        run_tag=run_tag,
        branch=target_branch,
        prepared=prepared,
        data_ready=is_data_ready(paths.cache_dir),
        current_commit=_current_commit(paths.repo_dir),
    )
    return {
        "run_tag": run_tag,
        "branch": target_branch,
        "repo_dir": str(paths.repo_dir),
        "repo_root_files": _repo_root_files(paths.repo_dir),
        "workspace_seed_source": AUTORESEARCH_WORKSPACE_SEED_SOURCE,
        "program_path": str(paths.program_path),
        "cache_dir": str(paths.cache_dir),
        "results_path": str(paths.results_path),
        "prepare_log_path": str(paths.prepare_log_path),
        "prepared": prepared,
        "data_ready": is_data_ready(paths.cache_dir),
        "commit": _current_commit(paths.repo_dir),
    }


@app.function(
    image=autoresearch_image,
    volumes=AUTORESEARCH_VOLUMES,
    timeout=600,
)
def get_autoresearch_program(run_tag: str) -> dict[str, Any]:
    """Return the current upstream program.md for a run tag."""
    paths, target_branch = _bootstrap_workspace(run_tag)
    content = paths.program_path.read_text(encoding="utf-8")
    return {
        "run_tag": run_tag,
        "branch": target_branch,
        "repo_dir": str(paths.repo_dir),
        "repo_root_files": _repo_root_files(paths.repo_dir),
        "workspace_seed_source": AUTORESEARCH_WORKSPACE_SEED_SOURCE,
        "program_path": str(paths.program_path),
        "program_text": content,
        "line_count": len(content.splitlines()),
        "current_commit": _current_commit(paths.repo_dir),
    }


@app.function(
    image=autoresearch_image,
    volumes=AUTORESEARCH_VOLUMES,
    timeout=600,
)
def set_autoresearch_program(run_tag: str, program_text: str) -> dict[str, Any]:
    """Write program.md for a run tag so the human can steer the agent loop."""
    paths, target_branch = _bootstrap_workspace(run_tag)
    normalized_text = program_text.rstrip("\n") + "\n"
    previous_text = paths.program_path.read_text(encoding="utf-8")
    updated = previous_text != normalized_text
    paths.program_path.write_text(normalized_text, encoding="utf-8")
    maybe_chown_for_runtime_user(paths.program_path)
    _write_run_state(
        paths,
        mode="set-program",
        status="completed",
        run_tag=run_tag,
        branch=target_branch,
        program_updated=updated,
        current_commit=_current_commit(paths.repo_dir),
    )
    return {
        "run_tag": run_tag,
        "branch": target_branch,
        "repo_dir": str(paths.repo_dir),
        "repo_root_files": _repo_root_files(paths.repo_dir),
        "workspace_seed_source": AUTORESEARCH_WORKSPACE_SEED_SOURCE,
        "program_path": str(paths.program_path),
        "program_updated": updated,
        "line_count": len(normalized_text.splitlines()),
        "current_commit": _current_commit(paths.repo_dir),
    }


@app.function(
    image=autoresearch_image,
    volumes=AUTORESEARCH_VOLUMES,
    timeout=600,
)
def inspect_autoresearch_run(run_tag: str, tail_lines: int = 20) -> dict[str, Any]:
    """Inspect the current git/log/results state for a prepared run."""
    paths, target_branch = _bootstrap_workspace(run_tag)
    return _inspect_run(paths, target_branch, tail_lines=tail_lines)


@app.function(
    image=autoresearch_image,
    volumes=AUTORESEARCH_VOLUMES,
    timeout=600,
)
def tail_autoresearch_artifact(
    run_tag: str, artifact: str = "agent", lines: int = 80
) -> dict[str, Any]:
    """Return the tail of a common runtime artifact for a run."""
    paths, target_branch = _bootstrap_workspace(run_tag)
    artifact_map = {
        "agent": paths.agent_log_path,
        "prepare": paths.prepare_log_path,
        "results": paths.results_path,
        "run": paths.run_log_path,
        "program": paths.program_path,
        "state": paths.state_path,
    }
    try:
        path = artifact_map[artifact]
    except KeyError as exc:
        raise ValueError(f"Unsupported artifact: {artifact}") from exc
    return {
        "run_tag": run_tag,
        "branch": target_branch,
        "artifact": artifact,
        "path": str(path),
        "lines": _recent_lines(path, lines=lines),
    }


@app.function(
    image=autoresearch_image,
    volumes=AUTORESEARCH_VOLUMES,
    gpu=_settings.autoresearch_gpu,
    timeout=_settings.autoresearch_train_timeout,
)
def run_autoresearch_baseline(
    run_tag: str | None = None,
    prepare_if_missing: bool = True,
) -> dict[str, Any]:
    """Run one direct baseline experiment without Claude in the loop."""
    run_tag = resolve_run_tag(run_tag, purpose="baseline")
    paths, target_branch = _bootstrap_workspace(run_tag)
    if prepare_if_missing:
        _prepare_if_needed(paths, _settings.autoresearch_prepare_num_shards)
    if not is_data_ready(paths.cache_dir):
        raise RuntimeError("Autoresearch cache is not ready. Run prepare_autoresearch_run first.")
    _write_run_state(
        paths,
        mode="baseline",
        status="running",
        run_tag=run_tag,
        branch=target_branch,
        current_commit=_current_commit(paths.repo_dir),
    )
    try:
        result = _train_baseline(paths, description="baseline")
    except Exception as exc:
        _write_run_state(
            paths,
            mode="baseline",
            status="failed",
            run_tag=run_tag,
            branch=target_branch,
            current_commit=_current_commit(paths.repo_dir),
            error=str(exc),
        )
        raise
    _write_run_state(
        paths,
        mode="baseline",
        status="completed",
        run_tag=run_tag,
        branch=target_branch,
        current_commit=result["commit"],
        summary=result["summary"],
    )
    result.update(
        {
            "run_tag": run_tag,
            "branch": target_branch,
            "repo_dir": str(paths.repo_dir),
        }
    )
    return result


@app.function(
    image=autoresearch_image,
    volumes=AUTORESEARCH_VOLUMES,
    gpu=_settings.autoresearch_gpu,
    secrets=get_modal_secrets(),
    timeout=_settings.autoresearch_claude_timeout,
)
def run_autoresearch_agent_loop(
    run_tag: str | None = None,
    prompt: str | None = None,
    max_turns: int = 200,
    max_experiments: int = 12,
    prepare_if_missing: bool = True,
) -> dict[str, Any]:
    """Run the primary upstream-style Claude autoresearch loop on Modal."""
    run_tag = resolve_run_tag(run_tag, purpose="agent-loop")
    paths, target_branch = _bootstrap_workspace(run_tag)
    if prepare_if_missing:
        _prepare_if_needed(paths, _settings.autoresearch_prepare_num_shards)
    if not is_data_ready(paths.cache_dir):
        raise RuntimeError("Autoresearch cache is not ready. Run prepare_autoresearch_run first.")

    agent_prompt = prompt or build_autoresearch_agent_prompt(
        run_tag,
        _settings.autoresearch_prepare_num_shards,
        max_experiments,
    )
    _write_run_state(
        paths,
        mode="agent-loop",
        status="running",
        run_tag=run_tag,
        branch=target_branch,
        max_turns=max_turns,
        max_experiments=max_experiments,
        current_commit=_current_commit(paths.repo_dir),
    )
    try:
        _run_claude_to_log(
            agent_prompt,
            cwd=paths.repo_dir,
            max_turns=max_turns,
            timeout=_settings.autoresearch_claude_timeout,
            log_path=paths.agent_log_path,
        )
    except Exception as exc:
        _write_run_state(
            paths,
            mode="agent-loop",
            status="failed",
            run_tag=run_tag,
            branch=target_branch,
            max_turns=max_turns,
            max_experiments=max_experiments,
            current_commit=_current_commit(paths.repo_dir),
            error=str(exc),
        )
        raise

    summary = _summary_from_run_log(paths)
    _write_run_state(
        paths,
        mode="agent-loop",
        status="completed",
        run_tag=run_tag,
        branch=target_branch,
        max_turns=max_turns,
        max_experiments=max_experiments,
        current_commit=_current_commit(paths.repo_dir),
        summary=summary,
    )
    payload = _inspect_run(paths, target_branch, tail_lines=30)
    payload["max_turns"] = max_turns
    payload["max_experiments"] = max_experiments
    payload["mode"] = "agent-loop"
    if summary is not None:
        payload["summary"] = summary
    return payload


@app.function(
    image=autoresearch_image,
    volumes=AUTORESEARCH_VOLUMES,
    gpu=_settings.autoresearch_gpu,
    secrets=get_modal_secrets(),
    timeout=_settings.autoresearch_claude_timeout,
)
def run_autoresearch_with_claude(
    run_tag: str,
    prompt: str | None = None,
    max_turns: int = 16,
    prepare_if_missing: bool = True,
) -> dict[str, Any]:
    """Run the legacy one-shot Claude baseline inside the GPU container."""
    paths, target_branch = _bootstrap_workspace(run_tag)
    if prepare_if_missing:
        _prepare_if_needed(paths, _settings.autoresearch_prepare_num_shards)
    if not is_data_ready(paths.cache_dir):
        raise RuntimeError("Autoresearch cache is not ready. Run prepare_autoresearch_run first.")

    cli_prompt = prompt or build_claude_baseline_prompt(
        run_tag,
        _settings.autoresearch_prepare_num_shards,
    )
    _write_run_state(
        paths,
        mode="claude-baseline",
        status="running",
        run_tag=run_tag,
        branch=target_branch,
        max_turns=max_turns,
        current_commit=_current_commit(paths.repo_dir),
    )
    try:
        cli_output = _run_claude(
            cli_prompt,
            cwd=paths.repo_dir,
            max_turns=max_turns,
            timeout=_settings.autoresearch_claude_timeout,
        )
    except Exception as exc:
        _write_run_state(
            paths,
            mode="claude-baseline",
            status="failed",
            run_tag=run_tag,
            branch=target_branch,
            max_turns=max_turns,
            current_commit=_current_commit(paths.repo_dir),
            error=str(exc),
        )
        raise

    payload: dict[str, Any] = {
        "run_tag": run_tag,
        "branch": target_branch,
        "repo_dir": str(paths.repo_dir),
        "results_path": str(paths.results_path),
        "cli_output": cli_output,
    }
    summary = _summary_from_run_log(paths)
    if summary is None and paths.run_log_path.exists():
        payload["run_log_tail"] = _tail_file(paths.run_log_path)
    elif summary is not None:
        payload["summary"] = summary
    _write_run_state(
        paths,
        mode="claude-baseline",
        status="completed",
        run_tag=run_tag,
        branch=target_branch,
        max_turns=max_turns,
        current_commit=_current_commit(paths.repo_dir),
        summary=summary,
    )
    _commit_volumes()
    return payload


@app.local_entrypoint()
def main(
    mode: str = "probe",
    run_tag: str = "",
    num_shards: int = 10,
    max_turns: int = 200,
    max_experiments: int = 12,
    artifact: str = "agent",
    lines: int = 80,
    program_file: str = "",
    prompt_file: str = "",
) -> None:
    """Convenience local entrypoint for common autoresearch flows."""
    requested_run_tag = run_tag or None
    prompt_text = Path(prompt_file).read_text(encoding="utf-8") if prompt_file else None
    if mode == "probe":
        result = probe_autoresearch_environment.remote()
    elif mode == "prepare":
        result = prepare_autoresearch_run.remote(run_tag=requested_run_tag, num_shards=num_shards)
    elif mode == "get-program":
        if requested_run_tag is None:
            raise ValueError("--run-tag is required for mode=get-program")
        result = get_autoresearch_program.remote(run_tag=run_tag)
    elif mode == "set-program":
        if requested_run_tag is None:
            raise ValueError("--run-tag is required for mode=set-program")
        if not program_file:
            raise ValueError("--program-file is required for mode=set-program")
        result = set_autoresearch_program.remote(
            run_tag=run_tag,
            program_text=Path(program_file).read_text(encoding="utf-8"),
        )
    elif mode == "inspect":
        if requested_run_tag is None:
            raise ValueError("--run-tag is required for mode=inspect")
        result = inspect_autoresearch_run.remote(run_tag=run_tag, tail_lines=lines)
    elif mode == "tail":
        if requested_run_tag is None:
            raise ValueError("--run-tag is required for mode=tail")
        result = tail_autoresearch_artifact.remote(run_tag=run_tag, artifact=artifact, lines=lines)
    elif mode == "baseline":
        result = run_autoresearch_baseline.remote(run_tag=requested_run_tag)
    elif mode == "agent-loop":
        result = run_autoresearch_agent_loop.remote(
            run_tag=requested_run_tag,
            prompt=prompt_text,
            max_turns=max_turns,
            max_experiments=max_experiments,
        )
    elif mode == "claude-baseline":
        if requested_run_tag is None:
            raise ValueError("--run-tag is required for mode=claude-baseline")
        result = run_autoresearch_with_claude.remote(
            run_tag=run_tag,
            prompt=prompt_text,
            max_turns=max_turns,
        )
    else:
        raise ValueError(f"Unsupported mode: {mode}")

    print(json.dumps(result, indent=2, sort_keys=True))
