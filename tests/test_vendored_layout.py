"""Tests for the vendored upstream root layout."""

from pathlib import Path

import pytest

from agent_sandbox.autoresearch import VENDORED_PROJECT_ROOT_ENTRIES, copy_vendored_project_root

REPO_ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_ROOT_ENTRIES = (
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
WRAPPER_OWNED_TOP_LEVEL_ENTRIES = (
    "AGENTS.md",
    "ARCHITECTURE.md",
    "agent_sandbox",
    "docs",
    "scripts",
    "tests",
)


def test_vendored_upstream_root_files_exist():
    for relative_path in UPSTREAM_ROOT_ENTRIES:
        assert (REPO_ROOT / relative_path).exists(), relative_path


def test_seed_allowlist_matches_upstream_root_entries():
    assert VENDORED_PROJECT_ROOT_ENTRIES == UPSTREAM_ROOT_ENTRIES


def test_seed_allowlist_excludes_wrapper_owned_top_level_entries():
    assert set(VENDORED_PROJECT_ROOT_ENTRIES).isdisjoint(WRAPPER_OWNED_TOP_LEVEL_ENTRIES)


def test_copy_vendored_project_root_excludes_wrapper_owned_entries(tmp_path: Path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    for entry_name in VENDORED_PROJECT_ROOT_ENTRIES:
        (source_root / entry_name).write_text(entry_name, encoding="utf-8")
    for entry_name in WRAPPER_OWNED_TOP_LEVEL_ENTRIES:
        path = source_root / entry_name
        if "." in entry_name:
            path.write_text("wrapper", encoding="utf-8")
        else:
            path.mkdir()
            (path / "nested.txt").write_text("wrapper", encoding="utf-8")

    destination_root = tmp_path / "seeded-repo"
    copy_vendored_project_root(source_root, destination_root)

    assert sorted(path.name for path in destination_root.iterdir()) == sorted(UPSTREAM_ROOT_ENTRIES)
    for entry_name in WRAPPER_OWNED_TOP_LEVEL_ENTRIES:
        assert not (destination_root / entry_name).exists()


def test_copy_vendored_project_root_fails_fast_when_root_entry_missing(tmp_path: Path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    for entry_name in VENDORED_PROJECT_ROOT_ENTRIES:
        if entry_name == "program.md":
            continue
        (source_root / entry_name).write_text(entry_name, encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="program.md"):
        copy_vendored_project_root(source_root, tmp_path / "seeded-repo")
