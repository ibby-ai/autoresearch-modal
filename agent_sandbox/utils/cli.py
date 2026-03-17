"""Runtime user and Claude CLI utility functions shared across modules.

This module provides shared functionality for running the Claude Code CLI
in a non-root environment with proper privilege demotion and authentication.
"""

import logging
import os
import pwd
from collections.abc import Callable
from pathlib import Path

_logger = logging.getLogger(__name__)

# Generic runtime user/path configuration. The installed CLI is still Claude Code today,
# but the container user/home should not hard-code a vendor name.
RUNTIME_USER = "agent"
RUNTIME_HOME = Path("/home/agent")
RUNTIME_CLI_PATH = f"{RUNTIME_HOME}/.local/bin:{RUNTIME_HOME}/.claude/bin"
RUNTIME_APP_ROOT = RUNTIME_HOME / "app"


def runtime_user_env() -> dict[str, str]:
    """Build environment dictionary for Claude CLI subprocess execution.

    Returns:
        Environment dict with HOME, USER, and PATH configured for the runtime user.
    """
    env = os.environ.copy()
    env["HOME"] = str(RUNTIME_HOME)
    env["USER"] = RUNTIME_USER
    env["PATH"] = f"{RUNTIME_CLI_PATH}:{env.get('PATH', '')}"
    return env


def require_claude_cli_auth(env: dict[str, str]) -> None:
    """Ensure Claude CLI has credentials available.

    Args:
        env: Environment dictionary to check for ANTHROPIC_API_KEY.

    Raises:
        RuntimeError: If ANTHROPIC_API_KEY is missing.
    """
    if env.get("ANTHROPIC_API_KEY"):
        return
    raise RuntimeError(
        "ANTHROPIC_API_KEY is missing. Configure the 'anthropic-secret' "
        "Modal secret so Claude CLI can authenticate."
    )


def runtime_user_ids() -> tuple[int, int]:
    """Get UID and GID for the runtime user.

    Returns:
        Tuple of (uid, gid) for the runtime user.

    Raises:
        RuntimeError: If the runtime user is not found in the system.
    """
    try:
        entry = pwd.getpwnam(RUNTIME_USER)
    except KeyError as exc:
        raise RuntimeError("Runtime user not found; rebuild the image to create it.") from exc
    return entry.pw_uid, entry.pw_gid


def demote_to_runtime_user() -> Callable[[], None]:
    """Create a preexec_fn for subprocess that drops privileges to the runtime user.

    This function is used as preexec_fn in subprocess.run() to demote the
    process to the non-root runtime user before executing the CLI.

    Returns:
        A callable that sets the process UID/GID to the runtime user.
    """
    uid, gid = runtime_user_ids()

    def _inner() -> None:
        os.setgid(gid)
        if hasattr(os, "setgroups"):
            os.setgroups([gid])
        os.setuid(uid)

    return _inner


def maybe_chown_for_runtime_user(path: Path) -> None:
    """Change ownership of a path to the runtime user if possible.

    This is used to ensure the runtime user can write to job workspaces.
    Failures are logged but not raised.

    Args:
        path: Path to change ownership of.
    """
    try:
        uid, gid = runtime_user_ids()
    except RuntimeError:
        _logger.warning("Runtime user missing; skipping workspace chown")
        return
    try:
        os.chown(path, uid, gid)
        path.chmod(0o775)
    except PermissionError:
        _logger.warning("Unable to chown workspace for runtime user", exc_info=True)
