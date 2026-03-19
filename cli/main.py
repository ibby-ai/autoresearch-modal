"""Define the public argparse surface for the `autoresearch-modal` CLI.

`cli.commands` owns the command-to-runtime mapping; this module owns the user
experience of the installed console script:

- normalize global flags such as `--dry-run`
- declare the supported command tree and each command's developer-facing help
- execute or preview the resolved command plan
- keep terminal failures concise and machine-readable successes as JSON
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from agent_sandbox.config.settings import get_settings

from . import commands
from .commands import CliExecutionError

DEFAULT_MAX_EXPERIMENTS = 12
DEFAULT_MAX_TURNS = 200
DEFAULT_TAIL_LINES = 80
DEFAULT_INSPECT_LINES = 20
DEFAULT_CLAUDE_BASELINE_MAX_TURNS = 16


def _normalize_argv(argv: Sequence[str] | None) -> tuple[list[str], bool]:
    """Accept `--dry-run` before or after a subcommand.

    `argparse` handles subcommand-local flags most naturally when they appear in
    one position, but the CLI contract allows developers to place `--dry-run`
    either before or after the subcommand. This helper strips it out first and
    returns a boolean that `main()` can apply uniformly.
    """
    values = list(sys.argv[1:] if argv is None else argv)
    dry_run_requested = False
    normalized: list[str] = []
    for value in values:
        if value == "--dry-run":
            dry_run_requested = True
            continue
        normalized.append(value)
    return normalized, dry_run_requested


def build_parser() -> argparse.ArgumentParser:
    """Construct the full argparse tree for the dedicated developer CLI.

    The parser mirrors the supported runtime workflow:
    `probe`, `prepare`, `program get`, `program set`, `baseline`, `run`,
    `inspect`, `tail`, and `claude-baseline`.
    """
    dry_run_parent = argparse.ArgumentParser(add_help=False)
    dry_run_parent.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and print the command payload without calling Modal.",
    )
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="autoresearch-modal",
        description="Developer-facing CLI for the autoresearch Modal runtime.",
        parents=[dry_run_parent],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe_parser = subparsers.add_parser(
        "probe",
        parents=[dry_run_parent],
        help="Verify the Modal runtime image and CLI surface.",
    )
    probe_parser.set_defaults(handler=commands.probe)

    prepare_parser = subparsers.add_parser(
        "prepare",
        parents=[dry_run_parent],
        help="Prepare a workspace repo and warm the autoresearch cache.",
    )
    prepare_parser.add_argument("--run-tag", help="Existing or new run tag to prepare.")
    prepare_parser.add_argument(
        "--num-shards",
        type=int,
        default=settings.autoresearch_prepare_num_shards,
        help="Shard count for prepare.py when bootstrapping the cache.",
    )
    prepare_parser.set_defaults(handler=commands.prepare)

    program_parser = subparsers.add_parser(
        "program",
        parents=[dry_run_parent],
        help="Read or update program.md for a prepared run.",
    )
    program_subparsers = program_parser.add_subparsers(dest="program_command", required=True)

    program_get_parser = program_subparsers.add_parser(
        "get",
        parents=[dry_run_parent],
        help="Print the current program.md for a run tag.",
    )
    program_get_parser.add_argument("--run-tag", required=True, help="Run tag to inspect.")
    program_get_parser.set_defaults(handler=commands.program_get)

    program_set_parser = program_subparsers.add_parser(
        "set",
        parents=[dry_run_parent],
        help="Replace program.md for a run tag from a local file.",
    )
    program_set_parser.add_argument("--run-tag", required=True, help="Run tag to update.")
    program_set_parser.add_argument(
        "--file",
        "--program-file",
        dest="program_file",
        required=True,
        help="Local markdown file to upload as program.md.",
    )
    program_set_parser.set_defaults(handler=commands.program_set)

    baseline_parser = subparsers.add_parser(
        "baseline",
        parents=[dry_run_parent],
        help="Run one direct baseline smoke without Claude in the loop.",
    )
    baseline_parser.add_argument("--run-tag", help="Existing or new run tag to use.")
    baseline_parser.set_defaults(handler=commands.baseline)

    run_parser = subparsers.add_parser(
        "run",
        aliases=["agent-loop"],
        parents=[dry_run_parent],
        help="Run the primary Claude-driven autoresearch loop.",
    )
    run_parser.add_argument("--run-tag", help="Existing or new run tag to use.")
    run_parser.add_argument(
        "--max-experiments",
        type=int,
        default=DEFAULT_MAX_EXPERIMENTS,
        help="Maximum completed experiment attempts for the session.",
    )
    run_parser.add_argument(
        "--max-turns",
        type=int,
        default=DEFAULT_MAX_TURNS,
        help="Maximum Claude CLI turns for the session.",
    )
    run_parser.add_argument(
        "--prompt-file",
        help="Optional local prompt file that overrides the default agent prompt.",
    )
    run_parser.set_defaults(handler=commands.run)

    inspect_parser = subparsers.add_parser(
        "inspect",
        parents=[dry_run_parent],
        help="Inspect logs, git state, and results for a run tag.",
    )
    inspect_parser.add_argument("--run-tag", required=True, help="Run tag to inspect.")
    inspect_parser.add_argument(
        "--lines",
        type=int,
        default=DEFAULT_INSPECT_LINES,
        help="Number of recent lines to include from common artifacts.",
    )
    inspect_parser.set_defaults(handler=commands.inspect)

    tail_parser = subparsers.add_parser(
        "tail",
        parents=[dry_run_parent],
        help="Tail one artifact for a run tag.",
    )
    tail_parser.add_argument("--run-tag", required=True, help="Run tag to inspect.")
    tail_parser.add_argument(
        "--artifact",
        default="agent",
        choices=["agent", "prepare", "results", "run", "program", "state"],
        help="Artifact to tail.",
    )
    tail_parser.add_argument(
        "--lines",
        type=int,
        default=DEFAULT_TAIL_LINES,
        help="Number of recent lines to return.",
    )
    tail_parser.set_defaults(handler=commands.tail)

    claude_baseline_parser = subparsers.add_parser(
        "claude-baseline",
        parents=[dry_run_parent],
        help="Run the legacy one-shot Claude baseline for debugging.",
    )
    claude_baseline_parser.add_argument("--run-tag", required=True, help="Run tag to use.")
    claude_baseline_parser.add_argument(
        "--max-turns",
        type=int,
        default=DEFAULT_CLAUDE_BASELINE_MAX_TURNS,
        help="Maximum Claude CLI turns for the bounded baseline.",
    )
    claude_baseline_parser.add_argument(
        "--prompt-file",
        help="Optional local prompt file that overrides the default baseline prompt.",
    )
    claude_baseline_parser.set_defaults(handler=commands.claude_baseline)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI entrypoint and emit a JSON payload or concise error.

    Success is always printed as formatted JSON so developers can inspect the
    resolved payload directly or pipe it into tooling. Failures are collapsed to
    a short stderr message and a non-zero exit code.
    """
    parser = build_parser()
    normalized_argv, dry_run_requested = _normalize_argv(argv)
    try:
        args = parser.parse_args(normalized_argv)
        plan = args.handler(args)
        payload = plan.dry_run_payload() if (args.dry_run or dry_run_requested) else plan.execute()
    except FileNotFoundError as exc:
        print(f"Input file not found: {exc.filename}", file=sys.stderr)
        return 1
    except CliExecutionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
