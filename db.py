"""
db.py — SQLite schema bootstrap and all CRUD helpers.
Single-file database; zero external infrastructure required.
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Optional

import config


# ── Schema ─────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS giveaways (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id       INTEGER NOT NULL,
    channel_id       INTEGER NOT NULL,
    channel_username TEXT,
    prize            TEXT,
    host             TEXT,
    required_channels TEXT,          -- JSON list of @handles
    deadline_ts      INTEGER,        -- unix timestamp; NULL if unknown
    join_status      TEXT DEFAULT 'pending',   -- pending/joined/partial/failed
    click_status     TEXT DEFAULT 'pending',   -- pending/clicked/failed/no_button
    win_status       TEXT DEFAULT 'unknown',   -- unknown/won/lost
    entered_at       INTEGER,
    checked_at       INTEGER,
    log_fwd_msg_id   INTEGER,
    log_summary_msg_id INTEGER,
    UNIQUE(message_id, channel_id)
);

CREATE TABLE IF NOT EXISTS channel_filters (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id       INTEGER,
    channel_username TEXT,
    mode             TEXT NOT NULL CHECK(mode IN ('whitelist', 'blacklist')),
    reason           TEXT,
    added_at         INTEGER DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS join_rate_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    joined_at        INTEGER NOT NULL   -- unix timestamp of each channel join
);
"""


# ── Connection context manager ─────────────────────────────────────────────────

@contextmanager
def _conn():
    con = sqlite3.connect(config.DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    """Create tables if they do not yet exist. Call once at startup."""
    with _conn() as con:
        con.executescript(_SCHEMA)
        # Migration for existing databases
        try:
            con.execute("ALTER TABLE giveaways ADD COLUMN log_fwd_msg_id INTEGER")
            con.execute("ALTER TABLE giveaways ADD COLUMN log_summary_msg_id INTEGER")
        except sqlite3.OperationalError:
            pass  # Columns already exist


# ── Giveaway helpers ───────────────────────────────────────────────────────────

def is_tracked(message_id: int, channel_id: int) -> bool:
    """Return True if this (message_id, channel_id) pair is already in the DB."""
    with _conn() as con:
        row = con.execute(
            "SELECT id FROM giveaways WHERE message_id=? AND channel_id=?",
            (message_id, channel_id),
        ).fetchone()
    return row is not None


def upsert_giveaway(
    *,
    message_id: int,
    channel_id: int,
    channel_username: Optional[str],
    prize: Optional[str],
    host: Optional[str],
    required_channels: list[str],
    deadline_ts: Optional[int],
    join_status: str,
    click_status: str,
    log_fwd_msg_id: Optional[int] = None,
    log_summary_msg_id: Optional[int] = None,
) -> int:
    """Insert or update a giveaway row; return the row id."""
    now = int(time.time())
    with _conn() as con:
        con.execute(
            """
            INSERT INTO giveaways
                (message_id, channel_id, channel_username, prize, host,
                 required_channels, deadline_ts, join_status, click_status,
                 win_status, entered_at, log_fwd_msg_id, log_summary_msg_id)
            VALUES (?,?,?,?,?,?,?,?,?,'unknown',?,?,?)
            ON CONFLICT(message_id, channel_id) DO UPDATE SET
                join_status   = excluded.join_status,
                click_status  = excluded.click_status,
                entered_at    = excluded.entered_at,
                log_fwd_msg_id = COALESCE(excluded.log_fwd_msg_id, giveaways.log_fwd_msg_id),
                log_summary_msg_id = COALESCE(excluded.log_summary_msg_id, giveaways.log_summary_msg_id)
            """,
            (
                message_id, channel_id, channel_username, prize, host,
                json.dumps(required_channels), deadline_ts,
                join_status, click_status, now, log_fwd_msg_id, log_summary_msg_id,
            ),
        )
        row = con.execute(
            "SELECT id FROM giveaways WHERE message_id=? AND channel_id=?",
            (message_id, channel_id),
        ).fetchone()
    return row["id"]


def mark_won(message_id: int, channel_id: int) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE giveaways SET win_status='won', checked_at=? "
            "WHERE message_id=? AND channel_id=?",
            (int(time.time()), message_id, channel_id),
        )


def mark_lost(message_id: int, channel_id: int) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE giveaways SET win_status='lost', checked_at=? "
            "WHERE message_id=? AND channel_id=?",
            (int(time.time()), message_id, channel_id),
        )


def get_pending_giveaways() -> list[sqlite3.Row]:
    """Return all entries whose win_status is still 'unknown'."""
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM giveaways WHERE win_status='unknown'"
        ).fetchall()
    return rows


def get_giveaway(message_id: int, channel_id: int) -> Optional[sqlite3.Row]:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM giveaways WHERE message_id=? AND channel_id=?",
            (message_id, channel_id),
        ).fetchone()


# ── Channel filter helpers ─────────────────────────────────────────────────────

def is_blacklisted(channel_id: int) -> bool:
    with _conn() as con:
        row = con.execute(
            "SELECT id FROM channel_filters WHERE channel_id=? AND mode='blacklist'",
            (channel_id,),
        ).fetchone()
    return row is not None


def is_whitelisted(channel_id: int) -> bool:
    with _conn() as con:
        row = con.execute(
            "SELECT id FROM channel_filters WHERE channel_id=? AND mode='whitelist'",
            (channel_id,),
        ).fetchone()
    return row is not None


# ── Rate-limit helpers ─────────────────────────────────────────────────────────

def log_join() -> None:
    """Record a channel-join event for rate-limit accounting."""
    with _conn() as con:
        con.execute(
            "INSERT INTO join_rate_log (joined_at) VALUES (?)",
            (int(time.time()),),
        )


def joins_in_last_hour() -> int:
    """Return the count of channel joins recorded in the past 3600 seconds."""
    cutoff = int(time.time()) - 3600
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) AS cnt FROM join_rate_log WHERE joined_at > ?",
            (cutoff,),
        ).fetchone()
    return row["cnt"]
