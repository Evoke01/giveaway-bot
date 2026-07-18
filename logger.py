"""
logger.py — Forward giveaway posts and entry summaries to Saved Messages.

Saved Messages is the user's private chat with themselves (`me`).
Two messages are sent per entry:
  1. A forward of the original giveaway post (for context / reference).
  2. A structured text summary with join/click results.
"""

import logging
import time

from telethon import TelegramClient
from telethon.tl.types import Message

import db
from parser import GiveawayInfo

log = logging.getLogger(__name__)


async def log_entry(
    client: TelegramClient,
    giveaway: GiveawayInfo,
    message: Message,
    join_result: dict[str, str],
    click_result: str,
) -> None:
    """
    1. Forward the original giveaway message to Saved Messages.
    2. Send a plain-text summary with all metadata.
    3. Persist to the database.
    """

    # ── Forward original post ─────────────────────────────────────────────────
    fwd_msg_id = None
    try:
        msgs = await client.forward_messages("me", message)
        if msgs and isinstance(msgs, list) and msgs[0]:
            fwd_msg_id = msgs[0].id
        elif msgs:
            fwd_msg_id = getattr(msgs, "id", None)
    except Exception as exc:                            # noqa: BLE001
        log.warning("Could not forward giveaway message: %s", exc)

    # ── Build summary ─────────────────────────────────────────────────────────
    deadline_str = (
        _format_ts(giveaway.deadline_ts) if giveaway.deadline_ts else "unknown"
    )
    join_str = (
        ", ".join(f"@{u}={s}" for u, s in join_result.items())
        if join_result else "none required"
    )
    summary = (
        f"🎯 *Giveaway Entered*\n"
        f"Prize:    {giveaway.prize or 'unknown'}\n"
        f"Host:     {giveaway.host or 'unknown'}\n"
        f"Channel:  @{giveaway.channel_username or giveaway.channel_id}\n"
        f"Join:     {join_str}\n"
        f"Click:    {click_result}\n"
        f"Deadline: {deadline_str}"
    )

    # ── Send summary ──────────────────────────────────────────────────────────
    summary_msg_id = None
    try:
        sent = await client.send_message("me", summary, parse_mode="markdown")
        if sent:
            summary_msg_id = sent.id
    except Exception as exc:                            # noqa: BLE001
        log.error("Failed to send summary to Saved Messages: %s", exc)

    # ── Persist ───────────────────────────────────────────────────────────────
    join_status = _derive_join_status(join_result)
    db.upsert_giveaway(
        message_id=giveaway.message_id,
        channel_id=giveaway.channel_id,
        channel_username=giveaway.channel_username,
        prize=giveaway.prize,
        host=giveaway.host,
        required_channels=giveaway.required_channels,
        deadline_ts=giveaway.deadline_ts,
        join_status=join_status,
        click_status=click_result,
        log_fwd_msg_id=fwd_msg_id,
        log_summary_msg_id=summary_msg_id,
    )
    log.info(
        "Entry logged — msg=%d ch=%d join=%s click=%s",
        giveaway.message_id, giveaway.channel_id, join_status, click_result,
    )


async def log_win(client: TelegramClient, giveaway_row, win_message: Message | None = None) -> None:
    """Notify Saved Messages when a win is detected."""
    summary = (
        f"🏆 *You Won!*\n"
        f"Prize:   {giveaway_row['prize'] or 'unknown'}\n"
        f"Channel: @{giveaway_row['channel_username'] or giveaway_row['channel_id']}"
    )
    try:
        await client.send_message("me", summary, parse_mode="markdown")
        if win_message:
            await client.forward_messages("me", win_message)
    except Exception as exc:                            # noqa: BLE001
        log.error("Failed to send win notification: %s", exc)


async def send_alert(client: TelegramClient, text: str) -> None:
    """Send an operational self-alert to Saved Messages (used for crash/errors)."""
    try:
        await client.send_message("me", f"⚠️ *Bot Alert*\n{text}", parse_mode="markdown")
    except Exception:                                   # noqa: BLE001
        pass   # If we can't send, we can't do much more.


# ── Internal helpers ───────────────────────────────────────────────────────────

def _format_ts(ts: int) -> str:
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _derive_join_status(join_result: dict[str, str]) -> str:
    if not join_result:
        return "no_channels"
    statuses = set(join_result.values())
    if all(s in {"joined", "already_member"} for s in statuses):
        return "joined"
    if any(s in {"joined", "already_member"} for s in statuses):
        return "partial"
    if all(s == "rate_capped" for s in statuses):
        return "rate_capped"
    return "failed"
