"""Configuration loading and startup validation.

Secrets (bot token, authorized user id) come from environment variables /
.env — never hardcoded. The directory allowlist is policy, not a secret,
but it's still machine-specific (absolute paths on this Mac), so it does
NOT live in source either — it's loaded from the "allowed_dirs" field of
bot_config.json, a gitignored file local to this machine.
bot_config.example.json (committed) documents the shape without exposing
any real path. Either way, the allowlist is never influenced by anything
a Telegram message can supply.

assert_dir_allowed() is the single choke point every directory-touching
operation in this project must call, not just the command handlers that
exist today — see tmux_manager.spawn_session for the reference usage.
Re-validating at the point of use (rather than trusting that whatever
resolved a path upstream did it correctly) is deliberate defense in
depth: a future handler bug that mis-resolves a path still can't reach
tmux with it.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


BOT_TOKEN: str = _require_env("TELEGRAM_BOT_TOKEN")

_raw_user_id = _require_env("TELEGRAM_AUTHORIZED_USER_ID")
try:
    AUTHORIZED_USER_ID: int = int(_raw_user_id)
except ValueError as exc:
    raise RuntimeError(
        "TELEGRAM_AUTHORIZED_USER_ID must be a numeric Telegram user id"
    ) from exc


def _load_allowed_dirs() -> dict[str, Path]:
    """Load project name -> absolute path from bot_config.json's "allowed_dirs" field.

    Add new projects by editing that file, not via any bot command or
    environment variable — this is the only set of directories any
    directory-touching operation in this bot is ever allowed to use.
    """
    config_file = Path(os.environ.get("BOT_CONFIG_FILE", REPO_ROOT / "bot_config.json"))
    if not config_file.is_file():
        raise RuntimeError(
            f"{config_file} not found. Copy bot_config.example.json to "
            "bot_config.json and fill in your real project paths."
        )
    raw = json.loads(config_file.read_text())
    allowed_dirs = raw.get("allowed_dirs") if isinstance(raw, dict) else None
    if not isinstance(allowed_dirs, dict):
        raise RuntimeError(
            f"{config_file} must contain an \"allowed_dirs\" object of name -> path"
        )
    return {name: Path(path).resolve() for name, path in allowed_dirs.items()}


PROJECT_ALLOWLIST: dict[str, Path] = _load_allowed_dirs()


def assert_dir_allowed(path: Path) -> Path:
    """Resolve `path` and confirm it is exactly one of PROJECT_ALLOWLIST's values.

    Every function anywhere in this project that is about to run a
    filesystem or subprocess operation scoped to a directory must call
    this first, even if the path already came from an allowlist lookup
    upstream (e.g. handlers.py resolving a project name) — it's cheap
    insurance against a future call site trusting an unvalidated path.
    """
    resolved = Path(path).resolve()
    if resolved not in PROJECT_ALLOWLIST.values():
        raise PermissionError(f"Directory not in allowed_dirs: {resolved}")
    return resolved

TMUX_BIN: str | None = shutil.which("tmux")
CLAUDE_BIN: str | None = shutil.which("claude")

SESSION_PREFIX = "otacon"
MAX_SESSIONS_PER_PROJECT = 3

# Drop folder for agents to push media into for delivery to Telegram
# (see otacon_bot/outbox.py). Deliberately in /tmp, not the repo or
# $HOME: never git-committable regardless of which project an agent is
# running in, and macOS clears /tmp on reboot so sent/failed archives
# can't grow unbounded.
OUTBOX_DIR = Path("/tmp/otacon-outbox")
OUTBOX_SENT_DIR = OUTBOX_DIR / "sent"
OUTBOX_FAILED_DIR = OUTBOX_DIR / "failed"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTS = {".mp4", ".mov"}

# Separate drop folder for plain text (sent via bot.send_message, not as
# a file attachment) - e.g. a status update or a final message from a
# session closing itself via the end-session skill.
OUTBOX_MESSAGES_DIR = OUTBOX_DIR / "messages"
OUTBOX_MESSAGES_SENT_DIR = OUTBOX_MESSAGES_DIR / "sent"
OUTBOX_MESSAGES_FAILED_DIR = OUTBOX_MESSAGES_DIR / "failed"
MAX_MESSAGE_CHARS = 4096  # Telegram's sendMessage text length cap


def validate() -> None:
    """Fail fast and loud at startup if the environment is unusable.

    This runs once, locally, before the bot starts polling — it is not
    reachable from Telegram, so verbose errors here are safe.
    """
    if TMUX_BIN is None:
        raise RuntimeError(
            "tmux not found on PATH. Install it (e.g. `brew install tmux`) "
            "before starting the bot."
        )
    if CLAUDE_BIN is None:
        raise RuntimeError(
            "claude not found on PATH. Install Claude Code before starting "
            "the bot."
        )
    missing = [
        name
        for name, path in PROJECT_ALLOWLIST.items()
        if not path.is_dir()
    ]
    if missing:
        raise RuntimeError(
            f"PROJECT_ALLOWLIST entries missing on disk: {', '.join(missing)}"
        )
    logger.info(
        "Config OK: %d allowlisted project(s), tmux=%s, claude=%s",
        len(PROJECT_ALLOWLIST),
        TMUX_BIN,
        CLAUDE_BIN,
    )
