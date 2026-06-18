"""Genius API lyrics lookup via lyricsgenius.

lyricsgenius searches the Genius API, then scrapes the song page for the
actual lyrics text (the API endpoint does not return lyrics directly).
All I/O is run in a thread executor so it never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional

import lyricsgenius

from config import GENIUS_API_KEY
from utils.logger import logger

_genius: Optional[lyricsgenius.Genius] = None


def _get_genius() -> Optional[lyricsgenius.Genius]:
    global _genius
    if not GENIUS_API_KEY:
        return None
    if _genius is None:
        _genius = lyricsgenius.Genius(
            GENIUS_API_KEY,
            quiet=True,
            skip_non_songs=True,
            remove_section_headers=False,
            timeout=15,
            retries=1,
        )
    return _genius


def _clean_title(title: str) -> str:
    """Strip YouTube-style suffixes that confuse Genius search."""
    patterns = [
        r"\[.*?\]",
        r"\(.*?official.*?\)",
        r"\(.*?lyrics?.*?\)",
        r"\(.*?audio.*?\)",
        r"\(.*?video.*?\)",
        r"\(.*?mv.*?\)",
        r"official\s*(mv|video|audio|lyrics?)",
        r"lyrics?\s*video",
        r"feat\..*",
        r"ft\..*",
    ]
    cleaned = title
    for p in patterns:
        cleaned = re.sub(p, "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(" -–—|")


def _clean_lyrics(lyrics: str) -> str:
    """Remove Genius embed footer noise."""
    lyrics = re.sub(r"\d*EmbedShare\s*URLCopyEmbedCopy.*$", "", lyrics, flags=re.DOTALL)
    lyrics = re.sub(r"EmbedShare.*$", "", lyrics, flags=re.DOTALL)
    return lyrics.strip()


async def fetch_lyrics(song_title: str) -> Optional[str]:
    """Return lyrics for *song_title*, or None on failure / no result."""
    genius = _get_genius()
    if not genius:
        return None

    query = _clean_title(song_title)
    loop = asyncio.get_running_loop()

    def _run() -> Optional[str]:
        try:
            song = genius.search_song(query)
            if song and song.lyrics:
                return _clean_lyrics(song.lyrics)
        except Exception as exc:
            logger.error("Genius API error for '%s': %s", query, exc)
        return None

    return await loop.run_in_executor(None, _run)


def chunk_lyrics(lyrics: str, max_len: int = 3900) -> list[str]:
    """Split lyrics into Discord-safe chunks, breaking on blank lines."""
    if len(lyrics) <= max_len:
        return [lyrics]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lyrics.splitlines(keepends=True):
        if current_len + len(line) > max_len and current:
            chunks.append("".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line)

    if current:
        chunks.append("".join(current))

    return chunks
