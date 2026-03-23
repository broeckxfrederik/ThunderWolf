"""
db.py — SQLite persistence layer
─────────────────────────────────
All state that must survive bot restarts and redeployments lives here.

DB file: /app/data/thunderwolf.db  (mounted as a Docker volume so it
survives `docker compose up --build`).

Tables
──────
  guild_config      key/value config per guild (role & channel ID mappings)
  cars              managed car list per guild (with setup forum thread ID)
  events            scheduled race events with lineup and reminder flags
  welcome_channels  active welcome channels (so 12h kick survives restart)
  role_requests     pending role-change requests
"""

import json
import os
import sqlite3

_here   = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(_here, "data", "thunderwolf.db"))

_DDL = """
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id  INTEGER,
    key       TEXT,
    value     TEXT,
    PRIMARY KEY (guild_id, key)
);

CREATE TABLE IF NOT EXISTS cars (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    name            TEXT    NOT NULL,
    setup_thread_id INTEGER,
    UNIQUE (guild_id, name)
);

CREATE TABLE IF NOT EXISTS events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id            INTEGER NOT NULL,
    channel_id          INTEGER,
    name                TEXT    NOT NULL,
    date_utc            TEXT    NOT NULL,
    slots_json          TEXT    NOT NULL DEFAULT '[]',
    lineup_json         TEXT    NOT NULL DEFAULT '{}',
    confirmed           INTEGER NOT NULL DEFAULT 0,
    results_json        TEXT,
    results_at          TEXT,
    reminder_24h_sent   INTEGER NOT NULL DEFAULT 0,
    reminder_1h_sent    INTEGER NOT NULL DEFAULT 0,
    roles_cleaned       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS welcome_channels (
    channel_id  INTEGER PRIMARY KEY,
    guild_id    INTEGER NOT NULL,
    member_id   INTEGER NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS role_requests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id       INTEGER NOT NULL,
    member_id      INTEGER NOT NULL,
    requested_role TEXT    NOT NULL,
    message_id     INTEGER,
    channel_id     INTEGER,
    status         TEXT    NOT NULL DEFAULT 'pending'
);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db() -> None:
    """Create tables and data directory if they don't exist. Call once at startup."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _conn() as c:
        c.executescript(_DDL)


# ── guild_config ──────────────────────────────────────────────────────────────

def get_config(guild_id: int, key: str) -> str | None:
    with _conn() as c:
        row = c.execute(
            "SELECT value FROM guild_config WHERE guild_id=? AND key=?",
            (guild_id, key),
        ).fetchone()
    return row["value"] if row else None


def set_config(guild_id: int, key: str, value: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO guild_config (guild_id, key, value) VALUES (?,?,?)",
            (guild_id, key, value),
        )


def get_all_config(guild_id: int) -> dict[str, str]:
    with _conn() as c:
        rows = c.execute(
            "SELECT key, value FROM guild_config WHERE guild_id=?", (guild_id,)
        ).fetchall()
    return {r["key"]: r["value"] for r in rows}


# ── cars ──────────────────────────────────────────────────────────────────────

def add_car(guild_id: int, name: str) -> int:
    """Insert car (or ignore if duplicate). Returns the car's id."""
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO cars (guild_id, name) VALUES (?,?)",
            (guild_id, name),
        )
        row = c.execute(
            "SELECT id FROM cars WHERE guild_id=? AND name=?", (guild_id, name)
        ).fetchone()
    return row["id"]


def set_car_thread(car_id: int, thread_id: int) -> None:
    with _conn() as c:
        c.execute("UPDATE cars SET setup_thread_id=? WHERE id=?", (thread_id, car_id))


def remove_car(guild_id: int, name: str) -> bool:
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM cars WHERE guild_id=? AND name=?", (guild_id, name)
        )
    return cur.rowcount > 0


