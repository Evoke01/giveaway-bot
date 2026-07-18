"""
parser.py — Extract structured GiveawayInfo from a detected message.

Design notes:
  • Mentions are extracted from entity offsets (MessageEntityMention /
    MessageEntityMentionName), not raw-text regex, to avoid false positives
    inside prize descriptions.
  • Telethon entity offsets are UTF-16 code-unit offsets, NOT Python str
    indices. We convert properly using a helper.
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
    is_ended: bool = False


# ── Public entry point ─────────────────────────────────────────────────────────

def parse_giveaway(message: Message) -> GiveawayInfo:
    text = message.raw_text or ""
    deadline_ts = _parse_deadline(text, base=message.date)

    return GiveawayInfo(
        message_id=message.id,
        channel_id=message.chat_id,
        channel_username=_get_channel_username(message),
        prize=_extract_prize(text),
        host=_extract_host(text),
        required_channels=_extract_required_channels(message),
        button_row=_find_button_row(message),
        button_col=_find_button_col(message),
        deadline_ts=deadline_ts,
        is_ended=_is_ended(text, deadline_ts),
    )


# ── Internal helpers ───────────────────────────────────────────────────────────

def _get_channel_username(message: Message) -> Optional[str]:
    chat = getattr(message, "chat", None)
    return getattr(chat, "username", None)


def _extract_prize(text: str) -> Optional[str]:
    """
    Extract prize from common giveaway formats:
      - "Prize: $100"
      - "🏆 $100 USDT"
      - "$100 GIVEAWAY" / "🎊 $100 GIVEAWAY 🎊"  (first line heuristic)
      - "Winning: ..."
    """
    # Explicit prize label
    for pattern in [
        r"(?i)prize[s]?[:\s]+(.+)",
        r"(?i)🏆\s*(.+)",
        r"(?i)winning[:\s]+(.+)",
        r"(?i)reward[s]?[:\s]+(.+)",
        r"(?i)amount[:\s]+(.+)",
    ]:
        m = re.search(pattern, text)
        if m:
            val = m.group(1).strip()
            # strip trailing emoji / punctuation clutter
            val = re.sub(r"[\U0001F300-\U0001FFFF\s]+$", "", val).strip()
            if val:
                return val[:200]

    # Heuristic: first line often contains the prize (e.g. "🎊 $100 GIVEAWAY 🎊")
    first_line = text.split("\n")[0].strip()
    # Must contain a currency / number signal
    if re.search(r"[\$\€\£\₿\d]", first_line) and len(first_line) < 120:
        # strip leading/trailing emoji
        cleaned = re.sub(r"^[\U0001F300-\U0001FFFF\s🎊🎉🏆💰]+", "", first_line).strip()
        cleaned = re.sub(r"[\U0001F300-\U0001FFFF\s🎊🎉🏆💰]+$", "", cleaned).strip()
        if cleaned:
            return cleaned[:200]

    return None


def _extract_host(text: str) -> Optional[str]:
    """
    Extract host from patterns like:
      - "Hosted By: @aralol"
      - "Hosted by @aralol"
      - "Host: @aralol"
      - "by @aralol"
    """
    patterns = [
        r"(?i)host(?:ed)?\s+by[:\s]+(@\w+)",
        r"(?i)host[:\s]+(@\w+)",
        r"(?i)\bby[:\s]+(@\w+)",
        r"(?i)organiz(?:er|ed\s+by)[:\s]+(@\w+)",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return None


# ── UTF-16 entity offset helper ────────────────────────────────────────────────
# Telegram encodes entity offsets in UTF-16 code units.
# Python strings are UCS-4 (each char = 1 index), so BMP chars are fine but
# supplementary chars (e.g. most emoji: U+1F600 etc.) occupy 2 UTF-16 units.
# We must convert UTF-16 offset → Python str index to slice correctly.

def _utf16_to_str_index(text: str, utf16_offset: int) -> int:
    """Convert a UTF-16 code-unit offset to a Python string character index."""
    idx = 0
    cu = 0  # UTF-16 code units consumed
    while cu < utf16_offset and idx < len(text):
        cp = ord(text[idx])
        cu += 2 if cp > 0xFFFF else 1
        idx += 1
    return idx


def _entity_text(text: str, offset: int, length: int) -> str:
    """Slice text using UTF-16 code-unit offset + length."""
    start = _utf16_to_str_index(text, offset)
    end   = _utf16_to_str_index(text, offset + length)
    return text[start:end]


def _extract_required_channels(message: Message) -> list[str]:
    """
    Parse channel @mentions from entity offsets inside a 'Giveaway Conditions'
    or 'Requirements' section.  Falls back to all @mentions in the message if
    no labelled section exists.

    Uses proper UTF-16 offset conversion to avoid character mangling.
    """
    text = message.raw_text or ""
    entities = message.entities or []

    # Locate section boundaries (case-insensitive header search).
    section_start = _find_section_start(
        text, ["giveaway conditions", "requirements", "must be a member", "join"]
    )
    section_end = _find_section_end(text, section_start)

    mentions: list[str] = []
    for ent in entities:
        utf16_offset = ent.offset

        # Convert UTF-16 offset to Python str index for boundary check
        str_offset = _utf16_to_str_index(text, utf16_offset)

        if section_start is not None and not (section_start <= str_offset < section_end):
            continue

        if isinstance(ent, MessageEntityMention):
            # Use proper UTF-16 slicing
            raw = _entity_text(text, ent.offset, ent.length)
            handle = raw.lstrip("@").strip()
            if handle and _is_valid_username(handle):
                mentions.append(handle)

        elif isinstance(ent, MessageEntityMentionName):
            # Inline mention — skip; join_channels() can't use a bare user_id.
            pass

    # Also try regex fallback inside the section for robustness
    if section_start is not None:
        section_text = text[section_start:section_end]
        for m in re.finditer(r"@([A-Za-z][A-Za-z0-9_]{3,})", section_text):
            handle = m.group(1)
            if handle and _is_valid_username(handle):
                mentions.append(handle)

    return list(dict.fromkeys(mentions))   # dedupe, preserve order


def _is_valid_username(handle: str) -> bool:
    """Telegram usernames: 5-32 chars, alphanumeric + underscore, no leading digit."""
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9_]{3,31}$", handle))


def _find_section_start(text: str, headers: list[str]) -> Optional[int]:
    lower = text.lower()
    best = None
    for h in headers:
        idx = lower.find(h)
        if idx != -1 and (best is None or idx < best):
            best = idx
    return best


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
    return best_pos if best_score >= 0.55 else (None, None)


# ── Ended-giveaway detection ───────────────────────────────────────────────────

_ENDED_PATTERNS = re.compile(
    r"(?i)(giveaway\s+(?:has\s+)?ended|entries?\s+closed|winner[s]?\s+(?:have\s+been\s+)?(?:selected|announced|chosen)|"
    r"giveaway\s+(?:is\s+)?over|ended\s+ago|closed\s+ago)",
    re.IGNORECASE,
)


def _is_ended(text: str, deadline_ts: Optional[int]) -> bool:
    """Return True if the giveaway is already over."""
    # Keyword signal in text
    if _ENDED_PATTERNS.search(text):
        return True
    # Deadline already passed
    if deadline_ts and deadline_ts < time.time():
        return True
    return False


# ── Deadline parsing ───────────────────────────────────────────────────────────

_RELATIVE_PATTERN = re.compile(
    r"ends?\s+in[:\s]*"
    r"(?:(\d+)\s*d(?:ays?)?)?[\s,]*"
    r"(?:(\d+)\s*h(?:ours?)?)?[\s,]*"
    r"(?:(\d+)\s*m(?:in(?:utes?)?)?)?[\s,]*"
    r"(?:(\d+)\s*s(?:ec(?:onds?)?)?)?",
    re.IGNORECASE,
)

# Also handle "X days" / "X hours" standalone near "ends" context
_RELATIVE_DAYS_PATTERN = re.compile(
    r"(?:ends?\s+in|in\s+)(\d+)\s+(day|hour|minute|week)s?",
    re.IGNORECASE,
)

_ABSOLUTE_PATTERN = re.compile(
    r"ends?\s+(?:on\s+)?(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
    re.IGNORECASE,
)

# ISO-like: "2026-07-27 20:32 UTC"
_ISO_PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}(?::\d{2})?(?:\s*UTC)?)",
    re.IGNORECASE,
)


def _parse_deadline(text: str, base: datetime) -> Optional[int]:
    # Ensure base is timezone-aware.
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)

    # ── ISO / absolute datetime ────────────────────────────────────────────────
    m = _ISO_PATTERN.search(text)
    if m:
        raw = m.group(1).strip().rstrip("UTC").strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
            try:
                dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
                return int(dt.timestamp())
            except ValueError:
                pass

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

    # ── Relative: "ends in 6 days" ────────────────────────────────────────────
    m = _RELATIVE_DAYS_PATTERN.search(text)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        multipliers = {"day": 86400, "hour": 3600, "minute": 60, "week": 604800}
        secs = n * multipliers.get(unit, 86400)
        if secs > 0:
            return int(base.timestamp()) + secs

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
