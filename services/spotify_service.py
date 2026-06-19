"""Spotify link resolver (no API credentials required).

Spotify audio is DRM-protected, so it can't be streamed directly. Instead we read
the track / album / playlist metadata from Spotify's public *embed* page — its
`__NEXT_DATA__` JSON carries the title, artist and duration with no auth — and
resolve each track to a YouTube match lazily at playback time (see
youtube_service.get_stream_url), so importing a big playlist stays fast.
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.request
from typing import Optional

from config import MAX_PLAYLIST_SONGS
from models.song import Song
from utils.logger import logger

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
# open.spotify.com/[intl-xx/][embed/]track|album|playlist/<id>  or  spotify:track:<id>
_SPOTIFY_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z]+/)?(?:embed/)?|spotify:)"
    r"(track|album|playlist)[:/]([A-Za-z0-9]+)"
)
_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>', re.S)
_TIMEOUT = 20.0


def is_spotify_url(text: str) -> bool:
    return "open.spotify.com/" in text or text.startswith("spotify:")


def _parse(url: str) -> tuple[Optional[str], Optional[str]]:
    m = _SPOTIFY_RE.search(url)
    return (m.group(1), m.group(2)) if m else (None, None)


def _http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


def _mk_title(track: Optional[str], artist: Optional[str]) -> str:
    track = (track or "").strip()
    artist = (artist or "").strip()
    return f"{artist} - {track}" if artist else (track or "Unknown")


def _resolve_blocking(url: str, requester: str) -> list[Song]:
    typ, sid = _parse(url)
    if not typ:
        return []
    html = _http_get(f"https://open.spotify.com/embed/{typ}/{sid}")
    m = _NEXT_RE.search(html)
    if not m:
        raise RuntimeError("no __NEXT_DATA__ in Spotify embed page")
    entity = (
        json.loads(m.group(1))
        .get("props", {}).get("pageProps", {}).get("state", {})
        .get("data", {}).get("entity", {})
    ) or {}

    songs: list[Song] = []
    track_list = entity.get("trackList") or []
    if track_list:  # album / playlist
        for t in track_list[:MAX_PLAYLIST_SONGS]:
            songs.append(Song(
                title=_mk_title(t.get("title"), t.get("subtitle")),
                url=url,
                duration=int((t.get("duration") or 0) // 1000),
                requester=requester,
                source="spotify",
            ))
    else:  # single track
        artist = ", ".join(a.get("name", "") for a in (entity.get("artists") or [])[:2])
        songs.append(Song(
            title=_mk_title(entity.get("title") or entity.get("name"), artist),
            url=url,
            duration=int((entity.get("duration") or 0) // 1000),
            requester=requester,
            source="spotify",
        ))
    return songs


async def resolve_spotify(url: str, requester: str) -> list[Song]:
    """Resolve a Spotify track/album/playlist URL to a list of Songs (metadata
    only; each is matched to YouTube lazily at playback)."""
    loop = asyncio.get_running_loop()

    def _run() -> list[Song]:
        try:
            return _resolve_blocking(url, requester)
        except Exception as exc:
            logger.error("Spotify resolve failed for %s: %s", url, exc)
            return []

    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _run), timeout=_TIMEOUT + 10)
    except asyncio.TimeoutError:
        logger.error("Spotify resolve timed out: %s", url)
        return []
