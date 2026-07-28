"""Command handlers.

By the time any handler here runs, otacon_bot.auth.auth_gate has already
confirmed the sender is the authorized user (group -1 runs first) - these
handlers don't re-check identity themselves. Errors are reported to the
user with full detail: since only the owner can ever reach this code,
there's no one to leak internals to.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from otacon_bot import config, tmux_manager

logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Otacon online.")


async def new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        projects = ", ".join(sorted(config.PROJECT_ALLOWLIST)) or "(none configured)"
        await update.message.reply_text(f"Usage: /new <project> [label]\nAvailable: {projects}")
        return

    project = context.args[0]
    label = context.args[1] if len(context.args) > 1 else None
    path = config.PROJECT_ALLOWLIST.get(project)
    if path is None:
        projects = ", ".join(sorted(config.PROJECT_ALLOWLIST)) or "(none configured)"
        await update.message.reply_text(f"Unknown project '{project}'.\nAvailable: {projects}")
        return

    try:
        session_name = tmux_manager.spawn_session(project, path, label)
    except tmux_manager.InvalidLabelError as exc:
        await update.message.reply_text(str(exc))
    except tmux_manager.SessionLimitError as exc:
        await update.message.reply_text(str(exc))
    except PermissionError as exc:
        # Should be unreachable in normal operation - path was just looked
        # up from PROJECT_ALLOWLIST above - but assert_dir_allowed is the
        # enforced choke point, so handle its failure mode explicitly.
        logger.exception("Directory allowlist rejected path for project=%s", project)
        await update.message.reply_text(f"Directory not allowed: {exc}")
    except (tmux_manager.TmuxNotFoundError, tmux_manager.TmuxError) as exc:
        logger.exception("Failed to spawn session for project=%s", project)
        await update.message.reply_text(f"Failed to spawn session: {exc}")
    else:
        await update.message.reply_text(
            f"Spawned session: {session_name}\n"
            "Open the Claude mobile app and continue it via Remote Control."
        )


async def sessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        names = tmux_manager.list_spawned_sessions()
    except (tmux_manager.TmuxNotFoundError, tmux_manager.TmuxError) as exc:
        logger.exception("Failed to list sessions")
        await update.message.reply_text(f"Failed to list sessions: {exc}")
        return

    if not names:
        await update.message.reply_text("No active sessions.")
    else:
        await update.message.reply_text("\n".join(names))


async def kill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /kill <session>")
        return

    name = context.args[0]
    try:
        tmux_manager.kill_session(name)
    except (tmux_manager.TmuxNotFoundError, tmux_manager.TmuxError) as exc:
        logger.exception("Failed to kill session=%s", name)
        await update.message.reply_text(f"Failed to kill '{name}': {exc}")
    else:
        await update.message.reply_text(f"Killed session: {name}")
