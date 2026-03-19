"""Tests for the dedicated autoresearch console script."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cli import commands as cli_commands
from cli import main as cli_main


def _load_stdout_json(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    captured = capsys.readouterr()
    return json.loads(captured.out)


def test_probe_command_executes_modal_runner_and_prints_json(monkeypatch, capsys):
    calls: list[list[str]] = []

    def fake_run(argv, capture_output, text, check):
        calls.append(list(argv))
        assert capture_output is True
        assert text is True
        assert check is False
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout='{"python": "Python 3.11.9"}\n',
            stderr="",
        )

    monkeypatch.setattr(cli_commands.subprocess, "run", fake_run)

    exit_code = cli_main.main(["probe"])

    assert exit_code == 0
    assert _load_stdout_json(capsys) == {"python": "Python 3.11.9"}
    assert calls == [
        [
            sys.executable,
            "-m",
            "modal",
            "run",
            "-q",
            "-m",
            "agent_sandbox.autoresearch_app",
            "--mode",
            "probe",
        ]
    ]


@pytest.mark.parametrize(
    ("argv", "expected_kwargs"),
    [
        (
            ["--dry-run", "prepare", "--num-shards", "7"],
            {"num_shards": 7, "run_tag": None},
        ),
        (
            ["--dry-run", "baseline"],
            {"run_tag": None},
        ),
        (
            ["--dry-run", "run"],
            {"max_experiments": 12, "max_turns": 200, "run_tag": None},
        ),
    ],
)
def test_first_time_commands_allow_missing_run_tag(capsys, argv, expected_kwargs):
    exit_code = cli_main.main(argv)

    assert exit_code == 0
    payload = _load_stdout_json(capsys)
    assert payload["dry_run"] is True
    assert payload["kwargs"] == expected_kwargs


def test_prepare_dry_run_prints_exact_subprocess_argv(capsys):
    exit_code = cli_main.main(
        ["prepare", "--dry-run", "--run-tag", "mar16-prepare", "--num-shards", "7"]
    )

    assert exit_code == 0
    assert _load_stdout_json(capsys) == {
        "argv": [
            sys.executable,
            "-m",
            "modal",
            "run",
            "-q",
            "-m",
            "agent_sandbox.autoresearch_app",
            "--mode",
            "prepare",
            "--num-shards",
            "7",
            "--run-tag",
            "mar16-prepare",
        ],
        "command": "prepare",
        "dry_run": True,
        "kwargs": {"num_shards": 7, "run_tag": "mar16-prepare"},
        "target": "agent_sandbox.autoresearch_app::prepare",
    }


def test_run_dry_run_uses_file_metadata_not_prompt_contents(tmp_path: Path, capsys):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("please run a focused experiment\n", encoding="utf-8")

    exit_code = cli_main.main(
        [
            "run",
            "--dry-run",
            "--run-tag",
            "mar16-run",
            "--prompt-file",
            str(prompt_file),
            "--max-experiments",
            "4",
            "--max-turns",
            "50",
        ]
    )

    assert exit_code == 0
    payload = _load_stdout_json(capsys)
    assert payload["kwargs"] == {
        "max_experiments": 4,
        "max_turns": 50,
        "run_tag": "mar16-run",
    }
    assert payload["file_inputs"] == [
        {
            "bytes": len(b"please run a focused experiment\n"),
            "flag": "--prompt-file",
            "line_count": 1,
            "path": str(prompt_file.resolve()),
            "sha256_12": payload["file_inputs"][0]["sha256_12"],
        }
    ]
    assert payload["argv"][-2:] == ["--prompt-file", str(prompt_file)]


def test_program_get_executes_modal_runner_and_parses_json(monkeypatch, capsys):
    def fake_run(argv, capture_output, text, check):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout='{"program_text": "hello", "run_tag": "mar16-program"}\n',
            stderr="",
        )

    monkeypatch.setattr(cli_commands.subprocess, "run", fake_run)

    exit_code = cli_main.main(["program", "get", "--run-tag", "mar16-program"])

    assert exit_code == 0
    assert _load_stdout_json(capsys) == {
        "program_text": "hello",
        "run_tag": "mar16-program",
    }


def test_program_set_dry_run_reads_file_metadata_and_skips_modal(tmp_path: Path, capsys):
    program_file = tmp_path / "program.md"
    program_file.write_text("Dry run this.\n", encoding="utf-8")

    exit_code = cli_main.main(
        [
            "program",
            "set",
            "--dry-run",
            "--run-tag",
            "mar16-program",
            "--file",
            str(program_file),
        ]
    )

    assert exit_code == 0
    payload = _load_stdout_json(capsys)
    assert payload["kwargs"] == {"run_tag": "mar16-program"}
    assert payload["file_inputs"][0]["flag"] == "--program-file"
    assert payload["file_inputs"][0]["path"] == str(program_file.resolve())
    assert payload["file_inputs"][0]["line_count"] == 1


def test_missing_input_file_returns_concise_error(capsys):
    exit_code = cli_main.main(
        ["program", "set", "--run-tag", "mar16-program", "--file", "./does-not-exist.md"]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Input file not found" in captured.err
    assert "does-not-exist.md" in captured.err


@pytest.mark.parametrize(
    "argv",
    [
        ["program", "get"],
        ["inspect"],
        ["tail"],
        ["claude-baseline"],
        ["inspect", "--dry-run"],
    ],
)
def test_follow_up_commands_require_explicit_run_tag(argv):
    with pytest.raises(SystemExit):
        cli_main.main(argv)


@pytest.mark.parametrize(
    ("argv", "expected_kwargs"),
    [
        (
            ["inspect", "--dry-run", "--run-tag", "mar16-inspect", "--lines", "30"],
            {"run_tag": "mar16-inspect", "tail_lines": 30},
        ),
        (
            [
                "tail",
                "--dry-run",
                "--run-tag",
                "mar16-inspect",
                "--artifact",
                "agent",
                "--lines",
                "80",
            ],
            {"artifact": "agent", "lines": 80, "run_tag": "mar16-inspect"},
        ),
    ],
)
def test_inspect_and_tail_dry_run_payloads(capsys, argv, expected_kwargs):
    exit_code = cli_main.main(argv)

    assert exit_code == 0
    payload = _load_stdout_json(capsys)
    assert payload["dry_run"] is True
    assert payload["kwargs"] == expected_kwargs


def test_claude_baseline_live_failure_is_concise(monkeypatch, capsys):
    def fake_run(argv, capture_output, text, check):
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout="",
            stderr="modal failure\ntrace line 1\ntrace line 2\n",
        )

    monkeypatch.setattr(cli_commands.subprocess, "run", fake_run)

    exit_code = cli_main.main(["claude-baseline", "--run-tag", "mar16-claude"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "`autoresearch-modal claude-baseline` failed with exit code 1." in captured.err
    assert "trace line 2" in captured.err


def test_parser_accepts_dry_run_before_and_after_subcommand(capsys):
    first = cli_main.main(["--dry-run", "inspect", "--run-tag", "smoke"])
    first_payload = _load_stdout_json(capsys)
    second = cli_main.main(["inspect", "--run-tag", "smoke", "--dry-run"])
    second_payload = _load_stdout_json(capsys)

    assert first == 0
    assert second == 0
    assert first_payload["argv"] == second_payload["argv"]
    assert first_payload["kwargs"] == second_payload["kwargs"]
