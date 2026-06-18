"""LRC synced-lyrics fetcher for KTV mode (L1).

Search order: syncedlyrics (LRCLib / Musixmatch) → None
Falls back gracefully when the library is not installed or no LRC found.
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional

from utils.logger import logger

_LRC_LINE_RE = re.compile(r"\[(\d{1,3}):(\d{2}(?:\.\d+)?)\](.*)")
_METADATA_RE = re.compile(r"\[(?:ti|ar|al|by|offset|length):")


def _parse_lrc(lrc_text: str) -> list[tuple[float, str]]:
    """Return sorted list of (timestamp_seconds, lyric_line)."""
    lines: list[tuple[float, str]] = []
    for line in lrc_text.splitlines():
        if _METADATA_RE.match(line):
            continue
        for m in _LRC_LINE_RE.finditer(line):
            minutes = int(m.group(1))
            seconds = float(m.group(2))
            text = m.group(3).strip()
            if text:
                lines.append((minutes * 60 + seconds, text))
    return sorted(lines, key=lambda x: x[0])


def get_current_line_idx(lines: list[tuple[float, str]], elapsed: float) -> int:
    """Binary-search for the last line whose timestamp ≤ elapsed."""
    idx = 0
    for i, (ts, _) in enumerate(lines):
        if ts <= elapsed:
            idx = i
        else:
            break
    return idx


def build_karaoke_window(
    lines: list[tuple[float, str]],
    current_idx: int,
    *,
    before: int = 2,
    after: int = 3,
) -> str:
    """Return a multi-line string showing a sliding window around current_idx."""
    start = max(0, current_idx - before)
    end = min(len(lines), current_idx + after + 1)
    parts: list[str] = []
    for i in range(start, end):
        _, text = lines[i]
        if i < current_idx:
            parts.append(f"~~{text}~~")        # past — strikethrough
        elif i == current_idx:
            parts.append(f"**▶ {text}**")      # current — bold
        else:
            parts.append(f"　　{text}")         # upcoming — indented
    return "\n".join(parts) if parts else "（正在載入歌詞…）"


async def fetch_synced_lyrics(query: str) -> Optional[list[tuple[float, str]]]:
    """Attempt to fetch LRC lyrics for *query*.  Returns None if unavailable."""
    loop = asyncio.get_running_loop()

    def _run() -> Optional[list[tuple[float, str]]]:
        try:
            import syncedlyrics  # optional dependency

            lrc = syncedlyrics.search(query, synced_only=True)
            if not lrc:
                return None
            parsed = _parse_lrc(lrc)
            return parsed if parsed else None
        except ImportError:
            logger.warning("syncedlyrics not installed — KTV mode unavailable.")
            return None
        except Exception as exc:
            logger.warning("syncedlyrics search failed for '%s': %s", query, exc)
            return None

    return await loop.run_in_executor(None, _run)
