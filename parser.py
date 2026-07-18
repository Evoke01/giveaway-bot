"""
parser.py — Extract structured GiveawayInfo from a detected message.

Design notes:
  • Mentions are extracted from entity offsets (MessageEntityMention /
    MessageEntityMentionName), not raw-text regex, to avoid false positives
    inside prize descriptions.
  • Button matching uses fuzzy lower-case substring comparison.
  • Deadline parsing handles relative ("Ends In: 2h 30m") and absolute formats.
"""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Optional

from telethon.tl.types import (
    Message,
    MessageEntityMention,
    MessageEntityMentionName,
)

import config


# ── Data class ─────────────────────────────────────────────────────────────────

@dataclass
class GiveawayInfo:
    message_id: int
    channel_id: int
    channel_username: Optional[str]
    prize: Optional[str]
    host: Optional[str]
    required_channels: list[str] = field(default_factory=list)
    button_row: Optional[int] = None
    button_col: Optional[int] = None
    deadline_ts: Optional[int] = None


# ── Public entry point ─────────────────────────────────────────────────────────

def parse_giveaway(message: Message) -> GiveawayInfo:
    text = message.raw_text or ""

    return GiveawayInfo(
        message_id=message.id,
        channel_id=message.chat_id,
        channel_username=_get_channel_username(message),
        prize=_extract_prize(text),
        host=_extract_host(text),
        required_channels=_extract_required_channels(message),
        button_row=_find_button_row(message),
        button_col=_find_button_col(message),
        deadline_ts=_parse_deadline(text, base=message.date),
    )


# ── Internal helpers ───────────────────────────────────────────────────────────

def _get_channel_username(message: Message) -> Optional[str]:
    chat = getattr(message, "chat", None)
    return getattr(chat, "username", None)


def _extract_prize(text: str) -> Optional[str]:
    """
    Heuristic: look for a line containing "Prize:" or "🏆" and take
    the rest of that line as the prize description.
    """
    for pattern in [
        r"(?i)prize[:\s]+(.+)",
        r"(?i)🏆\s*(.+)",
        r"(?i)winning[:\s]+(.+)",
    ]:
        m = re.search(pattern, text)
        if m:
            return m.group(1).strip()[:200]
    return None


def _extract_host(text: str) -> Optional[str]:
    """
    Look for "Hosted by @handle" or "by @handle" patterns.
    """
    m = re.search(r"(?i)hosted\s+by\s+(@\w+)", text)
    if m:
        return m.group(1)
    m = re.search(r"(?i)\bby\s+(@\w+)", text)
    if m:
        return m.group(1)
    return None


def _extract_required_channels(message: Message) -> list[str]:
    """
    Parse channel @mentions from entity offsets inside a 'Giveaway Conditions'
    or 'Requirements' section.  Falls back to all @mentions in the message if
    no labelled section exists.
    """
    text = message.raw_text or ""
    entities = message.entities or []

    # Locate section boundaries (case-insensitive header search).
    section_start = _find_section_start(text, ["giveaway conditions", "requirements", "join"])
    section_end = _find_section_end(text, section_start)

    mentions: list[str] = []
    for ent in entities:
        offset = ent.offset
        if section_start is not None and not (section_start <= offset < section_end):
            continue

        if isinstance(ent, MessageEntityMention):
            handle = text[ent.offset: ent.offset + ent.length]
            mentions.append(handle.lstrip("@"))

        elif isinstance(ent, MessageEntityMentionName):
            # Inline mention — we only know the user_id, not necessarily a channel.
            # Skip; join_channels() can't use a bare user_id.
            pass

    return list(dict.fromkeys(mentions))   # dedupe, preserve order


def _find_section_start(text: str, headers: list[str]) -> Optional[int]:
    lower = text.lower()
    for h in headers:
        idx = lower.find(h)
        if idx != -1:
            return idx
    return None


def _find_section_end(text: str, start: Optional[int]) -> int:
    if start is None:
        return len(text)
    # Next blank line after the section header acts as end of section.
    blank = text.find("\n\n", start)
    return blank if blank != -1 else len(text)


def _find_button_row(message: Message) -> Optional[int]:
    return _find_button_position(message)[0]


def _find_button_col(message: Message) -> Optional[int]:
    return _find_button_position(message)[1]


def _find_button_position(message: Message) -> tuple[Optional[int], Optional[int]]:
    """
    Return (row, col) of the button whose label best matches TARGET_BUTTON_PHRASES.
    Returns (None, None) if no match found.
    """
    if not message.buttons:
        return None, None

    best_score = 0.0
    best_pos: tuple[Optional[int], Optional[int]] = (None, None)

    for r, row in enumerate(message.buttons):
        for c, btn in enumerate(row):
            label = (btn.text or "").lower()
            for phrase in config.TARGET_BUTTON_PHRASES:
                # Exact substring is the best signal.
                if phrase in label:
                    return r, c
                # Fuzzy fallback for slight variations.
                score = SequenceMatcher(None, phrase, label).ratio()
                if score > best_score:
                    best_score = score
                    best_pos = (r, c)

    # Only accept fuzzy matches above a reasonable threshold.
    return best_pos if best_score >= 0.6 else (None, None)


# ── Deadline parsing ───────────────────────────────────────────────────────────

_RELATIVE_PATTERN = re.compile(
    r"ends?\s+in[:\s]*"
    r"(?:(\d+)\s*d(?:ays?)?)?\s*"
    r"(?:(\d+)\s*h(?:ours?)?)?\s*"
    r"(?:(\d+)\s*m(?:in(?:utes?)?)?)?\s*"
    r"(?:(\d+)\s*s(?:ec(?:onds?)?)?)?",
    re.IGNORECASE,
)

_ABSOLUTE_PATTERN = re.compile(
    r"ends?\s+(?:on\s+)?(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
    re.IGNORECASE,
)


def _parse_deadline(text: str, base: datetime) -> Optional[int]:
    # Ensure base is timezone-aware.
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)

    # ── Relative: "Ends In: 2d 3h 30m 10s" ───────────────────────────────────
    m = _RELATIVE_PATTERN.search(text)
    if m and any(m.groups()):
        days    = int(m.group(1) or 0)
        hours   = int(m.group(2) or 0)
        minutes = int(m.group(3) or 0)
        seconds = int(m.group(4) or 0)
        delta = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        if delta.total_seconds() > 0:
            return int((base + delta).timestamp())

    # ── Absolute: "Ends on 25/07/2025" ────────────────────────────────────────
    m = _ABSOLUTE_PATTERN.search(text)
    if m:
        raw = m.group(1).replace(".", "/").replace("-", "/")
        for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d/%m/%y", "%m/%d/%y"):
            try:
                dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
                return int(dt.timestamp())
            except ValueError:
                pass

    return None
