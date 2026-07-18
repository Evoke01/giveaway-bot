"""
scanner.py — Startup backfill scan.

On bot launch, iterates every dialog (channel / group / supergroup) the account
is in and checks recent messages for active giveaways that were missed while the
bot was offline.  Already-entered giveaways (tracked in DB) are skipped.

Performance:
  - Dialogs are processed with a concurrency semaphore (default: 3 at once)
  - Messages within each dialog are filtered for keyword hits before parsing
  - Ended giveaways are caught by the parser's is_ended check
"""

import asyncio
import logging
import os
from typing import Callable, Awaitable

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat, Message

import config

log = logging.getLogger("scanner")

# How many recent messages to fetch per dialog during the backfill scan.
SCAN_HISTORY_LIMIT: int = int(os.getenv("SCAN_HISTORY_LIMIT", "50"))

# Seconds to wait between scanning each dialog (be polite to Telegram)
SCAN_DIALOG_DELAY: float = float(os.getenv("SCAN_DIALOG_DELAY", "1.0"))

# Max concurrent dialogs being scanned at once
SCAN_CONCURRENCY: int = int(os.getenv("SCAN_CONCURRENCY", "3"))


async def run_startup_scan(client: TelegramClient, handler: Callable) -> None:
    """
    Walk all joined channels/groups and feed recent messages through the
    giveaway pipeline.  `handler` is the same coroutine used for live events.
    """
    log.info(
        "=== Backfill scan starting | limit=%d msgs/dialog, concurrency=%d ===",
        SCAN_HISTORY_LIMIT, SCAN_CONCURRENCY,
    )

    # Collect all dialogs first (avoids interleaving with scan network calls)
    dialogs = []
    try:
        async for dialog in client.iter_dialogs():
            if isinstance(dialog.entity, (Channel, Chat)):
                dialogs.append(dialog)
    except Exception as exc:
        log.error("Failed to fetch dialogs for scan: %s", exc)
        return

    log.info("Scanning %d dialogs for missed giveaways…", len(dialogs))

    sem = asyncio.Semaphore(SCAN_CONCURRENCY)
    found_total = 0
    lock = asyncio.Lock()

    async def scan_one(dialog) -> int:
        async with sem:
            count = await _scan_dialog(client, dialog, handler)
            await asyncio.sleep(SCAN_DIALOG_DELAY)
            return count

    tasks = [asyncio.create_task(scan_one(d)) for d in dialogs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, int):
            found_total += r
        elif isinstance(r, Exception):
            log.warning("Scan task failed: %s", r)

    log.info(
        "=== Backfill scan complete | %d dialogs | %d new giveaways entered ===",
        len(dialogs), found_total,
    )


async def _scan_dialog(client: TelegramClient, dialog, handler: Callable) -> int:
    """Scan one dialog; return count of giveaways entered."""
    entity = dialog.entity
    dialog_name = dialog.name or str(dialog.id)
    entered = 0

    try:
        async for message in client.iter_messages(entity, limit=SCAN_HISTORY_LIMIT):
            if not isinstance(message, Message):
                continue
            if not message.raw_text:
                continue

            # Quick keyword pre-filter (same as detector) before creating task
            text_lower = (message.raw_text or "").lower()
            if not any(k in text_lower for k in config.GIVEAWAY_KEYWORDS):
                continue
            if not message.buttons:
                continue

            fake_event = _FakeEvent(message)
            try:
                await handler(client, fake_event)
                entered += 1
            except Exception as exc:
                log.warning("Scan handler error on msg %d in %s: %s",
                            message.id, dialog_name, exc)

            # Small inter-message delay
            await asyncio.sleep(0.2)

    except Exception as exc:
        log.warning("Could not scan dialog %s: %s", dialog_name, exc)

    return entered


class _FakeEvent:
    """
    Minimal shim that makes a raw Message look enough like a
    NewMessage.Event for handle_potential_giveaway() to consume it.
    """

    __slots__ = ("message",)

    def __init__(self, message: Message) -> None:
        self.message = message
