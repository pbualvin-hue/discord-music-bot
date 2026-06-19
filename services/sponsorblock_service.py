"""SponsorBlock segment fetcher.

Looks up community-marked segments (sponsors, intros/outros and — most useful for
a music bot — `music_offtopic`, the non-music parts of music videos) so playback
can seek past them. Public API, no key required: https://sponsor.ajay.app
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from utils.logger import logger

_API = "https://sponsor.ajay.app/api/skipSegments"
# Categories worth skipping in a music context (intro/outro talk, sponsors).
_CATEGORIES = ["music_offtopic", "sponsor", "intro", "outro", "selfpromo", "interaction"]
_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/live/|/shorts/)([0-9A-Za-z_-]{11})")
_TIMEOUT = 10.0


def youtube_video_id(url: str) -> str | None:
    m = _VIDEO_ID_RE.search(url)
    return m.group(1) if m else None


def _fetch_blocking(video_id: str) -> list[tuple[float, float]]:
    cats = urllib.parse.quote(json.dumps(_CATEGORIES))
    url = f"{_API}?videoID={video_id}&categories={cats}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []  # no segments for this video
        logger.debug("SponsorBlock HTTP %s for %s", exc.code, video_id)
        return []
    except Exception as exc:
        logger.debug("SponsorBlock fetch failed for %s: %s", video_id, exc)
        return []

    segments: list[tuple[float, float]] = []
    for seg in data:
        try:
            start, end = seg["segment"]
            if end - start >= 0.5:
                segments.append((float(start), float(end)))
        except (KeyError, ValueError, TypeError):
            continue
    segments.sort()
    return segments


async def get_sponsor_segments(url: str) -> list[tuple[float, float]]:
    """Return sorted [(start, end)] segments to skip for a YouTube URL ([] if none)."""
    video_id = youtube_video_id(url)
    if not video_id:
        return []
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _fetch_blocking, video_id), timeout=_TIMEOUT + 2
        )
    except asyncio.TimeoutError:
        return []
