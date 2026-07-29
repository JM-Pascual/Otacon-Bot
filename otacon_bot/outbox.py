"""Watches drop folders under config.OUTBOX_DIR and forwards their
contents to Telegram.

This is the other half of two skills - send-to-telegram (media files
under OUTBOX_DIR itself) and end-session (text messages under
OUTBOX_MESSAGES_DIR, e.g. a session's closing note). An agent running in
any spawned tmux session just drops a file in the right folder, and this
watcher - running inside the already-trusted, already-token-holding bot
process - takes care of actually calling the Telegram API. The bot token
is never handed to spawned sessions; only this process ever touches it.

watchdog's Observer runs callbacks on its own OS thread, not the asyncio
loop that python-telegram-bot needs its API calls made from.
asyncio.run_coroutine_threadsafe is the standard bridge for handing work
from a foreign thread back onto a specific asyncio loop.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Awaitable, Callable

from telegram import Bot
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from otacon_bot import config

logger = logging.getLogger(__name__)

# How long to wait, and how many times to recheck, that a file's size
# has stopped changing before we send it - avoids uploading a file an
# agent is still in the middle of writing.
_STABLE_CHECK_DELAY = 0.5
_STABLE_CHECK_RETRIES = 6

ProcessFn = Callable[[Path, Bot], Awaitable[None]]


def _ensure_dirs() -> None:
    for directory in (
        config.OUTBOX_DIR,
        config.OUTBOX_SENT_DIR,
        config.OUTBOX_FAILED_DIR,
        config.OUTBOX_MESSAGES_DIR,
        config.OUTBOX_MESSAGES_SENT_DIR,
        config.OUTBOX_MESSAGES_FAILED_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    config.OUTBOX_DIR.chmod(0o700)
    config.OUTBOX_MESSAGES_DIR.chmod(0o700)


def _unique_destination(directory: Path, name: str) -> Path:
    """Avoid clobbering an existing file of the same name in sent/failed."""
    dest = directory / name
    if not dest.exists():
        return dest
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    while dest.exists():
        dest = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return dest


async def _wait_until_stable(path: Path) -> bool:
    """Poll file size until it stops changing, or the file disappears."""
    try:
        previous_size = path.stat().st_size
    except FileNotFoundError:
        return False

    for _ in range(_STABLE_CHECK_RETRIES):
        await asyncio.sleep(_STABLE_CHECK_DELAY)
        try:
            current_size = path.stat().st_size
        except FileNotFoundError:
            return False
        if current_size == previous_size:
            return True
        previous_size = current_size

    return True


async def _process_file(path: Path, bot: Bot) -> None:
    """Send a media file (image/video/generic document)."""
    if not await _wait_until_stable(path):
        # Disappeared before we could send it (e.g. a temp file the
        # agent itself cleaned up) - nothing to do.
        return

    suffix = path.suffix.lower()
    try:
        with path.open("rb") as fh:
            if suffix in config.IMAGE_EXTS:
                await bot.send_photo(chat_id=config.AUTHORIZED_USER_ID, photo=fh)
            elif suffix in config.VIDEO_EXTS:
                await bot.send_video(chat_id=config.AUTHORIZED_USER_ID, video=fh)
            else:
                await bot.send_document(chat_id=config.AUTHORIZED_USER_ID, document=fh)
    except Exception:
        logger.exception("Failed to send outbox file %s", path)
        path.rename(_unique_destination(config.OUTBOX_FAILED_DIR, path.name))
        return

    dest = _unique_destination(config.OUTBOX_SENT_DIR, path.name)
    path.rename(dest)
    logger.info("Sent outbox file %s -> %s", path.name, dest)


async def _process_message_file(path: Path, bot: Bot) -> None:
    """Send a plain-text file's contents as a Telegram text message."""
    if not await _wait_until_stable(path):
        return

    try:
        content = path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError:
        logger.exception("Outbox message file is not valid UTF-8 text: %s", path)
        path.rename(_unique_destination(config.OUTBOX_MESSAGES_FAILED_DIR, path.name))
        return

    if not content:
        logger.warning("Outbox message file was empty: %s", path)
        path.rename(_unique_destination(config.OUTBOX_MESSAGES_FAILED_DIR, path.name))
        return

    if len(content) > config.MAX_MESSAGE_CHARS:
        logger.warning(
            "Outbox message exceeds %d chars, refusing: %s",
            config.MAX_MESSAGE_CHARS,
            path,
        )
        path.rename(_unique_destination(config.OUTBOX_MESSAGES_FAILED_DIR, path.name))
        return

    try:
        await bot.send_message(chat_id=config.AUTHORIZED_USER_ID, text=content)
    except Exception:
        logger.exception("Failed to send outbox message %s", path)
        path.rename(_unique_destination(config.OUTBOX_MESSAGES_FAILED_DIR, path.name))
        return

    dest = _unique_destination(config.OUTBOX_MESSAGES_SENT_DIR, path.name)
    path.rename(dest)
    logger.info("Sent outbox message %s -> %s", path.name, dest)


