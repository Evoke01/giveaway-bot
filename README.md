# Telegram Giveaway Auto-Entry Bot

A 24/7 Telethon **userbot** (MTProto, not Bot API) that watches all your
joined Telegram chats for giveaway posts, auto-joins required channels,
clicks the entry button, logs everything to Saved Messages, and tracks
whether you won.

---

## ⚠️ Important Notices

1. **ToS Risk** — Userbot automation is against Telegram's Terms of Service
   for personal accounts.  The pacing delays and rate caps in this bot reduce
   (but do not eliminate) the risk of a temporary account restriction.
2. **Session Security** — The `.session` file grants full access to your
   Telegram account.  Back it up securely and never share it.
3. **Scope Limit** — This bot intentionally stops at Telegram-native actions
   (join / click / log).  Do **not** extend it to wallet-connect or
   transaction-signing flows.

---

## Project Structure

```
giveaway-bot/
├── main.py              # entrypoint — event loop + handler registration
├── config.py            # .env loader + all tunable constants
├── db.py                # SQLite schema + CRUD helpers
├── detector.py          # fast keyword/button/blacklist first-pass filter
├── parser.py            # structured GiveawayInfo extraction
├── actions.py           # join_channels() + click_enter_button()
├── logger.py            # Saved Messages forwarding + DB persistence
├── win_checker.py       # three-strategy result detection
├── requirements.txt
├── .env.example         # template — copy to .env and fill in
├── .gitignore
└── giveaway-bot.service # systemd unit for Oracle Cloud Free Tier
```

---

## Quick Start

### 1. Get Telegram API credentials

Visit <https://my.telegram.org/apps> and create an application to obtain
`API_ID` and `API_HASH`.

### 2. Clone and install

```bash
git clone <your-repo-url> giveaway-bot
cd giveaway-bot
python3.11 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env — fill in API_ID, API_HASH, PHONE
```

### 4. First run (interactive auth)

```bash
python main.py
# Telegram will send a code to your phone/app; enter it when prompted.
# If 2FA is enabled you will also be asked for your cloud password.
# A .session file is created — keep it safe.
```

### 5. Subsequent runs

The session file persists authentication; no phone code required again.

---

## Deployment on Oracle Cloud Free Tier (systemd)

```bash
# Copy unit file
sudo cp giveaway-bot.service /etc/systemd/system/

# Edit paths if needed, then:
sudo systemctl daemon-reload
sudo systemctl enable giveaway-bot
sudo systemctl start giveaway-bot

# Live logs
journalctl -u giveaway-bot -f
```

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `API_ID` | — | From my.telegram.org |
| `API_HASH` | — | From my.telegram.org |
| `PHONE` | — | Your phone number with country code |
| `SESSION_NAME` | `giveaway_bot` | Telethon session file basename |
| `MAX_JOINS_PER_HOUR` | `15` | Hard cap on channel joins per hour |
| `JOIN_DELAY_MIN` | `1.0` | Min seconds between joins |
| `JOIN_DELAY_MAX` | `5.0` | Max seconds between joins |
| `CLICK_DELAY_MIN` | `2.0` | Min seconds before button click |
| `CLICK_DELAY_MAX` | `6.0` | Max seconds before button click |
| `WIN_CHECK_GRACE_MINUTES` | `60` | Minutes after deadline to recheck |
| `DB_PATH` | `giveaways.db` | SQLite file path |
| `LOG_LEVEL` | `INFO` | Python logging level |

---

## Database Schema

| Table | Purpose |
|---|---|
| `giveaways` | One row per tracked giveaway post |
| `channel_filters` | Whitelist / blacklist for source channels |
| `join_rate_log` | Timestamped join events for hourly cap |

---

## Win Detection

Three complementary strategies run concurrently:

| Strategy | Mechanism |
|---|---|
| Edit listener | Watches `MessageEdited` on tracked posts for username mentions |
| Deadline recheck | Per-entry `asyncio` task wakes after deadline + grace period |
| DM listener | Scans incoming private messages for win-notification patterns |

---

## Extending the Keyword List

Edit `GIVEAWAY_KEYWORDS` in `config.py` — no logic changes needed:

```python
GIVEAWAY_KEYWORDS = [
    "giveaway",
    "your new keyword here",
    ...
]
```

---

## Channel Filters

Insert rows directly into SQLite to block or pin channels:

```sql
-- Blacklist a scam channel
INSERT INTO channel_filters (channel_id, mode, reason)
VALUES (-1001234567890, 'blacklist', 'known scam template');

-- Whitelist only specific channels (optional hardening)
INSERT INTO channel_filters (channel_id, mode)
VALUES (-1009876543210, 'whitelist');
```
