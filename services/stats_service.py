"""Persistent statistics, ratings, achievements, and channel config via SQLite.

All DB calls run in a thread executor so they never block the event loop.
WAL mode is enabled for better concurrent-read performance.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "stats.db"

_ACHIEVEMENT_MILESTONES = [10, 50, 100, 500, 1000]


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS play_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id   INTEGER NOT NULL,
            song_title TEXT    NOT NULL,
            song_url   TEXT    NOT NULL,
            requester  TEXT    NOT NULL,
            played_at  TEXT    DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ph_guild ON play_history (guild_id);

        CREATE TABLE IF NOT EXISTS ratings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id   INTEGER NOT NULL,
            song_url   TEXT    NOT NULL,
            song_title TEXT    NOT NULL,
            user_id    TEXT    NOT NULL,
            rating     INTEGER NOT NULL,
            rated_at   TEXT    DEFAULT (datetime('now')),
            UNIQUE(guild_id, song_url, user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_rt_guild ON ratings (guild_id, song_url);

        CREATE TABLE IF NOT EXISTS achievements (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    INTEGER NOT NULL,
            requester   TEXT    NOT NULL,
            milestone   INTEGER NOT NULL,
            achieved_at TEXT    DEFAULT (datetime('now')),
            UNIQUE(guild_id, requester, milestone)
        );

        CREATE TABLE IF NOT EXISTS playlists (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id   INTEGER NOT NULL,
            creator    TEXT    NOT NULL,
            name       TEXT    NOT NULL,
            songs_json TEXT    NOT NULL,
            created_at TEXT    DEFAULT (datetime('now')),
            UNIQUE(guild_id, name)
        );

        CREATE TABLE IF NOT EXISTS music_channels (
            guild_id   INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            message_id INTEGER
        );
    """)
    conn.commit()
    return conn


def _where(period: str) -> str:
    return {
        "week":  " AND played_at >= datetime('now', '-7 days')",
        "month": " AND played_at >= datetime('now', '-30 days')",
    }.get(period, "")


# ── Play history ──────────────────────────────────────────────────────

async def record_play(
    guild_id: int, song_title: str, song_url: str, requester: str
) -> None:
    loop = asyncio.get_running_loop()

    def _run() -> None:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO play_history (guild_id, song_title, song_url, requester) "
                "VALUES (?, ?, ?, ?)",
                (guild_id, song_title, song_url, requester),
            )

    await loop.run_in_executor(None, _run)


