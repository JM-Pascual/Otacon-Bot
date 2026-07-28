"""Global single-user auth gate.

Registered in main.py as a TypeHandler in handler group -1, which
python-telegram-bot guarantees runs before any handler in the default
group (0). Unauthorized senders get ApplicationHandlerStop raised, which
halts all further processing of that update — no reply is ever sent, and
no later handler (present or future) has a chance to run. This makes the
gate impossible to accidentally bypass by adding a new command handler,
unlike a per-handler decorator that every new command must remember to
apply.

Silent-drop (not even an error message) is deliberate: any response at
all - even "unauthorized" - confirms to a stranger that this bot is alive
and listening, which is exposure this bot doesn't need to offer.
"""

import logging

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from otacon_bot import config

logger = logging.getLogger(__name__)


async def auth_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or user.id != config.AUTHORIZED_USER_ID:
        # Logged locally only. This line is also the mechanism the README
        # points the owner at to safely discover their own numeric id.
        logger.info("Rejected update from unauthorized user id=%s", user.id if user else None)
        raise ApplicationHandlerStop
