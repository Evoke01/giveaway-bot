"""
config.py — Loads all environment variables and defines tunable constants.
Edit .env to configure the bot; do not hardcode secrets here.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram credentials ──────────────────────────────────────────────────────
API_ID: int = int(os.environ["API_ID"])
API_HASH: str = os.environ["API_HASH"]
PHONE: str = os.environ["PHONE"]          # e.g. +12025550123
SESSION_NAME: str = os.getenv("SESSION_NAME", "giveaway_bot")

# ── Detection keywords ─────────────────────────────────────────────────────────
# Any message containing at least one of these (case-insensitive) AND at least
# one inline button is treated as a potential giveaway.
GIVEAWAY_KEYWORDS: list[str] = [
    "giveaway",
    "entries:",
    "ends in",
    "hosted by",
    "winner",
    "prize",
    "раздача",      # Russian
    "çekiliş",      # Turkish
]

# ── Button-click target phrases ────────────────────────────────────────────────
# The parser fuzzy-matches button labels against these (lower-cased).
TARGET_BUTTON_PHRASES: list[str] = [
    "enter giveaway",
    "join giveaway",
    "participate",
    "enter now",
    "join now",
    "участвовать",
    "katıl",
]

# ── Rate-limiting guardrails ───────────────────────────────────────────────────
MAX_JOINS_PER_HOUR: int = int(os.getenv("MAX_JOINS_PER_HOUR", "15"))
JOIN_DELAY_MIN: float = float(os.getenv("JOIN_DELAY_MIN", "1.0"))   # seconds
JOIN_DELAY_MAX: float = float(os.getenv("JOIN_DELAY_MAX", "5.0"))   # seconds
CLICK_DELAY_MIN: float = float(os.getenv("CLICK_DELAY_MIN", "2.0"))
CLICK_DELAY_MAX: float = float(os.getenv("CLICK_DELAY_MAX", "6.0"))

# ── Win-checking ───────────────────────────────────────────────────────────────
# How many minutes after the stored deadline to recheck the channel for results.
WIN_CHECK_GRACE_MINUTES: int = int(os.getenv("WIN_CHECK_GRACE_MINUTES", "60"))

# ── Database ───────────────────────────────────────────────────────────────────
DB_PATH: str = os.getenv("DB_PATH", "giveaways.db")

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
