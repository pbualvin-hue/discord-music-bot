"""Bilibili audio resolver.

Bilibili serves the video *web page* (https://www.bilibili.com/video/BVxxx) with
HTTP 412 to datacenter IPs, so yt-dlp's page-scraping extractor fails. Its JSON
*API*, however, answers normally — so we resolve everything through the API:

    view API     → title / duration / cid
    playurl API  → DASH audio stream baseUrl  (fnval=16)

The bilivideo CDN requires a `Referer: https://www.bilibili.com/` header, which we
also hand to FFmpeg (see BILI_FFMPEG_HEADERS / bilibili_before_options).

Only a `buvid3` cookie is needed; we fetch a fresh one from the homepage and cache
it. No login required.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.request
from typing import Optional

from models.song import Song
from utils.logger import logger

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_REFERER = "https://www.bilibili.com/"

# Headers FFmpeg must send to the bilivideo CDN (newline-terminated for -headers).
BILI_FFMPEG_HEADERS = f"User-Agent: {_UA}\r\nReferer: {_REFERER}\r\n"

# bilibili.com/video/BVxxxx  or  b23.tv/xxxx short links
_BV_RE = re.compile(r"(?:bilibili\.com/video/|b23\.tv/)(BV[0-9A-Za-z]+)")
_TIMEOUT = 20.0

# Cached buvid3 cookie (valid ~2 years; refreshed hourly to be safe)
_buvid3: Optional[str] = None
_buvid3_at: float = 0.0
_BUVID_TTL = 3600.0


def is_bilibili_url(text: str) -> bool:
    return "bilibili.com/video/" in text or "b23.tv/" in text


def _extract_bvid(url: str) -> Optional[str]:
    # b23.tv share links are short codes (b23.tv/AbC12), not BV ids — follow the
    # redirect to the canonical /video/BVxxx URL first.
    if "b23.tv/" in url and not _BV_RE.search(url):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA}, method="HEAD")
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                url = resp.geturl()
        except Exception as exc:
            logger.error("Bilibili: failed to resolve short link %s: %s", url, exc)
    m = _BV_RE.search(url)
    return m.group(1) if m else None


def _http_get(url: str, *, referer: str = _REFERER, cookie: Optional[str] = None) -> str:
    headers = {"User-Agent": _UA, "Referer": referer}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _get_buvid3() -> str:
    """Fetch (and cache) a buvid3 cookie from the Bilibili homepage."""
    global _buvid3, _buvid3_at
    now = time.time()
    if _buvid3 and now - _buvid3_at < _BUVID_TTL:
        return _buvid3
    req = urllib.request.Request("https://www.bilibili.com", headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        # A response carries several Set-Cookie headers; .get() returns only the
        # first, so scan them all for buvid3.
        set_cookie = " ".join(resp.headers.get_all("Set-Cookie") or [])
    m = re.search(r"buvid3=([^;]+)", set_cookie)
    if not m:
        raise RuntimeError("could not obtain buvid3 cookie from bilibili.com")
    _buvid3 = m.group(1)
    _buvid3_at = now
    return _buvid3


def _resolve_blocking(bvid: str) -> tuple[str, int, str, str]:
    """Return (title, duration_seconds, thumbnail, audio_stream_url) for a BV id."""
    cookie = f"buvid3={_get_buvid3()}"

    view = json.loads(
        _http_get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", cookie=cookie)
    )
    if view.get("code") != 0:
        raise RuntimeError(f"view API code={view.get('code')} {view.get('message')}")
    data = view["data"]
    title = data.get("title") or "Unknown"
    duration = int(data.get("duration") or 0)
    thumbnail = data.get("pic") or ""
    cid = data["cid"]

    play = json.loads(
        _http_get(
            f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}"
            f"&fnval=16&fnver=0&fourk=1",
            cookie=cookie,
            referer=f"https://www.bilibili.com/video/{bvid}",
        )
    )
    if play.get("code") != 0:
        raise RuntimeError(f"playurl API code={play.get('code')} {play.get('message')}")
    audios = (play.get("data", {}).get("dash", {}) or {}).get("audio") or []
    if not audios:
        raise RuntimeError("no DASH audio streams in playurl response")
    best = max(audios, key=lambda a: a.get("bandwidth", 0))
    return title, duration, thumbnail, best["baseUrl"]


async def resolve_bilibili_song(url: str, requester: str) -> Optional[Song]:
    """Resolve a Bilibili URL to a Song (metadata only; stream fetched at playback)."""
    bvid = _extract_bvid(url)
    if not bvid:
        logger.error("Bilibili: no BV id in URL: %s", url)
        return None
    loop = asyncio.get_running_loop()

    def _run() -> Optional[Song]:
        try:
            title, duration, thumbnail, _ = _resolve_blocking(bvid)
        except Exception as exc:
            logger.error("Bilibili resolve failed for %s: %s", bvid, exc)
            return None
        return Song(
            title=title,
            url=f"https://www.bilibili.com/video/{bvid}",
            duration=duration,
            requester=requester,
            thumbnail=thumbnail,
            source="bilibili",
        )

    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _run), timeout=_TIMEOUT + 5)
    except asyncio.TimeoutError:
        logger.error("Bilibili resolve timed out for: %s", url)
        return None


async def get_bilibili_stream(song: Song) -> Optional[str]:
    """Fetch a fresh DASH audio stream URL for a Bilibili Song just before playback."""
    bvid = _extract_bvid(song.url)
    if not bvid:
        return None
    loop = asyncio.get_running_loop()

    def _run() -> Optional[str]:
        try:
            _, _, _, audio_url = _resolve_blocking(bvid)
            return audio_url
        except Exception as exc:
            logger.error("Bilibili stream fetch failed for %s: %s", bvid, exc)
            return None

    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _run), timeout=_TIMEOUT + 5)
    except asyncio.TimeoutError:
        logger.error("Bilibili stream fetch timed out for: %s", song.title)
        return None
