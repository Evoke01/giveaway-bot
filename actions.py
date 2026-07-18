"""
actions.py — Telegram side-effects: join channels and click entry buttons.

All functions respect the configured rate caps and random pacing delays.
FloodWaitError is always honoured with a real sleep — never retried immediately.
"""

import asyncio
import logging
import random
import re
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
        while True:
            current_joins = db.joins_in_last_hour()
            if current_joins < config.MAX_JOINS_PER_HOUR:
                break
            log.warning("Hourly join cap (%d/%d) reached — sleeping 5 mins before joining @%s",
                        current_joins, config.MAX_JOINS_PER_HOUR, uname)
            await asyncio.sleep(300)

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
      - "clicked_ok"            — received a positive response from the bot
      - "giveaway_ended"        — bot replied that the giveaway is already over
      - "clicked_no_response"   — click sent but bot returned nothing
      - "click_failed:<reason>" — exception raised
    """
    # Small random pre-click delay — mimics human behaviour.
    delay = random.uniform(config.CLICK_DELAY_MIN, config.CLICK_DELAY_MAX)
    await asyncio.sleep(delay)

    # Patterns that indicate the giveaway is already closed
    _ENDED_RESPONSE = re.compile(
        r"(giveaway\s+(?:has\s+)?ended|entries?\s+(?:are\s+)?closed|"
        r"already\s+ended|giveaway\s+(?:is\s+)?over|not\s+active)",
        re.IGNORECASE,
    )

    try:
        button = message.buttons[button_row][button_col]
        url = getattr(button.button, "url", None)
        deep_link_m = re.search(r"t\.me/([^/?]+)\?start=([a-zA-Z0-9_-]+)", url or "", re.IGNORECASE)

        if deep_link_m:
            # ── Verification Bot Deep-Link Flow ────────────────────────────────
            bot_username = deep_link_m.group(1)
            start_payload = deep_link_m.group(2)
            client = message.client
            log.info("Deep link detected. Sending /start %s to @%s", start_payload, bot_username)

            await client.send_message(bot_username, f"/start {start_payload}")

            # Wait up to 10 seconds for the bot to reply with a verification button
            for _ in range(5):
                await asyncio.sleep(2)
                recent = await client.get_messages(bot_username, limit=3)
                for reply_msg in recent:
                    if reply_msg.out:
                        continue  # Skip our own messages
                    
                    if reply_msg.buttons:
                        for r, row in enumerate(reply_msg.buttons):
                            for c, btn in enumerate(row):
                                label = (btn.text or "").lower()
                                if any(phrase in label for phrase in ["joined", "check", "verify", "done"]):
                                    log.info("Found verification button: %r. Clicking...", btn.text)
                                    resp = await reply_msg.click(r, c)
                                    resp_text = getattr(resp, "message", None) or str(resp)
                                    log.info("Verification click response: %s", resp_text)
                                    if _ENDED_RESPONSE.search(str(resp_text)):
                                        return "giveaway_ended"
                                    return "clicked_verification_bot"

            return "deep_link_sent_no_verification_button"

        else:
            # ── Standard Callback Button Flow ──────────────────────────────────
            resp = await message.click(button_row, button_col)
            if resp:
                resp_text = getattr(resp, "message", None) or str(resp)
                log.info("Button clicked; bot responded: %s", resp_text)
                if _ENDED_RESPONSE.search(str(resp_text)):
                    return "giveaway_ended"
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

