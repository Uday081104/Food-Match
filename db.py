"""
db.py — SQLite database setup and helper functions for the Surplus Food Matcher.

Tables:
    donors  - restaurants/households who donate surplus food
    ngos    - NGOs/shelters/homes who receive food
    offers  - a specific surplus-food offer posted by a donor
    claims  - which NGO claimed which offer (first-come-first-served)
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime

DB_PATH = "foodmatch.db"


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS donors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT UNIQUE NOT NULL,
                pincode TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS ngos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT UNIQUE NOT NULL,
                pincode TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                capacity_notes TEXT,
                is_available INTEGER DEFAULT 1,  -- toggled on/off by NGO via WhatsApp
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                donor_id INTEGER NOT NULL REFERENCES donors(id),
                category TEXT NOT NULL,         -- Food / Clothes / Books / Electronics / Other
                description TEXT NOT NULL,      -- free text, e.g. "20 veg meals" or "3 bags kids clothes"
                photo_path TEXT,                -- local path to downloaded photo, if any
                photo_media_id TEXT,            -- WhatsApp media ID (for re-sending to NGOs)
                prepared_at TEXT NOT NULL,      -- ISO timestamp, when the offer was posted
                pickup_by TEXT NOT NULL,        -- ISO timestamp deadline
                pickup_location TEXT NOT NULL,
                status TEXT DEFAULT 'open',     -- open / claimed / expired / cancelled
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id INTEGER NOT NULL REFERENCES offers(id),
                ngo_id INTEGER NOT NULL REFERENCES ngos(id),
                claimed_at TEXT DEFAULT (datetime('now')),
                picked_up INTEGER DEFAULT 0,     -- confirmed pickup happened
                no_show INTEGER DEFAULT 0        -- NGO claimed but never came
            );

            CREATE TABLE IF NOT EXISTS message_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                direction TEXT NOT NULL,   -- inbound / outbound
                phone TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            """
        )


def log_message(direction, phone, body):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO message_log (direction, phone, body) VALUES (?, ?, ?)",
            (direction, phone, body),
        )


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
