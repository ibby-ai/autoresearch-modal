"""Shared helpers for the autoresearch Modal wrapper."""

from agent_sandbox.utils.cli import (
    RUNTIME_APP_ROOT,
    RUNTIME_CLI_PATH,
    RUNTIME_HOME,
    RUNTIME_USER,
    demote_to_runtime_user,
    maybe_chown_for_runtime_user,
    require_claude_cli_auth,
    runtime_user_env,
    runtime_user_ids,
)

__all__ = [
    "RUNTIME_USER",
    "RUNTIME_HOME",
    "RUNTIME_CLI_PATH",
    "RUNTIME_APP_ROOT",
    "runtime_user_env",
    "runtime_user_ids",
    "demote_to_runtime_user",
    "maybe_chown_for_runtime_user",
    "require_claude_cli_auth",
]
