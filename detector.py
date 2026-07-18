"""
detector.py — Fast first-pass filter to decide whether an incoming message
is a giveaway candidate before the more expensive parser runs.
"""

from telethon.tl.types import Message

import config
import db


def is_giveaway(message: Message) -> bool:
    """
    Return True when:
      1. The message text contains at least one configured keyword.
      2. The message has at least one inline button row.
      3. The source channel is not blacklisted.

    This is intentionally cheap — no network calls, no DB writes.
    """
    text = (message.raw_text or "").lower()

    # ── keyword gate ──────────────────────────────────────────────────────────
    keyword_hit = any(k in text for k in config.GIVEAWAY_KEYWORDS)
    if not keyword_hit:
        return False

    # ── button gate ───────────────────────────────────────────────────────────
    has_buttons = bool(message.buttons)
    if not has_buttons:
        return False

    # ── blacklist gate ────────────────────────────────────────────────────────
    chat_id = message.chat_id
    if chat_id and db.is_blacklisted(chat_id):
        return False

    return True


def already_entered(message: Message) -> bool:
    """Return True if we have already processed this exact message."""
    return db.is_tracked(message.id, message.chat_id)
