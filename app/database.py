"""
SQLite layer: deduplication tracking, processing logs, entry state.
"""
import json
import hashlib
import aiosqlite
import os
from datetime import datetime
from typing import Optional

from app.config import get_settings

_settings = get_settings()
DB_PATH = _settings.sqlite_db_path


async def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS captures (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT    UNIQUE,
                source_type TEXT,
                channel     TEXT,
                captured_at TEXT,
                capture_type TEXT,
                title       TEXT,
                company     TEXT,
                sheet_tab   TEXT,
                sheets_row  INTEGER,
                status      TEXT    DEFAULT 'ok',
                raw_input   TEXT,
                raw_ai_json TEXT,
                created_at  TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS processing_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                capture_id  INTEGER,
                event       TEXT,
                detail      TEXT,
                created_at  TEXT    DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_captures_fingerprint ON captures(fingerprint);
            CREATE INDEX IF NOT EXISTS idx_captures_channel      ON captures(channel);
        """)
        await db.commit()


def _make_fingerprint(source_type: str, raw_input: str,
                      title: Optional[str] = None,
                      company: Optional[str] = None) -> str:
    """
    Build a stable dedup fingerprint.
    For URLs → hash the normalised URL.
    For job postings → hash URL + title + company (lower-cased).
    For text/image → hash the first 400 chars of raw content.
    """
    parts = [source_type.lower()]
    if source_type == "url":
        # strip trailing slashes and query noise
        url = raw_input.strip().rstrip("/").split("?")[0].lower()
        parts.append(url)
    if title:
        parts.append(title.lower().strip())
    if company:
        parts.append(company.lower().strip())
    if source_type in ("text", "image") and raw_input:
        parts.append(raw_input[:400])

    fingerprint_str = "|".join(parts)
    return hashlib.sha256(fingerprint_str.encode()).hexdigest()


async def check_duplicate(source_type: str, raw_input: str,
                           title: Optional[str] = None,
                           company: Optional[str] = None) -> Optional[dict]:
    """Returns existing capture dict if duplicate, else None."""
    if not _settings.dedup_enabled:
        return None
    fp = _make_fingerprint(source_type, raw_input, title, company)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM captures WHERE fingerprint = ?", (fp,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def save_capture(
    source_type: str,
    channel: str,
    capture_type: str,
    title: Optional[str],
    company: Optional[str],
    sheet_tab: str,
    sheets_row: Optional[int],
    raw_input: str,
    raw_ai_json: str,
    status: str = "ok",
) -> int:
    fp = _make_fingerprint(source_type, raw_input, title, company)
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT OR REPLACE INTO captures
               (fingerprint, source_type, channel, captured_at, capture_type,
                title, company, sheet_tab, sheets_row, status, raw_input, raw_ai_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fp, source_type, channel, now, capture_type,
             title, company, sheet_tab, sheets_row, status,
             raw_input[:2000], raw_ai_json),
        )
        await db.commit()
        return cur.lastrowid


async def log_event(capture_id: int, event: str, detail: str = "") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO processing_log (capture_id, event, detail) VALUES (?,?,?)",
            (capture_id, event, detail[:1000]),
        )
        await db.commit()


async def get_recent_captures(channel: Optional[str] = None, limit: int = 5) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if channel:
            cur = await db.execute(
                "SELECT * FROM captures WHERE channel=? ORDER BY created_at DESC LIMIT ?",
                (channel, limit),
            )
        else:
            cur = await db.execute(
                "SELECT * FROM captures ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
