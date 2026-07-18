"""
actions.py — Telegram side-effects: join channels and click entry buttons.

All functions respect the configured rate caps and random pacing delays.
FloodWaitError is always honoured with a real sleep — never retried immediately.
"""

import asyncio
import logging
import random
import time

from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    FloodWaitError,
    UserAlreadyParticipantError,
    UsernameNotOccupiedError,
    UsernameInvalidError,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import Message

import config
import db

log = logging.getLogger(__name__)


# ── Join channels ──────────────────────────────────────────────────────────────

async def join_channels(
    client: TelegramClient,
    usernames: list[str],
) -> dict[str, str]:
    """
    Attempt to join each channel in `usernames`.

    Returns a dict mapping username → status string:
      - "joined"              — successfully joined
      - "already_member"      — account is already a member
      - "flood_wait_slept"    — FloodWait encountered; slept and moved on
      - "unreachable"         — channel private, username invalid, or not found
      - "rate_capped"         — global hourly cap reached; skipped
      - "error:<msg>"         — unexpected error
    """
    results: dict[str, str] = {}

    for uname in usernames:
        # ── hourly rate cap ───────────────────────────────────────────────────
        if db.joins_in_last_hour() >= config.MAX_JOINS_PER_HOUR:
            log.warning("Hourly join cap (%d) reached — skipping @%s",
                        config.MAX_JOINS_PER_HOUR, uname)
            results[uname] = "rate_capped"
            continue

        try:
            await client(JoinChannelRequest(uname))
            db.log_join()
            results[uname] = "joined"
            log.info("Joined channel @%s", uname)

        except UserAlreadyParticipantError:
            results[uname] = "already_member"
            log.debug("Already a member of @%s", uname)

        except FloodWaitError as exc:
            wait = exc.seconds
            log.warning("FloodWait %ds for @%s — sleeping", wait, uname)
            await asyncio.sleep(wait)
            results[uname] = "flood_wait_slept"

        except (ChannelPrivateError, UsernameNotOccupiedError, UsernameInvalidError):
            results[uname] = "unreachable"
            log.warning("Cannot reach @%s (private/not found)", uname)

        except Exception as exc:                        # noqa: BLE001
            results[uname] = f"error:{exc}"
            log.error("Unexpected error joining @%s: %s", uname, exc)

        # ── anti-spam random delay between every join ─────────────────────────
        delay = random.uniform(config.JOIN_DELAY_MIN, config.JOIN_DELAY_MAX)
        await asyncio.sleep(delay)

    return results


def join_summary(results: dict[str, str]) -> str:
    """Human-readable one-liner of join results for the log message."""
    if not results:
        return "no channels required"
    parts = [f"@{u}={s}" for u, s in results.items()]
    return ", ".join(parts)


# ── Click entry button ─────────────────────────────────────────────────────────

async def click_enter_button(
    message: Message,
    button_row: int,
    button_col: int,
) -> str:
    """
    Click the inline button at (button_row, button_col) and return a status
    string:
      - "clicked_ok"            — received a response from the bot
      - "clicked_no_response"   — click sent but bot returned nothing
      - "click_failed:<reason>" — exception raised
    """
    # Small random pre-click delay — mimics human behaviour.
    delay = random.uniform(config.CLICK_DELAY_MIN, config.CLICK_DELAY_MAX)
    await asyncio.sleep(delay)

    try:
        resp = await message.click(button_row, button_col)
        if resp:
            log.info("Button clicked; bot responded: %s", getattr(resp, "message", resp))
            return "clicked_ok"
        log.info("Button clicked; no bot response (may still be counted).")
        return "clicked_no_response"

    except FloodWaitError as exc:
        log.warning("FloodWait %ds on button click — sleeping", exc.seconds)
        await asyncio.sleep(exc.seconds)
        return f"click_failed:flood_wait_{exc.seconds}s"

    except Exception as exc:                            # noqa: BLE001
        log.error("Button click failed: %s", exc)
        return f"click_failed:{exc}"
