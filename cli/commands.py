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
from argparse import Namespace
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MODAL_MODULE = "agent_sandbox.autoresearch_app"


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
        completed = subprocess.run(
            self.argv(),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise CliExecutionError(_format_subprocess_error(self.command, completed))
        return _parse_json_output(completed.stdout)

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
