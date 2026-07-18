"""
win_checker.py — Three complementary strategies to detect giveaway results.

Strategy 1 — Edit listener:
    The original giveaway post is often edited to announce winners.
    We watch for MessageEdited events and check if our username appears.

Strategy 2 — Scheduled recheck:
    Each tracked giveaway spawns one asyncio task that sleeps until
    (deadline + grace_period) and then scans recent channel messages.

Strategy 3 — DM listener:
    Many giveaway bots DM winners directly.  We watch all private incoming
    messages for win-notification patterns.

All three strategies write to the same `win_status` DB column and send
a Saved Messages notification via logger.log_win().
"""

import asyncio
import logging
import re
import time
from typing import Optional

from telethon import TelegramClient, events
from telethon.tl.types import Message

import config
import db
import logger

log = logging.getLogger(__name__)

# Populated at startup by main.py after the client is connected.
_MY_USERNAME: Optional[str] = None
_CLIENT: Optional[TelegramClient] = None


def initialise(client: TelegramClient, my_username: str) -> None:
    """Call once after connecting; wires module-level state."""
    global _CLIENT, _MY_USERNAME
    _CLIENT = client
    _MY_USERNAME = my_username.lower().lstrip("@")


# ── Strategy 1: edited-post listener ─────────────────────────────────────────

async def on_message_edited(event: events.MessageEdited.Event) -> None:
    """Registered as an event handler in main.py."""
    msg = event.message
    if not db.is_tracked(msg.id, event.chat_id):
        return

    row = db.get_giveaway(msg.id, event.chat_id)
    if row and row["win_status"] != "unknown":
        return   # already resolved

    if _username_mentioned(msg):
        log.info("Win detected via post edit — msg=%d ch=%d", msg.id, event.chat_id)
        db.mark_won(msg.id, event.chat_id)
        await logger.log_win(_CLIENT, row, msg)
    else:
        # Edit exists but we're not mentioned — assume lost if deadline passed.
        if row and row["deadline_ts"] and row["deadline_ts"] < time.time():
            db.mark_lost(msg.id, event.chat_id)


# ── Strategy 2: per-giveaway scheduled recheck ───────────────────────────────

def schedule_recheck(giveaway_id: int, deadline_ts: Optional[int]) -> None:
    """
    Fire-and-forget asyncio task.  Wakes up after deadline + grace period
    and scans recent messages in the source channel.
    """
    asyncio.create_task(_recheck_task(giveaway_id, deadline_ts))


async def _recheck_task(giveaway_id: int, deadline_ts: Optional[int]) -> None:
    with db._conn() as con:
        row = con.execute(
            "SELECT * FROM giveaways WHERE id=?", (giveaway_id,)
        ).fetchone()

    if not row:
        return

    # Sleep until deadline + configured grace period.
    now = time.time()
    if deadline_ts:
        sleep_for = max(0.0, (deadline_ts - now) + config.WIN_CHECK_GRACE_MINUTES * 60)
    else:
        sleep_for = config.WIN_CHECK_GRACE_MINUTES * 60

    log.debug(
        "Recheck task for giveaway %d sleeping %.0fs", giveaway_id, sleep_for
    )
    await asyncio.sleep(sleep_for)

    # Re-fetch in case something already resolved it.
    row = db.get_giveaway(row["message_id"], row["channel_id"])
    if not row or row["win_status"] != "unknown":
        return

    channel_id = row["channel_id"]
    try:
        recent: list[Message] = await _CLIENT.get_messages(channel_id, limit=30)
    except Exception as exc:                            # noqa: BLE001
        log.warning("get_messages failed for ch=%d: %s", channel_id, exc)
        return

    for msg in recent:
        if _looks_like_results_post(msg) and _username_mentioned(msg):
            log.info("Win detected via recheck — msg=%d ch=%d", row["message_id"], channel_id)
            db.mark_won(row["message_id"], channel_id)
            await logger.log_win(_CLIENT, row, msg)
            return

    # No win found after recheck — mark as lost.
    db.mark_lost(row["message_id"], channel_id)
    log.info("Marked lost after recheck — giveaway id=%d", giveaway_id)

    msgs_to_delete = []
    if row["log_fwd_msg_id"]:
        msgs_to_delete.append(row["log_fwd_msg_id"])
    if row["log_summary_msg_id"]:
        msgs_to_delete.append(row["log_summary_msg_id"])

    if msgs_to_delete:
        try:
            await _CLIENT.delete_messages("me", msgs_to_delete)
        except Exception as exc:                          # noqa: BLE001
            log.warning("Could not delete stale log messages: %s", exc)


# ── Strategy 3: private DM listener ──────────────────────────────────────────

async def on_private_message(event: events.NewMessage.Event) -> None:
    """Registered as an event handler in main.py for incoming private messages."""
    msg = event.message
    if _looks_like_win_notification(msg):
        log.info("Win notification DM received from %s", event.chat_id)
        # We don't know which giveaway this belongs to without matching context.
        # Forward to Saved Messages with a generic win alert.
        await logger.send_alert(
            _CLIENT,
            f"🏆 Possible win DM received!\nFrom: {event.chat_id}\n\n{msg.raw_text[:400]}",
        )
        try:
            await _CLIENT.forward_messages("me", msg)
        except Exception:                               # noqa: BLE001
            pass


# ── Pattern helpers ───────────────────────────────────────────────────────────

_WIN_DM_PATTERNS = re.compile(
    r"(congratulations|you\s+won|you\s+are\s+(?:a\s+)?winner|claim\s+your\s+prize)",
    re.IGNORECASE,
)

_RESULTS_PATTERNS = re.compile(
    r"(winner[s]?(\s+are)?[:\s]|results?[:\s]|congratulations\s+to)",
    re.IGNORECASE,
)


def _username_mentioned(message: Message) -> bool:
    """Return True if our @username appears anywhere in the message text."""
    if not _MY_USERNAME:
        return False
    text = (message.raw_text or "").lower()
    return _MY_USERNAME in text


def _looks_like_win_notification(message: Message) -> bool:
    text = message.raw_text or ""
    return bool(_WIN_DM_PATTERNS.search(text))


def _looks_like_results_post(message: Message) -> bool:
    text = message.raw_text or ""
    return bool(_RESULTS_PATTERNS.search(text))
