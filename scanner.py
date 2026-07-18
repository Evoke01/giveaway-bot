"""
scanner.py — Startup backfill scan.

On bot launch, iterates every dialog (channel / group / supergroup) the account
is in and checks recent messages for active giveaways that were missed while the
bot was offline.  Already-entered giveaways (tracked in DB) are skipped.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat, Message

import config

if TYPE_CHECKING:
    pass

log = logging.getLogger("scanner")

# How many recent messages to fetch per dialog during the backfill scan.
# Configurable via SCAN_HISTORY_LIMIT in .env (default: 50)
SCAN_HISTORY_LIMIT: int = int(__import__("os").getenv("SCAN_HISTORY_LIMIT", "50"))

# Delay between processing each dialog to avoid flooding (seconds)
SCAN_DIALOG_DELAY: float = float(__import__("os").getenv("SCAN_DIALOG_DELAY", "1.5"))


async def run_startup_scan(client: TelegramClient, handler) -> None:
    """
    Walk all joined channels/groups and feed recent messages through the
    giveaway pipeline.  `handler` is the same coroutine used for live events:
        handler(client, FakeEvent(message)) -> None
    """
    log.info("=== Starting backfill scan (SCAN_HISTORY_LIMIT=%d per dialog) ===",
             SCAN_HISTORY_LIMIT)

    scanned_dialogs = 0
    found_giveaways = 0

    async for dialog in client.iter_dialogs():
        entity = dialog.entity

        # Only scan channels and groups — not private DMs
        if not isinstance(entity, (Channel, Chat)):
            continue

        dialog_name = dialog.name or str(dialog.id)

        try:
            async for message in client.iter_messages(entity, limit=SCAN_HISTORY_LIMIT):
                if not isinstance(message, Message):
                    continue
                if not message.raw_text:
                    continue

                # Wrap in a lightweight fake-event compatible object so the
                # existing handler can process it without modification.
                fake_event = _FakeEvent(message)
                try:
                    await handler(client, fake_event)
                    # Small delay between messages to stay rate-limit safe
                    await asyncio.sleep(0.3)
                except Exception as exc:          # noqa: BLE001
                    log.warning("Scan error on msg %d in %s: %s",
                                message.id, dialog_name, exc)

            scanned_dialogs += 1
            log.debug("Scanned dialog: %s", dialog_name)

        except Exception as exc:                  # noqa: BLE001
            log.warning("Could not scan dialog %s: %s", dialog_name, exc)

        # Polite delay between dialogs
        await asyncio.sleep(SCAN_DIALOG_DELAY)

    log.info("=== Backfill scan complete — %d dialogs scanned, %d new giveaways found ===",
             scanned_dialogs, found_giveaways)


class _FakeEvent:
    """
    Minimal shim that makes a raw Message look enough like a
    NewMessage.Event for handle_potential_giveaway() to consume it.
    """

    def __init__(self, message: Message) -> None:
        self.message = message

    # The handler only ever accesses event.message, so this is sufficient.
