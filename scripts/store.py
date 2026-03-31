"""
store.py — SQLite read/write operations for the digest database.
"""

import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "digest.db"


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS articles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT UNIQUE NOT NULL,
                title       TEXT NOT NULL,
                summary     TEXT,
                source_name TEXT,
                category    TEXT,
                published   TEXT,
                fetched_at  TEXT NOT NULL,
                score       REAL,
                included    INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS digest_runs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at       TEXT NOT NULL,
                articles_fetched  INTEGER DEFAULT 0,
                articles_kept     INTEGER DEFAULT 0,
                email_sent        INTEGER DEFAULT 0,
                dashboard_updated INTEGER DEFAULT 0,
                error_message     TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_articles_fetched ON articles(fetched_at);
            CREATE INDEX IF NOT EXISTS idx_articles_score   ON articles(score);
            CREATE INDEX IF NOT EXISTS idx_articles_included ON articles(included);
        """)
    logger.info("Database initialised at %s", db_path)


def upsert_article(
    conn: sqlite3.Connection,
    url: str,
    title: str,
    summary: Optional[str],
    source_name: str,
    category: str,
    published: Optional[str],
    score: Optional[float] = None,
    included: bool = False,
) -> bool:
    """Insert article if URL not seen before. Returns True if new."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            """
            INSERT INTO articles
                (url, title, summary, source_name, category, published, fetched_at, score, included)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                score    = excluded.score,
                included = excluded.included
            """,
            (url, title, summary, source_name, category, published, now,
             score, int(included)),
        )
        return conn.execute("SELECT changes()").fetchone()[0] > 0
    except sqlite3.Error as e:
        logger.error("upsert_article failed for %s: %s", url, e)
        return False


def mark_included(conn: sqlite3.Connection, urls: list[str]) -> None:
    conn.executemany(
        "UPDATE articles SET included = 1 WHERE url = ?",
        [(u,) for u in urls],
    )


def already_seen(conn: sqlite3.Connection, url: str) -> bool:
    row = conn.execute("SELECT 1 FROM articles WHERE url = ?", (url,)).fetchone()
    return row is not None


def get_articles_since(
    conn: sqlite3.Connection, since_iso: str, min_score: float = 0.0
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM articles
        WHERE fetched_at >= ?
          AND score >= ?
        ORDER BY score DESC
        """,
        (since_iso, min_score),
    ).fetchall()


def get_recent_articles_for_dashboard(
    conn: sqlite3.Connection, limit: int = 50
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM articles
        WHERE score IS NOT NULL
        ORDER BY fetched_at DESC, score DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def log_run(
    conn: sqlite3.Connection,
    articles_fetched: int,
    articles_kept: int,
    email_sent: bool,
    dashboard_updated: bool,
    error_message: Optional[str] = None,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO digest_runs
            (run_at, articles_fetched, articles_kept, email_sent,
             dashboard_updated, error_message)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (now, articles_fetched, articles_kept,
         int(email_sent), int(dashboard_updated), error_message),
    )
    return cur.lastrowid
