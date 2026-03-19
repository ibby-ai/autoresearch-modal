"""Resolve CLI subcommands into executable or inspectable Modal command plans.

This module keeps the public CLI surface small and explicit:

- each top-level function maps one user-facing subcommand to the existing
  `agent_sandbox.autoresearch_app` `--mode` contract
- the mapping is represented as a :class:`CommandPlan`, which can either be
  executed for real or rendered as a dry-run payload
- live execution shells out through `python -m modal run`, rather than calling
  imported Modal `.remote()` functions directly, because the dedicated CLI runs
  outside an already-hydrated Modal app context

The result is a single place where developers can read exactly which runtime
entrypoint each CLI command targets, which arguments are forwarded, and what
shape the dry-run preview returns.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from argparse import Namespace
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_sandbox.config.settings import get_settings

MODAL_MODULE = "agent_sandbox.autoresearch_app"
FAILURE_CONTEXT_COMMANDS = {"run", "claude-baseline"}
FAILURE_CONTEXT_LINES = 20
RECONCILE_CONTEXT_COMMANDS = {"inspect", "tail"}
AUTORESEARCH_WORKSPACE_SEED_SOURCE = "vendored-project-root-allowlist"
_settings = get_settings()


class CliExecutionError(RuntimeError):
    """Raised when the CLI cannot complete a requested action cleanly.

    The CLI normalizes subprocess failures, missing JSON payloads, and other
    command-resolution problems into this exception so `main()` can print a
    short actionable message instead of a full Python traceback.
    """


@dataclass
class FileInput:
    """Metadata describing a local file consumed by a CLI command.

    Dry-run mode intentionally avoids echoing full file contents back to the
    terminal. Instead, commands such as `program set` and `run --prompt-file`
    resolve the file eagerly, validate that it exists, and emit only this
    compact metadata block.
    """

    flag: str
    path: str
    bytes: int
    line_count: int
    sha256_12: str


@dataclass
class CommandPlan:
    """Resolved representation of one CLI action.

    Attributes:
        command: Human-facing command label used in error messages and dry-run
            output, for example `program set` or `claude-baseline`.
        mode: Underlying `agent_sandbox.autoresearch_app --mode <mode>` target.
        modal_args: Concrete arguments passed after `-m
            agent_sandbox.autoresearch_app`.
        kwargs: Scalar arguments that the CLI exposes in JSON output so callers
            can see the resolved logical payload without reconstructing it from
            argv.
        file_inputs: Resolved metadata for local files referenced by the
            command, included only when relevant.
    """

    command: str
    mode: str
    modal_args: list[str]
    kwargs: dict[str, Any]
    file_inputs: list[FileInput] = field(default_factory=list)

    @property
    def target(self) -> str:
        """Return the symbolic Modal target shown in dry-run output."""
        return f"{MODAL_MODULE}::{self.mode}"

    def argv(self) -> list[str]:
        """Return the exact subprocess argv used for live execution.

        This makes the CLI behavior auditable: dry-run can print the same argv
        that `execute()` would launch, and tests can assert the full command
        without invoking Modal.
        """
        return [
            sys.executable,
            "-m",
            "modal",
            "run",
            "-q",
            "-m",
            MODAL_MODULE,
            *self.modal_args,
        ]

    def execute(self) -> dict[str, Any]:
        """Execute the resolved plan through the Modal CLI and parse JSON.

        The underlying runtime already emits JSON payloads for the dedicated
        CLI. This method preserves that contract and translates subprocess
        failures into :class:`CliExecutionError`.
        """
        if self.command in RECONCILE_CONTEXT_COMMANDS:
            host_payload = _resolve_host_follow_up_payload(self.command, self.kwargs)
            if host_payload is not None:
                return host_payload
        completed = subprocess.run(
            self.argv(),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            message = _format_subprocess_error(self.command, completed)
            context = _best_effort_failure_context(self.command, self.kwargs.get("run_tag"))
            if context is not None:
                message += "\nRun context:\n" + json.dumps(context, indent=2, sort_keys=True)
            raise CliExecutionError(message)
        payload = _parse_json_output(completed.stdout)
        return _maybe_reconcile_payload(self.command, payload, self.kwargs)

    def dry_run_payload(self) -> dict[str, Any]:
        """Render the resolved action without invoking Modal.

        The dry-run payload is meant for developers and automation. It exposes
        the exact argv, the logical command name, the symbolic target, scalar
        kwargs, and optional file metadata, but it does not leak full prompt or
        program file contents.
        """
        payload: dict[str, Any] = {
            "argv": self.argv(),
            "command": self.command,
            "dry_run": True,
            "kwargs": self.kwargs,
            "target": self.target,
        }
        if self.file_inputs:
            payload["file_inputs"] = [
                {
                    "flag": item.flag,
                    "path": item.path,
                    "bytes": item.bytes,
                    "line_count": item.line_count,
                    "sha256_12": item.sha256_12,
                }
                for item in self.file_inputs
            ]
        return payload


def _format_subprocess_error(command: str, completed: subprocess.CompletedProcess[str]) -> str:
    """Convert a failed Modal subprocess into a concise user-facing message.

    Modal failures often include a large traceback or log preamble. The CLI
    keeps the last few lines so the immediate cause is still visible while
    avoiding an overwhelming wall of output.
    """
    stderr = (completed.stderr or "").strip()
    stdout = (completed.stdout or "").strip()
    detail_source = stderr if stderr else stdout
    if detail_source:
        detail_lines = detail_source.splitlines()[-8:]
        detail = "\n".join(detail_lines)
        return (
            f"`autoresearch-modal {command}` failed with exit code {completed.returncode}.\n"
            f"Last output:\n{detail}"
        )
    return f"`autoresearch-modal {command}` failed with exit code {completed.returncode}."


def _parse_json_output(output: str) -> dict[str, Any]:
    """Parse the JSON object emitted by the Modal runtime.

    The runtime is expected to print a single JSON object. In practice, Modal
    may prepend incidental text in some environments, so this parser falls back
    to scanning for a trailing JSON object before giving up.
    """
    text = output.strip()
    if not text:
        raise CliExecutionError("Modal command completed without returning JSON output.")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                parsed, end = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if text[index + end :].strip():
                continue
            break
        else:
            raise CliExecutionError(
                f"Modal command returned non-JSON output.\nLast output:\n{text.splitlines()[-1]}"
            ) from None
    if not isinstance(parsed, dict):
        raise CliExecutionError("Modal command returned JSON that was not an object.")
    return parsed


def _modal_volume_remote_path(run_tag: str, suffix: str) -> str:
    return f"/{run_tag}/{suffix}"


def _absolute_workspace_path(run_tag: str, suffix: str) -> str:
    return str(Path(_settings.autoresearch_workspace_root) / run_tag / suffix)


def _host_artifact_paths(path_value: Any, *, run_tag: str, suffix: str) -> tuple[str, str]:
    display_path = (
        str(path_value)
        if isinstance(path_value, str) and path_value
        else _absolute_workspace_path(run_tag, suffix)
    )
    return display_path, _modal_volume_remote_path(run_tag, suffix)


def _read_volume_file_text(remote_path: str) -> str | None:
    with tempfile.TemporaryDirectory() as tmpdir:
        local_destination = Path(tmpdir) / Path(remote_path).name
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "modal",
                "volume",
                "get",
                _settings.autoresearch_workspace_vol_name,
                remote_path,
                str(local_destination),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0 or not local_destination.exists():
            return None
        return local_destination.read_text(encoding="utf-8", errors="ignore")


def _read_volume_file_lines(remote_path: str, *, lines: int) -> list[str]:
    content = _read_volume_file_text(remote_path)
    if content is None:
        return []
    return content.splitlines()[-lines:]


def _read_host_run_state(run_tag: str) -> dict[str, Any] | None:
    content = _read_volume_file_text(_modal_volume_remote_path(run_tag, "modal-run-state.json"))
    if content is None:
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _git_status(repo_dir: Path) -> tuple[list[dict[str, str]], list[str]]:
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return [], []
    tracked_changes: list[dict[str, str]] = []
    untracked_files: list[str] = []
    for line in completed.stdout.splitlines():
        if len(line) < 3:
            continue
        status = line[:2]
        path = line[3:] if len(line) > 3 and line[2] == " " else line[2:]
        if status == "??":
            untracked_files.append(path)
            continue
        tracked_changes.append({"status": status.strip(), "path": path})
    return tracked_changes, untracked_files


def _current_commit(repo_dir: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    return (completed.stdout or "").strip()


def _read_host_repo_snapshot(run_tag: str) -> dict[str, Any] | None:
    with tempfile.TemporaryDirectory() as tmpdir:
        local_destination = Path(tmpdir) / "repo"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "modal",
                "volume",
                "get",
                _settings.autoresearch_workspace_vol_name,
                _modal_volume_remote_path(run_tag, "repo"),
                str(local_destination),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0 or not local_destination.exists():
            return None
        tracked_changes, untracked_files = _git_status(local_destination)
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
            "repo_dir": str(local_destination),
            "repo_root_files": sorted(
                path.name for path in local_destination.iterdir() if path.name != ".git"
            ),
            "current_commit": _current_commit(local_destination),
            "tracked_changes": tracked_changes,
            "untracked_files": untracked_files,
            "unexpected_dirty_paths": unexpected_dirty_paths,
        }


def _host_terminal_run_state(run_tag: str) -> dict[str, Any] | None:
    state = _read_host_run_state(run_tag)
    if state is None:
        return None
    if state.get("status") != "running":
        return state

    app_id = state.get("modal_app_id")
    app_record = _lookup_modal_app_record(app_id) if isinstance(app_id, str) and app_id else None
    if app_record is None:
        reconciled_state = _reconcile_run_state(
            run_tag,
            state_status="stale",
            terminal_reason="modal_app_not_found",
        )
        post_reconcile_state = _read_host_run_state(run_tag)
        if isinstance(post_reconcile_state, dict) and post_reconcile_state.get("status") != "running":
            return post_reconcile_state
        return reconciled_state or {
            **state,
            "status": "stale",
            "terminal_reason": "modal_app_not_found",
        }
    if app_record["running_tasks"] > 0:
        enriched_state = {
            **state,
            "modal_app_running_tasks": app_record["running_tasks"],
        }
        if app_record["state"]:
            enriched_state["modal_app_state"] = app_record["state"]
        return enriched_state

    reconciled_state = _reconcile_run_state(
        run_tag,
        state_status="interrupted",
        terminal_reason="modal_app_stopped",
        modal_app_state=str(app_record["state"] or ""),
        modal_app_running_tasks=int(app_record["running_tasks"]),
    )
    post_reconcile_state = _read_host_run_state(run_tag)
    if isinstance(post_reconcile_state, dict) and post_reconcile_state.get("status") != "running":
        return post_reconcile_state
    return reconciled_state or {
        **state,
        "status": "interrupted",
        "terminal_reason": "modal_app_stopped",
        "modal_app_running_tasks": app_record["running_tasks"],
        "modal_app_state": app_record["state"],
    }


def _host_follow_up_context(run_tag: str, *, lines: int) -> dict[str, Any] | None:
    run_state = _host_terminal_run_state(run_tag)
    if run_state is None or run_state.get("status") == "running":
        return None
    context: dict[str, Any] = {"run_state": run_state}
    for key, suffix in (
        ("agent_log_tail", "agent.log"),
        ("run_log_tail", "repo/run.log"),
        ("prepare_log_tail", "prepare.log"),
        ("results_tail", "repo/results.tsv"),
    ):
        value = _read_volume_file_lines(_modal_volume_remote_path(run_tag, suffix), lines=lines)
        if value:
            context[key] = value
    return context


def _host_follow_up_inspect_payload(run_tag: str, *, lines: int) -> dict[str, Any] | None:
    run_state = _host_terminal_run_state(run_tag)
    if run_state is None or run_state.get("status") == "running":
        return None
    repo_snapshot = _read_host_repo_snapshot(run_tag) or {}
    program_path = str(
        run_state.get("program_path")
        or _absolute_workspace_path(run_tag, "repo/program.md")
    )
    results_path = str(
        run_state.get("results_path")
        or _absolute_workspace_path(run_tag, "repo/results.tsv")
    )
    run_log_path = str(
        run_state.get("run_log_path")
        or _absolute_workspace_path(run_tag, "repo/run.log")
    )
    prepare_log_path = str(
        run_state.get("prepare_log_path")
        or _absolute_workspace_path(run_tag, "prepare.log")
    )
    agent_log_path = str(
        run_state.get("agent_log_path")
        or _absolute_workspace_path(run_tag, "agent.log")
    )
    payload: dict[str, Any] = {
        "run_tag": run_tag,
        "branch": repo_snapshot.get("branch") or run_state.get("branch"),
        "repo_dir": run_state.get("repo_dir") or _absolute_workspace_path(run_tag, "repo"),
        "repo_root_files": repo_snapshot.get("repo_root_files", []),
        "workspace_seed_source": AUTORESEARCH_WORKSPACE_SEED_SOURCE,
        "program_path": program_path,
        "results_path": results_path,
        "run_log_path": run_log_path,
        "prepare_log_path": prepare_log_path,
        "agent_log_path": agent_log_path,
        "state_path": _absolute_workspace_path(run_tag, "modal-run-state.json"),
        "current_commit": repo_snapshot.get("current_commit") or run_state.get("current_commit"),
        "data_ready": run_state.get("data_ready"),
        "tracked_changes": repo_snapshot.get("tracked_changes", []),
        "untracked_files": repo_snapshot.get("untracked_files", []),
        "unexpected_dirty_paths": repo_snapshot.get("unexpected_dirty_paths", []),
        "program_preview": _read_volume_file_lines(
            _modal_volume_remote_path(run_tag, "repo/program.md"),
            lines=lines,
        ),
        "results_tail": _read_volume_file_lines(
            _modal_volume_remote_path(run_tag, "repo/results.tsv"),
            lines=lines,
        ),
        "run_log_tail": _read_volume_file_lines(
            _modal_volume_remote_path(run_tag, "repo/run.log"),
            lines=lines,
        ),
        "prepare_log_tail": _read_volume_file_lines(
            _modal_volume_remote_path(run_tag, "prepare.log"),
            lines=lines,
        ),
        "agent_log_tail": _read_volume_file_lines(
            _modal_volume_remote_path(run_tag, "agent.log"),
            lines=lines,
        ),
        "run_state": run_state,
    }
    return payload


def _host_follow_up_tail_payload(run_tag: str, *, artifact: str, lines: int) -> dict[str, Any] | None:
    run_state = _host_terminal_run_state(run_tag)
    if run_state is None or run_state.get("status") == "running":
        return None
    artifact_paths = {
        "agent": _host_artifact_paths(
            run_state.get("agent_log_path"),
            run_tag=run_tag,
            suffix="agent.log",
        ),
        "prepare": _host_artifact_paths(
            run_state.get("prepare_log_path"),
            run_tag=run_tag,
            suffix="prepare.log",
        ),
        "results": _host_artifact_paths(
            run_state.get("results_path"),
            run_tag=run_tag,
            suffix="repo/results.tsv",
        ),
        "run": _host_artifact_paths(
            run_state.get("run_log_path"),
            run_tag=run_tag,
            suffix="repo/run.log",
        ),
        "program": _host_artifact_paths(
            run_state.get("program_path"),
            run_tag=run_tag,
            suffix="repo/program.md",
        ),
        "state": (
            _absolute_workspace_path(run_tag, "modal-run-state.json"),
            _modal_volume_remote_path(run_tag, "modal-run-state.json"),
        ),
    }
    try:
        display_path, remote_path = artifact_paths[artifact]
    except KeyError as exc:
        raise ValueError(f"Unsupported artifact: {artifact}") from exc
    if artifact == "state":
        rendered_lines = json.dumps(run_state, indent=2, sort_keys=True).splitlines()
    else:
        rendered_lines = _read_volume_file_lines(remote_path, lines=lines)
    return {
        "run_tag": run_tag,
        "branch": run_state.get("branch"),
        "artifact": artifact,
        "path": display_path,
        "lines": rendered_lines,
        "run_state": run_state,
    }


def _resolve_host_follow_up_payload(command: str, kwargs: dict[str, Any]) -> dict[str, Any] | None:
    if command == "inspect":
        run_tag = kwargs.get("run_tag")
        tail_lines = kwargs.get("tail_lines")
        if not isinstance(run_tag, str) or not run_tag or not isinstance(tail_lines, int):
            return None
        return _host_follow_up_inspect_payload(run_tag, lines=tail_lines)
    if command == "tail":
        run_tag = kwargs.get("run_tag")
        artifact = kwargs.get("artifact")
        lines = kwargs.get("lines")
        if not isinstance(run_tag, str) or not run_tag:
            return None
        if not isinstance(artifact, str) or not artifact:
            return None
        if not isinstance(lines, int):
            return None
        return _host_follow_up_tail_payload(run_tag, artifact=artifact, lines=lines)
    return None


def _best_effort_failure_context(command: str, run_tag: Any) -> dict[str, Any] | None:
    """Fetch compact inspect context for failed run-tagged commands when possible."""
    if command not in FAILURE_CONTEXT_COMMANDS:
        return None
    if not isinstance(run_tag, str) or not run_tag:
        return None
    context = _host_follow_up_context(run_tag, lines=FAILURE_CONTEXT_LINES)
    if context is not None:
        return context
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "modal",
            "run",
            "-q",
            "-m",
            MODAL_MODULE,
            "--mode",
            "inspect",
            "--run-tag",
            run_tag,
            "--lines",
            str(FAILURE_CONTEXT_LINES),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    try:
        payload = _parse_json_output(completed.stdout)
    except CliExecutionError:
        return None
    payload = _maybe_reconcile_payload("inspect", payload, {"run_tag": run_tag})
    context: dict[str, Any] = {}
    run_state = payload.get("run_state")
    if isinstance(run_state, dict):
        context["run_state"] = run_state
    for key in (
        "agent_log_tail",
        "run_log_tail",
        "prepare_log_tail",
        "results_tail",
        "unexpected_dirty_paths",
    ):
        value = payload.get(key)
        if value:
            context[key] = value
    return context or None


def _lookup_modal_app_record(app_id: str) -> dict[str, Any] | None:
    completed = subprocess.run(
        [sys.executable, "-m", "modal", "app", "list", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list):
        return None
    for item in payload:
        if not isinstance(item, dict) or item.get("App ID") != app_id:
            continue
        tasks = item.get("Tasks")
        try:
            running_tasks = int(tasks) if tasks is not None else 0
        except (TypeError, ValueError):
            running_tasks = 0
        return {
            "app_id": app_id,
            "state": item.get("State"),
            "running_tasks": running_tasks,
            "stopped_at": item.get("Stopped at"),
        }
    return None


def _reconcile_run_state(
    run_tag: str,
    *,
    state_status: str,
    terminal_reason: str,
    modal_app_state: str = "",
    modal_app_running_tasks: int = 0,
) -> dict[str, Any] | None:
    argv = [
        sys.executable,
        "-m",
        "modal",
        "run",
        "-q",
        "-m",
        MODAL_MODULE,
        "--mode",
        "reconcile-state",
        "--run-tag",
        run_tag,
        "--state-status",
        state_status,
        "--terminal-reason",
        terminal_reason,
        "--modal-app-running-tasks",
        str(modal_app_running_tasks),
    ]
    if modal_app_state:
        argv.extend(["--modal-app-state", modal_app_state])
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    try:
        payload = _parse_json_output(completed.stdout)
    except CliExecutionError:
        return None
    return payload if isinstance(payload, dict) else None


def _maybe_reconcile_payload(command: str, payload: dict[str, Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    if command not in RECONCILE_CONTEXT_COMMANDS:
        return payload

    run_state = payload.get("run_state")
    if not isinstance(run_state, dict) or run_state.get("status") != "running":
        return payload

    run_tag = kwargs.get("run_tag") or payload.get("run_tag")
    if not isinstance(run_tag, str) or not run_tag:
        return payload

    modal_app_id = run_state.get("modal_app_id")
    if not isinstance(modal_app_id, str) or not modal_app_id:
        return payload

    modal_app = _lookup_modal_app_record(modal_app_id)
    if modal_app is None:
        reconciled_state = _reconcile_run_state(
            run_tag,
            state_status="stale",
            terminal_reason="modal_app_not_found",
        ) or {
            **run_state,
            "status": "stale",
            "terminal_reason": "modal_app_not_found",
        }
    elif modal_app["running_tasks"] > 0 and not modal_app["stopped_at"]:
        enriched_state = {
            **run_state,
            "modal_app_running_tasks": modal_app["running_tasks"],
        }
        if modal_app["state"]:
            enriched_state["modal_app_state"] = modal_app["state"]
        payload["run_state"] = enriched_state
        return payload
    else:
        reconciled_state = _reconcile_run_state(
            run_tag,
            state_status="interrupted",
            terminal_reason="modal_app_stopped",
            modal_app_state=str(modal_app["state"] or ""),
            modal_app_running_tasks=int(modal_app["running_tasks"]),
        ) or {
            **run_state,
            "status": "interrupted",
            "terminal_reason": "modal_app_stopped",
            "modal_app_running_tasks": modal_app["running_tasks"],
            "modal_app_state": modal_app["state"],
        }

    payload["run_state"] = reconciled_state
    if payload.get("artifact") == "state":
        payload["lines"] = json.dumps(reconciled_state, indent=2, sort_keys=True).splitlines()
    return payload


def _load_file(path_value: str, *, flag: str) -> FileInput:
    """Resolve a local file and return metadata suitable for dry-run output.

    The file is read eagerly so both live and dry-run paths fail early when the
    path is invalid. Only metadata is retained here; the file contents continue
    to flow to the runtime through the forwarded CLI argument.
    """
    path = Path(path_value).expanduser().resolve()
    content = path.read_text(encoding="utf-8")
    return FileInput(
        flag=flag,
        path=str(path),
        bytes=len(content.encode("utf-8")),
        line_count=len(content.splitlines()),
        sha256_12=hashlib.sha256(content.encode("utf-8")).hexdigest()[:12],
    )


def _plan(
    command: str,
    mode: str,
    *modal_args: str,
    kwargs: dict[str, Any] | None = None,
    file_inputs: list[FileInput] | None = None,
) -> CommandPlan:
    """Build a :class:`CommandPlan` from already-resolved command details."""
    return CommandPlan(
        command=command,
        mode=mode,
        modal_args=list(modal_args),
        kwargs={} if kwargs is None else kwargs,
        file_inputs=[] if file_inputs is None else file_inputs,
    )


def probe(_args: Namespace) -> CommandPlan:
    """Verify the remote Modal runtime environment.

    `probe` is the safest live command: it is read-only and returns version and
    path information for the Python runtime, Git, Claude CLI, workspace root,
    and cache root inside the Modal image.
    """
    return _plan("probe", "probe", "--mode", "probe")


def prepare(args: Namespace) -> CommandPlan:
    """Prepare or reuse a run workspace and warm the upstream cache.

    This is the canonical setup step before baseline or Claude-driven runs. It
    can accept an explicit `run_tag` or let the runtime generate one, and it
    forwards `--num-shards` for the upstream cache bootstrap path.
    """
    modal_args = ["--mode", "prepare", "--num-shards", str(args.num_shards)]
    if args.run_tag:
        modal_args.extend(["--run-tag", args.run_tag])
    return _plan(
        "prepare",
        "prepare",
        *modal_args,
        kwargs={
            "num_shards": args.num_shards,
            "run_tag": args.run_tag,
        },
    )


def program_get(args: Namespace) -> CommandPlan:
    """Fetch the current `program.md` for an existing run tag.

    This command is read-only and intentionally requires an explicit run tag so
    developers do not accidentally read from an implicitly generated run.
    """
    return _plan(
        "program get",
        "get-program",
        "--mode",
        "get-program",
        "--run-tag",
        args.run_tag,
        kwargs={"run_tag": args.run_tag},
    )


def program_set(args: Namespace) -> CommandPlan:
    """Replace `program.md` for an existing run tag from a local markdown file.

    The CLI validates the local file path immediately. In dry-run mode it emits
    only metadata for that file, while live execution forwards the original
    `--program-file` argument to the Modal runtime.
    """
    file_input = _load_file(args.program_file, flag="--program-file")
    return _plan(
        "program set",
        "set-program",
        "--mode",
        "set-program",
        "--run-tag",
        args.run_tag,
        "--program-file",
        args.program_file,
        kwargs={"run_tag": args.run_tag},
        file_inputs=[file_input],
    )


def baseline(args: Namespace) -> CommandPlan:
    """Run a direct non-Claude baseline smoke for a run workspace.

    This is the fastest way to confirm that the vendored upstream project can
    execute end-to-end in the prepared Modal environment before involving the
    longer Claude-driven loop.
    """
    modal_args = ["--mode", "baseline"]
    if args.run_tag:
        modal_args.extend(["--run-tag", args.run_tag])
    return _plan(
        "baseline",
        "baseline",
        *modal_args,
        kwargs={"run_tag": args.run_tag},
    )


def run(args: Namespace) -> CommandPlan:
    """Run the primary bounded Claude-driven experiment loop.

    This command corresponds to the runtime's `agent-loop` mode. It can reuse
    an existing run tag or let the runtime mint one, and it optionally forwards
    a prompt override file for debugging or prompt iteration.
    """
    modal_args = [
        "--mode",
        "agent-loop",
        "--max-turns",
        str(args.max_turns),
        "--max-experiments",
        str(args.max_experiments),
    ]
    file_inputs: list[FileInput] = []
    if args.run_tag:
        modal_args.extend(["--run-tag", args.run_tag])
    if args.prompt_file:
        modal_args.extend(["--prompt-file", args.prompt_file])
        file_inputs.append(_load_file(args.prompt_file, flag="--prompt-file"))
    return _plan(
        "run",
        "agent-loop",
        *modal_args,
        kwargs={
            "max_experiments": args.max_experiments,
            "max_turns": args.max_turns,
            "run_tag": args.run_tag,
        },
        file_inputs=file_inputs,
    )


def inspect(args: Namespace) -> CommandPlan:
    """Inspect the current state of an existing run.

    `inspect` is read-only. It returns a compact operational snapshot covering
    recent logs, git state, and other run artifacts for the provided tag.
    """
    return _plan(
        "inspect",
        "inspect",
        "--mode",
        "inspect",
        "--run-tag",
        args.run_tag,
        "--lines",
        str(args.lines),
        kwargs={
            "run_tag": args.run_tag,
            "tail_lines": args.lines,
        },
    )


def tail(args: Namespace) -> CommandPlan:
    """Tail one named artifact for an existing run tag.

    This is a narrower companion to `inspect` when a developer already knows
    which artifact stream matters and only wants the last N lines.
    """
    return _plan(
        "tail",
        "tail",
        "--mode",
        "tail",
        "--run-tag",
        args.run_tag,
        "--artifact",
        args.artifact,
        "--lines",
        str(args.lines),
        kwargs={
            "artifact": args.artifact,
            "lines": args.lines,
            "run_tag": args.run_tag,
        },
    )


def claude_baseline(args: Namespace) -> CommandPlan:
    """Run the legacy one-shot Claude baseline for bounded debugging.

    This is retained as a focused troubleshooting path rather than the primary
    workflow. It always requires an explicit run tag because it targets an
    already prepared workspace.
    """
    modal_args = [
        "--mode",
        "claude-baseline",
        "--run-tag",
        args.run_tag,
        "--max-turns",
        str(args.max_turns),
    ]
    file_inputs: list[FileInput] = []
    if args.prompt_file:
        modal_args.extend(["--prompt-file", args.prompt_file])
        file_inputs.append(_load_file(args.prompt_file, flag="--prompt-file"))
    return _plan(
        "claude-baseline",
        "claude-baseline",
        *modal_args,
        kwargs={
            "max_turns": args.max_turns,
            "run_tag": args.run_tag,
        },
        file_inputs=file_inputs,
    )