class _WatchedDropHandler(FileSystemEventHandler):
    """
    Watches a single directory for new files and hands each one to
    `process_fn`. Two things this guards against, learned from testing
    the media path the hard way:

    1. Self-triggering on our own archival move. Renaming a file into
       that directory's sent/ or failed/ subfolder after handling it is
       itself a filesystem event, and on macOS the FSEvents backend
       delivers it here even though the observer watches non-recursively
       - without a check, the archived file gets reprocessed as if it
       were new (visible as a double-send, the second copy landing in
       sent/ with a "-1" suffix). _schedule only acts on files whose
       resolved parent is exactly `watch_dir`, never a nested sent/ or
       failed/.
    2. Duplicate real events for one drop (e.g. a create followed by a
       modify, or - since /tmp is a symlink to /private/tmp on macOS -
       the same file reported under two differently-spelled paths).
       _in_flight de-dupes on the resolved absolute path so a second
       event for a file already being processed is a no-op. watchdog
       dispatches events one at a time from a single thread, so the
       synchronous check-and-add in _schedule is race-free without
       needing the lock for correctness - it's kept anyway as cheap
       insurance against a watchdog version change relaxing that
       guarantee.
    """

    def __init__(
        self,
        bot: Bot,
        loop: asyncio.AbstractEventLoop,
        watch_dir: Path,
        process_fn: ProcessFn,
    ) -> None:
        self._bot = bot
        self._loop = loop
        self._watch_dir = watch_dir.resolve()
        self._process_fn = process_fn
        self._in_flight: set[str] = set()
        self._lock = threading.Lock()

    def _schedule(self, path_str: str) -> None:
        path = Path(path_str)
        if not path.is_file():
            return
        resolved = path.resolve()
        if resolved.parent != self._watch_dir:
            # Not a direct child of the watched dir - e.g. our own
            # archival move into sent/ or failed/. Ignore.
            return
        resolved_key = str(resolved)
        with self._lock:
            if resolved_key in self._in_flight:
                return
            self._in_flight.add(resolved_key)
        # Called from watchdog's own thread - hand off to the bot's loop.
        asyncio.run_coroutine_threadsafe(self._run(path, resolved_key), self._loop)

    async def _run(self, path: Path, resolved_key: str) -> None:
        try:
            await self._process_fn(path, self._bot)
        finally:
            with self._lock:
                self._in_flight.discard(resolved_key)

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._schedule(event.src_path)

    def on_moved(self, event) -> None:
        if not event.is_directory:
            self._schedule(event.dest_path)


def start_outbox_watcher(bot: Bot) -> Observer:
    """Start watching both outbox folders for new files. Call once at startup."""
    _ensure_dirs()
    loop = asyncio.get_running_loop()
    observer = Observer()
    observer.schedule(
        _WatchedDropHandler(bot, loop, config.OUTBOX_DIR, _process_file),
        str(config.OUTBOX_DIR),
        recursive=False,
    )
    observer.schedule(
        _WatchedDropHandler(bot, loop, config.OUTBOX_MESSAGES_DIR, _process_message_file),
        str(config.OUTBOX_MESSAGES_DIR),
        recursive=False,
    )
    observer.start()
    logger.info(
        "Outbox watcher started on %s and %s",
        config.OUTBOX_DIR,
        config.OUTBOX_MESSAGES_DIR,
    )
    return observer
