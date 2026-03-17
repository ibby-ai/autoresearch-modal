"""Tests for runtime entrypoint tag behavior."""

import json

import pytest

from agent_sandbox import autoresearch_app
from agent_sandbox.autoresearch import branch_name, build_paths


def _stub_paths(run_tag: str):
    return build_paths("/workspace/autoresearch", "/cache/autoresearch", run_tag)


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
