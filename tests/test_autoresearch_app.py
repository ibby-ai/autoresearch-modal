"""Tests for runtime entrypoint tag behavior."""

import json
from pathlib import Path

import pytest

from agent_sandbox import autoresearch_app
from agent_sandbox.autoresearch import branch_name, build_paths


def _stub_paths(run_tag: str):
    return build_paths("/workspace/autoresearch", "/cache/autoresearch", run_tag)


def _raise_if_called(*_args, **_kwargs):
    raise AssertionError("should not bootstrap for read-only command")


def test_prepare_run_returns_generated_tag_when_omitted(monkeypatch):
    raw = autoresearch_app.prepare_autoresearch_run.get_raw_f()
    run_tag = "20260317-154233-prepare-a7c3f1"
    paths = _stub_paths(run_tag)
    state_updates: list[dict[str, object]] = []

    monkeypatch.setattr(
        autoresearch_app,
        "resolve_run_tag",
        lambda value, *, purpose: run_tag,
    )
    monkeypatch.setattr(
        autoresearch_app,
        "_bootstrap_workspace",
        lambda value: (paths, branch_name(value)),
    )
    monkeypatch.setattr(
        autoresearch_app,
        "_write_run_state",
        lambda _paths, **payload: state_updates.append(payload),
    )
    monkeypatch.setattr(autoresearch_app, "_prepare_if_needed", lambda *_args: True)
    monkeypatch.setattr(autoresearch_app, "_current_commit", lambda *_args: "abc1234")
    monkeypatch.setattr(
        autoresearch_app, "_repo_root_files", lambda *_args: ["README.md", "train.py"]
    )
    monkeypatch.setattr(autoresearch_app, "is_data_ready", lambda *_args: True)

    result = raw(run_tag=None, num_shards=7)

    assert result["run_tag"] == run_tag
    assert result["branch"] == branch_name(run_tag)
    assert state_updates[0]["run_tag"] == run_tag
    assert state_updates[-1]["run_tag"] == run_tag


def test_baseline_run_returns_generated_tag_when_omitted(monkeypatch):
    raw = autoresearch_app.run_autoresearch_baseline.get_raw_f()
    run_tag = "20260317-154233-baseline-a7c3f1"
    paths = _stub_paths(run_tag)
    state_updates: list[dict[str, object]] = []

    monkeypatch.setattr(
        autoresearch_app,
        "resolve_run_tag",
        lambda value, *, purpose: run_tag,
    )
    monkeypatch.setattr(
        autoresearch_app,
        "_bootstrap_workspace",
        lambda value: (paths, branch_name(value)),
    )
    monkeypatch.setattr(autoresearch_app, "_prepare_if_needed", lambda *_args: None)
    monkeypatch.setattr(autoresearch_app, "is_data_ready", lambda *_args: True)
    monkeypatch.setattr(
        autoresearch_app,
        "_write_run_state",
        lambda _paths, **payload: state_updates.append(payload),
    )
    monkeypatch.setattr(autoresearch_app, "_current_commit", lambda *_args: "seed123")
    monkeypatch.setattr(
        autoresearch_app,
        "_train_baseline",
        lambda _paths, description: {
            "commit": "base456",
            "summary": {"val_bpb": 0.99},
            "results_path": str(paths.results_path),
            "run_log_path": str(paths.run_log_path),
        },
    )

    result = raw(run_tag=None)

    assert result["run_tag"] == run_tag
    assert result["branch"] == branch_name(run_tag)
    assert state_updates[0]["run_tag"] == run_tag
    assert state_updates[-1]["run_tag"] == run_tag