async def get_play_history(guild_id: int, limit: int = 20) -> list[dict]:
    """Return the most recent *limit* songs played in this guild."""
    loop = asyncio.get_running_loop()

    def _run() -> list[dict]:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT song_title, song_url, requester, played_at "
                "FROM play_history WHERE guild_id = ? "
                "ORDER BY played_at DESC LIMIT ?",
                (guild_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    return await loop.run_in_executor(None, _run)


async def get_top_songs(
    guild_id: int, period: str = "all", limit: int = 10
) -> list[dict]:
    loop = asyncio.get_running_loop()

    def _run() -> list[dict]:
        extra = _where(period)
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT song_title, song_url, COUNT(*) AS cnt "
                f"FROM play_history WHERE guild_id = ?{extra} "
                f"GROUP BY song_url ORDER BY cnt DESC LIMIT ?",
                (guild_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    return await loop.run_in_executor(None, _run)


async def get_top_requesters(
    guild_id: int, period: str = "all", limit: int = 10
) -> list[dict]:
    loop = asyncio.get_running_loop()

    def _run() -> list[dict]:
        extra = _where(period)
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT requester, COUNT(*) AS cnt "
                f"FROM play_history WHERE guild_id = ?{extra} "
                f"GROUP BY requester ORDER BY cnt DESC LIMIT ?",
                (guild_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    return await loop.run_in_executor(None, _run)


async def get_total_plays(guild_id: int, period: str = "all") -> int:
    loop = asyncio.get_running_loop()

    def _run() -> int:
        extra = _where(period)
        with _connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM play_history WHERE guild_id = ?{extra}",
                (guild_id,),
            ).fetchone()
        return row[0] if row else 0

    return await loop.run_in_executor(None, _run)


# ── Ratings ───────────────────────────────────────────────────────────

async def record_rating(
    guild_id: int, song_url: str, song_title: str, user_id: str, rating: int
) -> None:
    """Upsert a 1–5 rating for a song from a user."""
    loop = asyncio.get_running_loop()

    def _run() -> None:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO ratings (guild_id, song_url, song_title, user_id, rating) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(guild_id, song_url, user_id) DO UPDATE SET rating=excluded.rating, rated_at=datetime('now')",
                (guild_id, song_url, song_title, user_id, rating),
            )

    await loop.run_in_executor(None, _run)


async def get_song_rating(guild_id: int, song_url: str) -> dict:
    """Return avg rating and vote count for a song."""
    loop = asyncio.get_running_loop()

    def _run() -> dict:
        with _connect() as conn:
            row = conn.execute(
                "SELECT ROUND(AVG(rating), 1) AS avg_rating, COUNT(*) AS votes "
                "FROM ratings WHERE guild_id = ? AND song_url = ?",
                (guild_id, song_url),
            ).fetchone()
        return {"avg": row["avg_rating"] or 0, "votes": row["votes"] or 0}

    return await loop.run_in_executor(None, _run)


async def get_top_rated_songs(guild_id: int, limit: int = 10) -> list[dict]:
    loop = asyncio.get_running_loop()

    def _run() -> list[dict]:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT song_title, song_url, "
                "ROUND(AVG(rating), 1) AS avg_rating, COUNT(*) AS votes "
                "FROM ratings WHERE guild_id = ? "
                "GROUP BY song_url HAVING votes >= 1 "
                "ORDER BY avg_rating DESC, votes DESC LIMIT ?",
                (guild_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    return await loop.run_in_executor(None, _run)


# ── Achievements ──────────────────────────────────────────────────────

async def check_new_achievements(guild_id: int, requester: str) -> list[int]:
    """Return list of newly unlocked milestones for this requester."""
    loop = asyncio.get_running_loop()

    def _run() -> list[int]:
        with _connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM play_history WHERE guild_id = ? AND requester = ?",
                (guild_id, requester),
            ).fetchone()[0]

            existing = {
                r[0] for r in conn.execute(
                    "SELECT milestone FROM achievements WHERE guild_id = ? AND requester = ?",
                    (guild_id, requester),
                ).fetchall()
            }

            newly_unlocked = []
            for ms in _ACHIEVEMENT_MILESTONES:
                if total >= ms and ms not in existing:
                    conn.execute(
                        "INSERT OR IGNORE INTO achievements (guild_id, requester, milestone) "
                        "VALUES (?, ?, ?)",
                        (guild_id, requester, ms),
                    )
                    newly_unlocked.append(ms)
            conn.commit()
        return newly_unlocked

    return await loop.run_in_executor(None, _run)


# ── Playlists ─────────────────────────────────────────────────────────

async def save_playlist(
    guild_id: int, creator: str, name: str, songs_json: str
) -> bool:
    """Save or overwrite a named playlist. Returns True on success."""
    loop = asyncio.get_running_loop()

    def _run() -> bool:
        try:
            with _connect() as conn:
                conn.execute(
                    "INSERT INTO playlists (guild_id, creator, name, songs_json) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(guild_id, name) DO UPDATE SET "
                    "songs_json=excluded.songs_json, creator=excluded.creator, "
                    "created_at=datetime('now')",
                    (guild_id, creator, name, songs_json),
                )
            return True
        except Exception:
            return False

    return await loop.run_in_executor(None, _run)


async def load_playlist(guild_id: int, name: str) -> dict | None:
    loop = asyncio.get_running_loop()

    def _run() -> dict | None:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM playlists WHERE guild_id = ? AND name = ?",
                (guild_id, name),
            ).fetchone()
        return dict(row) if row else None

    return await loop.run_in_executor(None, _run)


async def list_playlists(guild_id: int) -> list[dict]:
    loop = asyncio.get_running_loop()

    def _run() -> list[dict]:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT name, creator, created_at, "
                "json_array_length(songs_json) AS song_count "
                "FROM playlists WHERE guild_id = ? ORDER BY name",
                (guild_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    return await loop.run_in_executor(None, _run)


async def delete_playlist(guild_id: int, name: str) -> bool:
    loop = asyncio.get_running_loop()

    def _run() -> bool:
        with _connect() as conn:
            cur = conn.execute(
                "DELETE FROM playlists WHERE guild_id = ? AND name = ?",
                (guild_id, name),
            )
        return cur.rowcount > 0

    return await loop.run_in_executor(None, _run)


# ── Music channel (D4) ────────────────────────────────────────────────

async def save_music_channel(
    guild_id: int, channel_id: int, message_id: int | None = None
) -> None:
    loop = asyncio.get_running_loop()

    def _run() -> None:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO music_channels (guild_id, channel_id, message_id) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(guild_id) DO UPDATE SET "
                "channel_id=excluded.channel_id, message_id=excluded.message_id",
                (guild_id, channel_id, message_id),
            )

    await loop.run_in_executor(None, _run)


