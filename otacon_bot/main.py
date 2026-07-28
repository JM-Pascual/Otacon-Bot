"""Entry point.

Uses long polling (run_polling) rather than a webhook: this machine
doesn't need to open any inbound port, accept connections from the
internet, or hold a TLS cert for the core feature. Webhook mode is an
intentional non-goal for v1 and can be added later as a separate,
opt-in mode without touching this module's structure.
"""

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, TypeHandler

from otacon_bot import config, handlers
from otacon_bot.auth import auth_gate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# httpx logs full request URLs at INFO level, and python-telegram-bot's
# API calls embed the bot token in the URL path - left at INFO this
# writes the token in plaintext to stdout/log files on every request.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def build_application() -> Application:
    config.validate()

    application = Application.builder().token(config.BOT_TOKEN).build()

    # group=-1 runs before the default group (0), so this gate sees every
    # update first, for every command handler below and any added later.
    application.add_handler(TypeHandler(Update, auth_gate), group=-1)

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("new", handlers.new))
    application.add_handler(CommandHandler("sessions", handlers.sessions))
    application.add_handler(CommandHandler("kill", handlers.kill))

    return application


def main() -> None:
    application = build_application()
    logger.info("Starting long polling...")
    application.run_polling()


if __name__ == "__main__":
    main()
