"""Subprocess wrappers around tmux.

Every tmux invocation here uses an argument list (never shell=True and
never a formatted shell string), so nothing a caller supplies - including
a project name that ultimately came from a Telegram message - is ever
interpreted by a shell. Callers are additionally expected to have already
resolved "project" through config.PROJECT_ALLOWLIST before calling
spawn_session, so cwd is always one of a small, hardcoded set of paths.
"""

from __future__ import annotations

import datetime
import logging
import re
import subprocess
from pathlib import Path

from otacon_bot import config

logger = logging.getLogger(__name__)

# Deliberately restrictive: the label ends up as a literal fragment of a
# tmux session name (used later in `tmux -t <name>` lookups for /kill and
# in the startswith() prefix filtering in list_spawned_sessions). Keeping
# it to a small safe charset avoids ambiguity in that filtering and any
# tmux special-character handling in session-name targets (e.g. `.`, `:`)
# without needing to reason about tmux's own escaping rules.
_LABEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")


class TmuxNotFoundError(RuntimeError):
    pass


class TmuxError(RuntimeError):
    pass


class SessionLimitError(RuntimeError):
    pass


class InvalidLabelError(RuntimeError):
    pass


def _validate_label(label: str) -> str:
    if not _LABEL_RE.match(label):
        raise InvalidLabelError(
            "Label may only contain letters, numbers, '-' and '_' "
            "(max 20 characters)."
        )
    return label


def _tmux_bin() -> str:
    if config.TMUX_BIN is None:
        raise TmuxNotFoundError("tmux is not installed or not on PATH")
    return config.TMUX_BIN


def list_spawned_sessions() -> list[str]:
    """Return names of all currently running tmux sessions this bot spawned."""
    result = subprocess.run(
        [_tmux_bin(), "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # tmux exits nonzero when there are zero sessions at all - not a
        # failure for us. The exact stderr wording varies: "no server
        # running on <path>" on some versions, "error connecting to
        # <path> (No such file or directory)" on others (e.g. when the
        # server has never started on this machine at all).
        stderr_lower = result.stderr.lower()
        if (
            "no server running" in stderr_lower
            or "no sessions" in stderr_lower
            or "error connecting to" in stderr_lower
        ):
            return []
        raise TmuxError(result.stderr.strip() or "tmux list-sessions failed")

    prefix = f"{config.SESSION_PREFIX}-"
    return [
        line
        for line in result.stdout.splitlines()
        if line.startswith(prefix)
    ]


def count_sessions_for(project: str) -> int:
    project_prefix = f"{config.SESSION_PREFIX}-{project}-"
    return sum(1 for name in list_spawned_sessions() if name.startswith(project_prefix))


def spawn_session(project: str, cwd: Path, label: str | None = None) -> str:
    """Spawn a new tmux session running `claude` in cwd. Returns the session name.

    `label` is an optional free-text tag (e.g. "auth-fix") purely for the
    human to recognize sessions by in /sessions - it plays no role in
    directory resolution or access control, which is why it's validated
    separately from (and after) the allowlist/limit checks below.
    """
    if count_sessions_for(project) >= config.MAX_SESSIONS_PER_PROJECT:
        raise SessionLimitError(
            f"Already at the limit of {config.MAX_SESSIONS_PER_PROJECT} "
            f"concurrent session(s) for '{project}'. Kill one first with /kill."
        )

    # Defense in depth: re-validate cwd against allowed_dirs here, even
    # though handlers.py already resolved it via the allowlist lookup.
    # This is the actual filesystem/subprocess operation, so this is the
    # choke point that must never trust an unvalidated caller.
    allowed_cwd = config.assert_dir_allowed(cwd)

    timestamp = datetime.datetime.now().strftime("%H%M%S")
    session_name = f"{config.SESSION_PREFIX}-{project}-{timestamp}"
    if label:
        session_name += f"-{_validate_label(label)}"

    result = subprocess.run(
        [
            _tmux_bin(),
            "new-session",
            "-d",
            "-s",
            session_name,
            "-c",
            str(allowed_cwd),
            config.CLAUDE_BIN or "claude",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise TmuxError(result.stderr.strip() or "tmux new-session failed")

    logger.info("Spawned session %s in %s", session_name, allowed_cwd)
    return session_name


def kill_session(name: str) -> None:
    """Kill a tmux session, but only if it's one this bot spawned."""
    if name not in list_spawned_sessions():
        raise TmuxError(f"'{name}' is not a session this bot spawned (or it isn't running)")

    result = subprocess.run(
        [_tmux_bin(), "kill-session", "-t", name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise TmuxError(result.stderr.strip() or "tmux kill-session failed")

    logger.info("Killed session %s", name)