async def update_music_channel_message(guild_id: int, message_id: int | None) -> None:
    loop = asyncio.get_running_loop()

    def _run() -> None:
        with _connect() as conn:
            conn.execute(
                "UPDATE music_channels SET message_id = ? WHERE guild_id = ?",
                (message_id, guild_id),
            )

    await loop.run_in_executor(None, _run)


async def get_music_channel(guild_id: int) -> dict | None:
    loop = asyncio.get_running_loop()

    def _run() -> dict | None:
        with _connect() as conn:
            row = conn.execute(
                "SELECT channel_id, message_id FROM music_channels WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
        return dict(row) if row else None

    return await loop.run_in_executor(None, _run)


async def clear_music_channel(guild_id: int) -> None:
    loop = asyncio.get_running_loop()

    def _run() -> None:
        with _connect() as conn:
            conn.execute("DELETE FROM music_channels WHERE guild_id = ?", (guild_id,))

    await loop.run_in_executor(None, _run)


# ── Personal stats (G2) ───────────────────────────────────────────────

async def get_my_stats(guild_id: int, requester: str) -> dict:
    """Return personal play stats for one user."""
    loop = asyncio.get_running_loop()

    def _run() -> dict:
        with _connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM play_history WHERE guild_id=? AND requester=?",
                (guild_id, requester),
            ).fetchone()[0]

            top_song = conn.execute(
                "SELECT song_title, COUNT(*) AS cnt FROM play_history "
                "WHERE guild_id=? AND requester=? "
                "GROUP BY song_url ORDER BY cnt DESC LIMIT 1",
                (guild_id, requester),
            ).fetchone()

            week_total = conn.execute(
                "SELECT COUNT(*) FROM play_history "
                "WHERE guild_id=? AND requester=? "
                "AND played_at >= datetime('now','-7 days')",
                (guild_id, requester),
            ).fetchone()[0]

            avg_rating_given = conn.execute(
                "SELECT ROUND(AVG(rating),1) FROM ratings "
                "WHERE guild_id=? AND user_id=?",
                (guild_id, requester),
            ).fetchone()[0]

            milestones = conn.execute(
                "SELECT milestone FROM achievements WHERE guild_id=? AND requester=? "
                "ORDER BY milestone",
                (guild_id, requester),
            ).fetchall()

        return {
            "total": total,
            "week": week_total,
            "top_song": dict(top_song) if top_song else None,
            "avg_rating_given": avg_rating_given or 0,
            "milestones": [r[0] for r in milestones],
        }

    return await loop.run_in_executor(None, _run)


# ── Year wrap (G3) ────────────────────────────────────────────────────

async def get_year_wrap(guild_id: int, year: int) -> dict:
    """Return aggregate stats for a given calendar year."""
    loop = asyncio.get_running_loop()

    def _run() -> dict:
        start = f"{year}-01-01"
        end = f"{year + 1}-01-01"

        with _connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM play_history "
                "WHERE guild_id=? AND played_at>=? AND played_at<?",
                (guild_id, start, end),
            ).fetchone()[0]

            top_songs = conn.execute(
                "SELECT song_title, COUNT(*) AS cnt FROM play_history "
                "WHERE guild_id=? AND played_at>=? AND played_at<? "
                "GROUP BY song_url ORDER BY cnt DESC LIMIT 5",
                (guild_id, start, end),
            ).fetchall()

            top_users = conn.execute(
                "SELECT requester, COUNT(*) AS cnt FROM play_history "
                "WHERE guild_id=? AND played_at>=? AND played_at<? "
                "GROUP BY requester ORDER BY cnt DESC LIMIT 5",
                (guild_id, start, end),
            ).fetchall()

            peak_day = conn.execute(
                "SELECT DATE(played_at) AS day, COUNT(*) AS cnt FROM play_history "
                "WHERE guild_id=? AND played_at>=? AND played_at<? "
                "GROUP BY day ORDER BY cnt DESC LIMIT 1",
                (guild_id, start, end),
            ).fetchone()

            unique_songs = conn.execute(
                "SELECT COUNT(DISTINCT song_url) FROM play_history "
                "WHERE guild_id=? AND played_at>=? AND played_at<?",
                (guild_id, start, end),
            ).fetchone()[0]

        return {
            "year": year,
            "total": total,
            "unique_songs": unique_songs,
            "top_songs": [dict(r) for r in top_songs],
            "top_users": [dict(r) for r in top_users],
            "peak_day": dict(peak_day) if peak_day else None,
        }

    return await loop.run_in_executor(None, _run)
