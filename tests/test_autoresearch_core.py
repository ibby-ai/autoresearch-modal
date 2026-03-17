"""Tests for pure autoresearch helpers."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_sandbox.autoresearch import (
    RESULTS_HEADER,
    append_result_row,
    branch_name,
    build_autoresearch_agent_prompt,
    build_claude_baseline_prompt,
    build_paths,
    ensure_results_file,
    generate_run_tag,
    is_data_ready,
    parse_training_summary,
    resolve_run_tag,
    validate_run_tag,
)


def test_validate_run_tag_accepts_simple_value():
    assert validate_run_tag("mar16-smoke") == "mar16-smoke"


def test_generate_run_tag_is_sortable_and_valid():
    tag = generate_run_tag(
        "agent-loop",
        now=datetime(2026, 3, 17, 15, 42, 33, tzinfo=UTC),
        entropy="a7c3f1",
    )

    assert tag == "20260317-154233-agentloop-a7c3f1"
    assert validate_run_tag(tag) == tag


@pytest.mark.parametrize("value", ["", "bad/tag", "bad tag", "../oops"])
def test_validate_run_tag_rejects_invalid_values(value: str):
    with pytest.raises(ValueError):
        validate_run_tag(value)


def test_resolve_run_tag_preserves_explicit_override():
    assert resolve_run_tag("  mar16-explicit  ", purpose="baseline") == "mar16-explicit"


def test_resolve_run_tag_generates_when_missing():
    tag = resolve_run_tag(None, purpose="prepare")

    assert tag
    assert "-prepare-" in tag
    assert validate_run_tag(tag) == tag


def test_branch_name_prefixes_run_tag():
    assert branch_name("mar16") == "autoresearch/mar16"


def test_build_paths_uses_run_tag_layout():
    paths = build_paths("/workspace/autoresearch", "/cache/autoresearch", "smoke")
    assert paths.run_root == Path("/workspace/autoresearch/smoke")
    assert paths.repo_dir == Path("/workspace/autoresearch/smoke/repo")
    assert paths.program_path == Path("/workspace/autoresearch/smoke/repo/program.md")
    assert paths.results_path == Path("/workspace/autoresearch/smoke/repo/results.tsv")
    assert paths.prepare_log_path == Path("/workspace/autoresearch/smoke/prepare.log")
    assert paths.agent_log_path == Path("/workspace/autoresearch/smoke/agent.log")
    assert paths.state_path == Path("/workspace/autoresearch/smoke/modal-run-state.json")


def test_ensure_results_file_writes_header_once(tmp_path: Path):
    results_path = tmp_path / "results.tsv"

    ensure_results_file(results_path)
    ensure_results_file(results_path)

    assert results_path.read_text(encoding="utf-8") == RESULTS_HEADER


def test_append_result_row_uses_tsv_format(tmp_path: Path):
    results_path = tmp_path / "results.tsv"

    append_result_row(
        results_path,
        commit="abc1234",
        val_bpb=0.9979,
        memory_gb=44.0,
        status="keep",
        description="baseline",
    )

    lines = results_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == RESULTS_HEADER.strip()
    assert lines[1] == "abc1234\t0.997900\t44.0\tkeep\tbaseline"


def test_is_data_ready_requires_tokenizer_and_two_shards(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    data_dir = cache_dir / "data"
    tokenizer_dir = cache_dir / "tokenizer"
    data_dir.mkdir(parents=True)
    tokenizer_dir.mkdir(parents=True)

    assert is_data_ready(cache_dir) is False

    (data_dir / "shard_00000.parquet").write_text("")
    (data_dir / "shard_06542.parquet").write_text("")
    (tokenizer_dir / "tokenizer.pkl").write_text("")
    (tokenizer_dir / "token_bytes.pt").write_text("")

    assert is_data_ready(cache_dir) is True


def test_parse_training_summary_reads_upstream_fields():
    log_text = """
---
val_bpb:          0.997900
training_seconds: 300.1
total_seconds:    325.9
peak_vram_mb:     45060.2
mfu_percent:      39.80
total_tokens_M:   499.6
num_steps:        953
num_params_M:     50.3
depth:            8
"""

    summary = parse_training_summary(log_text)

    assert summary.val_bpb == pytest.approx(0.9979)
    assert summary.peak_vram_mb == pytest.approx(45060.2)
    assert summary.num_steps == 953
    assert summary.depth == 8


def test_build_claude_baseline_prompt_is_bounded():
    prompt = build_claude_baseline_prompt("mar16", 10)

    assert "autoresearch/mar16" in prompt
    assert "Do exactly one baseline training run" in prompt
    assert "Do not modify `train.py`" in prompt
    assert "uv run train.py > run.log 2>&1" in prompt


def test_build_autoresearch_agent_prompt_matches_upstream_loop():
    prompt = build_autoresearch_agent_prompt("mar16", 10, 12)

    assert "autoresearch/mar16" in prompt
    assert "Treat `README.md` and `program.md` as the primary contract" in prompt
    assert "Modify only `train.py`." in prompt
    assert "uv run train.py > run.log 2>&1" in prompt
    assert "Perform up to 12 completed experiment attempts" in prompt
    assert "Do not ask the human for confirmation once the loop begins." in prompt