def list_cars(guild_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, name, setup_thread_id FROM cars WHERE guild_id=? ORDER BY name",
            (guild_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def search_cars(guild_id: int, query: str, limit: int = 10) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, name FROM cars WHERE guild_id=? AND name LIKE ? LIMIT ?",
            (guild_id, f"%{query}%", limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_car_by_name(guild_id: int, name: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT id, name, setup_thread_id FROM cars WHERE guild_id=? AND name=?",
            (guild_id, name),
        ).fetchone()
    return dict(row) if row else None


# ── events ────────────────────────────────────────────────────────────────────

def create_event(guild_id: int, name: str, date_utc: str, slots: list[dict]) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO events (guild_id, name, date_utc, slots_json) VALUES (?,?,?,?)",
            (guild_id, name, date_utc, json.dumps(slots)),
        )
    return cur.lastrowid


def set_event_channel(event_id: int, channel_id: int) -> None:
    with _conn() as c:
        c.execute("UPDATE events SET channel_id=? WHERE id=?", (channel_id, event_id))


def get_event(event_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["slots"]  = json.loads(d.pop("slots_json"))
    d["lineup"] = json.loads(d.pop("lineup_json"))
    d["results"] = json.loads(d["results_json"]) if d.get("results_json") else None
    return d


def get_active_events(guild_id: int) -> list[dict]:
    """Return confirmed=0 and past-deadline events that haven't finished."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM events WHERE guild_id=? ORDER BY date_utc",
            (guild_id,),
        ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["slots"]  = json.loads(d.pop("slots_json"))
        d["lineup"] = json.loads(d.pop("lineup_json"))
        d["results"] = json.loads(d["results_json"]) if d.get("results_json") else None
        out.append(d)
    return out


def update_lineup(event_id: int, lineup: dict) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE events SET lineup_json=? WHERE id=?",
            (json.dumps(lineup), event_id),
        )


def confirm_event(event_id: int) -> None:
    with _conn() as c:
        c.execute("UPDATE events SET confirmed=1 WHERE id=?", (event_id,))


def set_results(event_id: int, results: dict, results_at: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE events SET results_json=?, results_at=? WHERE id=?",
            (json.dumps(results), results_at, event_id),
        )


def mark_reminder(event_id: int, which: str) -> None:
    col = "reminder_24h_sent" if which == "24h" else "reminder_1h_sent"
    with _conn() as c:
        c.execute(f"UPDATE events SET {col}=1 WHERE id=?", (event_id,))


def mark_roles_cleaned(event_id: int) -> None:
    with _conn() as c:
        c.execute("UPDATE events SET roles_cleaned=1 WHERE id=?", (event_id,))


def get_events_due_cleanup(before_iso: str) -> list[dict]:
    """Return events whose results were posted before before_iso and haven't been cleaned yet."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM events WHERE results_at IS NOT NULL "
            "AND results_at <= ? AND roles_cleaned=0",
            (before_iso,),
        ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["slots"]  = json.loads(d.pop("slots_json"))
        d["lineup"] = json.loads(d.pop("lineup_json"))
        out.append(d)
    return out


# ── welcome_channels ──────────────────────────────────────────────────────────

def add_welcome(channel_id: int, guild_id: int, member_id: int, created_at: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO welcome_channels VALUES (?,?,?,?)",
            (channel_id, guild_id, member_id, created_at),
        )


def remove_welcome(channel_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM welcome_channels WHERE channel_id=?", (channel_id,))


def get_expired_welcomes(before_iso: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM welcome_channels WHERE created_at <= ?", (before_iso,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── role_requests ─────────────────────────────────────────────────────────────

def create_role_request(guild_id: int, member_id: int, requested_role: str) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO role_requests (guild_id, member_id, requested_role) VALUES (?,?,?)",
            (guild_id, member_id, requested_role),
        )
    return cur.lastrowid


def set_request_message(request_id: int, message_id: int, channel_id: int) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE role_requests SET message_id=?, channel_id=? WHERE id=?",
            (message_id, channel_id, request_id),
        )


def get_role_request(request_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM role_requests WHERE id=?", (request_id,)
        ).fetchone()
    return dict(row) if row else None


def update_request_status(request_id: int, status: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE role_requests SET status=? WHERE id=?", (status, request_id)
        )
