"""
main.py — Entrypoint.  Bootstraps Telethon client, registers all event
handlers, and runs the asyncio event loop until interrupted.
"""

import asyncio
import logging
import signal
import sys

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

import config
import db
import detector
import logger
import win_checker
from actions import click_enter_button, join_channels
from parser import parse_giveaway

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("main")


# ── Core giveaway handler ──────────────────────────────────────────────────────

async def handle_potential_giveaway(
    client: TelegramClient,
    event: events.NewMessage.Event,
) -> None:
    """Full pipeline: detect → parse → join → click → log → schedule recheck."""
    message = event.message

    if not detector.is_giveaway(message):
        return
    if detector.already_entered(message):
        log.debug("Duplicate — msg=%d ch=%d", message.id, message.chat_id)
        return

    log.info("Giveaway detected — msg=%d ch=%d", message.id, message.chat_id)

    giveaway = parse_giveaway(message)

    # ── Join required channels ─────────────────────────────────────────────────
    join_result: dict[str, str] = {}
    if giveaway.required_channels:
        join_result = await join_channels(client, giveaway.required_channels)

    # ── Click entry button ─────────────────────────────────────────────────────
    click_result = "no_button"
    if giveaway.button_row is not None and giveaway.button_col is not None:
        click_result = await click_enter_button(
            message, giveaway.button_row, giveaway.button_col
        )

    # ── Log entry to Saved Messages + DB ──────────────────────────────────────
    await logger.log_entry(client, giveaway, message, join_result, click_result)

    # ── Schedule win recheck ───────────────────────────────────────────────────
    row = db.get_giveaway(giveaway.message_id, giveaway.channel_id)
    if row:
        win_checker.schedule_recheck(row["id"], giveaway.deadline_ts)


# ── Main ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    db.init_db()
    log.info("Database initialised at %s", config.DB_PATH)

    # Use StringSession for headless / server deployments; fall back to file session.
    session = StringSession(config.SESSION_STRING) if config.SESSION_STRING else config.SESSION_NAME
    client = TelegramClient(session, config.API_ID, config.API_HASH)

    if config.SESSION_STRING:
        # StringSession already carries auth — just connect, no phone prompt.
        await client.connect()
        if not await client.is_user_authorized():
            log.error("SESSION_STRING is set but account is not authorised. Regenerate the string.")
            sys.exit(1)
    else:
        await client.start(phone=config.PHONE)

    # Handle 2FA if the account has a cloud password.
    if not await client.is_user_authorized():
        log.error("Not authorised — check phone number or session file.")
        sys.exit(1)

    me = await client.get_me()
    my_username = me.username or str(me.id)
    log.info("Logged in as @%s (id=%d)", my_username, me.id)

    # Wire the win-checker module.
    win_checker.initialise(client, my_username)

    # ── Register event handlers ────────────────────────────────────────────────

    @client.on(events.NewMessage(incoming=True))
    async def on_new_message(event: events.NewMessage.Event) -> None:
        try:
            await handle_potential_giveaway(client, event)
        except Exception as exc:                        # noqa: BLE001
            log.exception("Unhandled error in message handler: %s", exc)
            await logger.send_alert(client, f"Handler error: {exc}")

    @client.on(events.MessageEdited())
    async def on_edited(event: events.MessageEdited.Event) -> None:
        try:
            await win_checker.on_message_edited(event)
        except Exception as exc:                        # noqa: BLE001
            log.exception("Error in edit handler: %s", exc)

    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def on_private_dm(event: events.NewMessage.Event) -> None:
        try:
            await win_checker.on_private_message(event)
        except Exception as exc:                        # noqa: BLE001
            log.exception("Error in DM handler: %s", exc)

    # ── Graceful shutdown on SIGINT / SIGTERM ──────────────────────────────────
    loop = asyncio.get_running_loop()

    def _shutdown(sig: signal.Signals) -> None:
        log.info("Received %s — shutting down…", sig.name)
        loop.create_task(_graceful_exit(client))

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown, sig)
        except NotImplementedError:
            # Windows does not support add_signal_handler for all signals.
            pass

    log.info("Bot is running.  Watching all joined chats for giveaways…")
    await client.run_until_disconnected()


async def _graceful_exit(client: TelegramClient) -> None:
    await logger.send_alert(client, "Bot is shutting down gracefully.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
