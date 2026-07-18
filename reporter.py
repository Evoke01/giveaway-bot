"""
reporter.py — Handles startup reporting and stale giveaway cleanup.
"""
import logging
import time

from telethon import TelegramClient

import db

log = logging.getLogger(__name__)


async def run_startup_report(client: TelegramClient) -> None:
    """
    Tally pending giveaways, delete old expired ones, and send a summary to Saved Messages.
    """
    pending = db.get_pending_giveaways()
    now = int(time.time())
    grace_period = 86400  # 24 hours

    active_count = 0
    cleaned_count = 0

    msgs_to_delete = []

    for row in pending:
        deadline = row["deadline_ts"]
        if deadline and (deadline + grace_period) < now:
            # It's dead Jim.
            db.mark_lost(row["message_id"], row["channel_id"])
            cleaned_count += 1
            if row["log_fwd_msg_id"]:
                msgs_to_delete.append(row["log_fwd_msg_id"])
            if row["log_summary_msg_id"]:
                msgs_to_delete.append(row["log_summary_msg_id"])
        else:
            active_count += 1

    if msgs_to_delete:
        log.info("Cleaning up %d stale log messages...", len(msgs_to_delete))
        try:
            # telethon delete_messages can take a list of IDs. Max 100 at a time.
            for i in range(0, len(msgs_to_delete), 100):
                batch = msgs_to_delete[i:i+100]
                await client.delete_messages("me", batch)
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to delete stale logs: %s", exc)

    report = (
        f"📊 *Bot Started!*\n"
        f"🟢 Currently tracking: {active_count} active giveaways\n"
        f"🧹 Cleaned up: {cleaned_count} ended giveaways (logs deleted)"
    )
    try:
        await client.send_message("me", report, parse_mode="markdown")
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to send startup report: %s", exc)