def test_agent_loop_returns_generated_tag_when_omitted(monkeypatch):
    raw = autoresearch_app.run_autoresearch_agent_loop.get_raw_f()
    run_tag = "20260317-154233-agentloop-a7c3f1"
    paths = _stub_paths(run_tag)
    state_updates: list[dict[str, object]] = []

    monkeypatch.setattr(
        autoresearch_app,
        "resolve_run_tag",
        lambda value, *, purpose: run_tag,
    )
    monkeypatch.setattr(
        autoresearch_app,
        "_bootstrap_workspace",
        lambda value: (paths, branch_name(value)),
    )
    monkeypatch.setattr(autoresearch_app, "_prepare_if_needed", lambda *_args: None)
    monkeypatch.setattr(autoresearch_app, "is_data_ready", lambda *_args: True)
    monkeypatch.setattr(
        autoresearch_app,
        "build_autoresearch_agent_prompt",
        lambda *_args: "agent prompt",
    )
    monkeypatch.setattr(
        autoresearch_app,
        "_write_run_state",
        lambda _paths, **payload: state_updates.append(payload),
    )
    monkeypatch.setattr(autoresearch_app, "_preflight_workspace_runtime", lambda *_args: None)
    monkeypatch.setattr(autoresearch_app, "_run_claude_to_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(autoresearch_app, "_summary_from_run_log", lambda *_args: {"val_bpb": 0.98})
    monkeypatch.setattr(
        autoresearch_app,
        "_inspect_run",
        lambda _paths, target_branch, *, tail_lines=20: {
            "run_tag": run_tag,
            "branch": target_branch,
        },
    )
    monkeypatch.setattr(autoresearch_app, "_current_commit", lambda *_args: "agent789")

    result = raw(run_tag=None, max_turns=5, max_experiments=2)

    assert result["run_tag"] == run_tag
    assert result["branch"] == branch_name(run_tag)
    assert result["mode"] == "agent-loop"
    assert result["summary"] == {"val_bpb": 0.98}
    assert state_updates[0]["run_tag"] == run_tag
    assert state_updates[-1]["run_tag"] == run_tag


def test_program_get_uses_existing_run_without_bootstrapping(monkeypatch, tmp_path: Path):
    raw = autoresearch_app.get_autoresearch_program.get_raw_f()
    run_tag = "existing-program"
    paths = build_paths(tmp_path / "workspace", tmp_path / "cache", run_tag)
    paths.repo_dir.mkdir(parents=True)
    paths.program_path.write_text("hello\n", encoding="utf-8")

    monkeypatch.setattr(autoresearch_app, "_bootstrap_workspace", _raise_if_called)
    monkeypatch.setattr(
        autoresearch_app,
        "_open_existing_run",
        lambda value: (paths, branch_name(value)),
    )
    monkeypatch.setattr(autoresearch_app, "_repo_root_files", lambda *_args: ["program.md"])
    monkeypatch.setattr(autoresearch_app, "_current_commit", lambda *_args: "abc1234")

    result = raw(run_tag=run_tag)

    assert result["run_tag"] == run_tag
    assert result["program_text"] == "hello\n"
    assert result["branch"] == branch_name(run_tag)


def test_inspect_uses_existing_run_without_bootstrapping(monkeypatch):
    raw = autoresearch_app.inspect_autoresearch_run.get_raw_f()
    run_tag = "existing-inspect"
    paths = _stub_paths(run_tag)

    monkeypatch.setattr(autoresearch_app, "_bootstrap_workspace", _raise_if_called)
    monkeypatch.setattr(
        autoresearch_app,
        "_open_existing_run",
        lambda value: (paths, branch_name(value)),
    )
    monkeypatch.setattr(
        autoresearch_app,
        "_inspect_run",
        lambda _paths, target_branch, *, tail_lines=20: {
            "run_tag": run_tag,
            "branch": target_branch,
            "tail_lines": tail_lines,
        },
    )

    result = raw(run_tag=run_tag, tail_lines=7)

    assert result == {
        "run_tag": run_tag,
        "branch": branch_name(run_tag),
        "tail_lines": 7,
    }


def test_tail_uses_existing_run_without_bootstrapping(monkeypatch, tmp_path: Path):
    raw = autoresearch_app.tail_autoresearch_artifact.get_raw_f()
    run_tag = "existing-tail"
    paths = build_paths(tmp_path / "workspace", tmp_path / "cache", run_tag)
    paths.run_root.mkdir(parents=True)
    paths.agent_log_path.write_text("one\ntwo\n", encoding="utf-8")

    monkeypatch.setattr(autoresearch_app, "_bootstrap_workspace", _raise_if_called)
    monkeypatch.setattr(
        autoresearch_app,
        "_open_existing_run",
        lambda value: (paths, branch_name(value)),
    )

    result = raw(run_tag=run_tag, artifact="agent", lines=1)

    assert result == {
        "run_tag": run_tag,
        "branch": branch_name(run_tag),
        "artifact": "agent",
        "path": str(paths.agent_log_path),
        "lines": ["two"],
    }


def test_inspect_uses_persisted_run_state_without_modal_lookup(monkeypatch, tmp_path: Path):
    raw = autoresearch_app.inspect_autoresearch_run.get_raw_f()
    run_tag = "existing-inspect"
    paths = build_paths(tmp_path / "workspace", tmp_path / "cache", run_tag)
    paths.repo_dir.mkdir(parents=True)
    paths.program_path.write_text("program\n", encoding="utf-8")
    paths.state_path.write_text(
        json.dumps(
            {
                "branch": branch_name(run_tag),
                "current_commit": "abc1234",
                "modal_app_id": "ap-stopped",
                "modal_app_name": "autoresearch-modal",
                "mode": "agent-loop",
                "modal_app_state": "stopped",
                "modal_app_running_tasks": 0,
                "run_tag": run_tag,
                "status": "running",
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(autoresearch_app, "_bootstrap_workspace", _raise_if_called)
    monkeypatch.setattr(
        autoresearch_app,
        "_open_existing_run",
        lambda value: (paths, branch_name(value)),
    )
    monkeypatch.setattr(autoresearch_app, "_git_status", lambda *_args, **_kwargs: ([], []))
    monkeypatch.setattr(autoresearch_app, "_repo_root_files", lambda *_args: ["program.md"])
    monkeypatch.setattr(autoresearch_app, "_current_commit", lambda *_args: "abc1234")
    monkeypatch.setattr(autoresearch_app, "is_data_ready", lambda *_args: True)

    result = raw(run_tag=run_tag, tail_lines=5)

    assert result["run_state"]["status"] == "running"
    assert result["run_state"]["modal_app_id"] == "ap-stopped"
    assert result["run_state"]["modal_app_state"] == "stopped"
    assert result["run_state"]["modal_app_running_tasks"] == 0
    assert json.loads(paths.state_path.read_text(encoding="utf-8"))["status"] == "running"


def test_tail_returns_persisted_state_artifact_without_modal_lookup(
    monkeypatch, tmp_path: Path
):
    raw = autoresearch_app.tail_autoresearch_artifact.get_raw_f()
    run_tag = "existing-tail"
    paths = build_paths(tmp_path / "workspace", tmp_path / "cache", run_tag)
    paths.repo_dir.mkdir(parents=True)
    paths.state_path.write_text(
        json.dumps(
            {
                "branch": branch_name(run_tag),
                "current_commit": "abc1234",
                "modal_app_id": "ap-stopped",
                "modal_app_name": "autoresearch-modal",
                "mode": "agent-loop",
                "modal_app_state": "stopped",
                "modal_app_running_tasks": 0,
                "run_tag": run_tag,
                "status": "running",
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(autoresearch_app, "_bootstrap_workspace", _raise_if_called)
    monkeypatch.setattr(
        autoresearch_app,
        "_open_existing_run",
        lambda value: (paths, branch_name(value)),
    )

    result = raw(run_tag=run_tag, artifact="state", lines=20)

    assert result["run_tag"] == run_tag
    assert result["artifact"] == "state"
    assert any('"status": "running"' in line for line in result["lines"])
    assert json.loads(paths.state_path.read_text(encoding="utf-8"))["status"] == "running"


def test_reconcile_state_updates_running_state_for_existing_run(monkeypatch, tmp_path: Path):
    raw = autoresearch_app.reconcile_autoresearch_run_state.get_raw_f()
    run_tag = "existing-reconcile"
    paths = build_paths(tmp_path / "workspace", tmp_path / "cache", run_tag)
    paths.repo_dir.mkdir(parents=True)
    paths.program_path.write_text("program\n", encoding="utf-8")
    paths.state_path.write_text(
        json.dumps(
            {
                "branch": branch_name(run_tag),
                "current_commit": "abc1234",
                "mode": "agent-loop",
                "run_tag": run_tag,
                "status": "running",
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        autoresearch_app,
        "_open_existing_run",
        lambda value: (paths, branch_name(value)),
    )
    result = raw(
        run_tag=run_tag,
        state_status="interrupted",
        terminal_reason="modal_app_stopped",
        modal_app_state="stopped",
        modal_app_running_tasks=0,
    )

    assert result["status"] == "interrupted"
    assert result["terminal_reason"] == "modal_app_stopped"
    assert result["modal_app_state"] == "stopped"
    assert json.loads(paths.state_path.read_text(encoding="utf-8"))["status"] == "interrupted"


def test_reconcile_state_returns_existing_terminal_state_without_changes(
    monkeypatch, tmp_path: Path
):
    raw = autoresearch_app.reconcile_autoresearch_run_state.get_raw_f()
    run_tag = "existing-tail"
    paths = build_paths(tmp_path / "workspace", tmp_path / "cache", run_tag)
    paths.repo_dir.mkdir(parents=True)
    paths.state_path.write_text(
        json.dumps(
            {
                "branch": branch_name(run_tag),
                "current_commit": "abc1234",
                "modal_app_id": "ap-stopped",
                "modal_app_name": "autoresearch-modal",
                "mode": "agent-loop",
                "run_tag": run_tag,
                "status": "interrupted",
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        autoresearch_app,
        "_open_existing_run",
        lambda value: (paths, branch_name(value)),
    )

    result = raw(
        run_tag=run_tag,
        state_status="stale",
        terminal_reason="modal_app_not_found",
        modal_app_state="stopped",
        modal_app_running_tasks=0,
    )

    assert result["status"] == "interrupted"
    assert json.loads(paths.state_path.read_text(encoding="utf-8"))["status"] == "interrupted"


def test_agent_loop_failure_records_failed_state_with_artifact_tails(
    monkeypatch, tmp_path: Path
):
    raw = autoresearch_app.run_autoresearch_agent_loop.get_raw_f()
    run_tag = "20260317-154233-agentloop-a7c3f1"
    paths = build_paths(tmp_path / "workspace", tmp_path / "cache", run_tag)
    state_updates: list[dict[str, object]] = []

    monkeypatch.setattr(
        autoresearch_app,
        "resolve_run_tag",
        lambda value, *, purpose: run_tag,
    )
    monkeypatch.setattr(
        autoresearch_app,
        "_bootstrap_workspace",
        lambda value: (paths, branch_name(value)),
    )
    monkeypatch.setattr(autoresearch_app, "_prepare_if_needed", lambda *_args: None)
    monkeypatch.setattr(autoresearch_app, "is_data_ready", lambda *_args: True)
    monkeypatch.setattr(
        autoresearch_app,
        "build_autoresearch_agent_prompt",
        lambda *_args: "agent prompt",
    )
    monkeypatch.setattr(
        autoresearch_app,
        "_write_run_state",
        lambda _paths, **payload: state_updates.append(payload),
    )
    monkeypatch.setattr(autoresearch_app, "_preflight_workspace_runtime", lambda *_args: None)

    def fail_claude(*_args, **_kwargs):
        paths.agent_log_path.parent.mkdir(parents=True, exist_ok=True)
        paths.agent_log_path.write_text("agent failure\n", encoding="utf-8")
        paths.run_log_path.parent.mkdir(parents=True, exist_ok=True)
        paths.run_log_path.write_text("python failure\n", encoding="utf-8")
        raise RuntimeError("claude exploded")

    monkeypatch.setattr(autoresearch_app, "_run_claude_to_log", fail_claude)
    monkeypatch.setattr(autoresearch_app, "_current_commit", lambda *_args: "agent789")

    with pytest.raises(
        RuntimeError, match="Claude agent loop failed: RuntimeError: claude exploded"
    ):
        raw(run_tag=run_tag, max_turns=5, max_experiments=2)

    assert state_updates[0]["status"] == "running"
    assert state_updates[-1]["status"] == "failed"
    assert state_updates[-1]["error_type"] == "RuntimeError"
    assert state_updates[-1]["agent_log_tail"] == ["agent failure"]
    assert state_updates[-1]["run_log_tail"] == ["python failure"]
    assert "claude exploded" in str(state_updates[-1]["error"])


def test_main_prepare_allows_missing_run_tag_and_prints_payload(monkeypatch, capsys):
    class StubPrepare:
        @staticmethod
        def remote(*, run_tag, num_shards):
            assert run_tag is None
            assert num_shards == 10
            return {"run_tag": "20260317-154233-prepare-a7c3f1"}

    monkeypatch.setattr(autoresearch_app, "prepare_autoresearch_run", StubPrepare())

    autoresearch_app.main(mode="prepare")

    captured = capsys.readouterr()
    assert json.loads(captured.out)["run_tag"] == "20260317-154233-prepare-a7c3f1"


def test_main_inspect_still_requires_explicit_run_tag():
    with pytest.raises(ValueError, match="--run-tag is required for mode=inspect"):
        autoresearch_app.main(mode="inspect")
