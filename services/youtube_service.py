import asyncio
from typing import Optional

import yt_dlp

from config import COOKIES_FILE, MAX_PLAYLIST_SONGS
from models.song import Song
from utils.logger import logger

FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_BASE_OPTIONS = "-vn"

# yt-dlp hangs indefinitely if network calls stall.
# This timeout (seconds) is applied to every run_in_executor call so Discord
# always gets a response, even when YouTube is slow or blocking.
_YDL_TIMEOUT = 30.0

# Each extraction spawns a Node.js process to solve YouTube's n-challenge (~8s,
# CPU-bound). On this single-core host, 3+ concurrent solves thrash CPU/memory and
# every call hits the 30s timeout; 2 concurrent finish in ~14s. Cap at 2 so the
# realistic load (current song + 1 prefetch) always succeeds, and any extra guilds'
# requests queue behind it instead of failing en masse.
_EXTRACT_SEM = asyncio.Semaphore(2)

_YDL_COMMON: dict = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",
    "retries": 3,
    "extractor_retries": 3,
    "socket_timeout": 15,
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    },
    # player_client="tv" yields audio-only DASH (itag 251 opus); "web" falls back to
    # the combined itag-18 stream, whose CDN URL returns 403 on datacenter IPs and
    # makes FFmpeg exit instantly — the "song instantly finished → rating" bug.
    "extractor_args": {
        "youtube": {
            "player_client": ["tv"],
        }
    },
    # js_runtimes must be a dict {runtime_name: config_dict}; list format throws ValueError
    "js_runtimes": {"node": {}},
}


_YDL_SINGLE = {
    **_YDL_COMMON,
    "noplaylist": True,
    "default_search": "ytsearch1",
}

_YDL_MULTI = {
    **_YDL_COMMON,
    "noplaylist": True,
    "extract_flat": True,
    # NOTE: do NOT set default_search here — extract_flat + default_search skips searching
    # and returns the query string as a URL stub. Pass "ytsearchN:QUERY" directly instead.
}

_YDL_STREAM = {
    **_YDL_COMMON,
    "noplaylist": True,
}

_YDL_PLAYLIST = {
    **_YDL_COMMON,
    "noplaylist": False,
    "extract_flat": "in_playlist",
    "playlistend": MAX_PLAYLIST_SONGS,
}


def _build_opts(base: dict) -> dict:
    opts = dict(base)
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
    return opts


def _thumbnail_from_id(video_id: str) -> str:
    return f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg" if video_id else ""


def _make_song(info: dict, requester: str) -> Song:
    video_id = info.get("id") or ""
    thumbnail = info.get("thumbnail") or _thumbnail_from_id(video_id)
    return Song(
        title=info.get("title") or "Unknown",
        url=info.get("webpage_url") or info.get("url") or "",
        duration=int(info.get("duration") or 0),
        requester=requester,
        thumbnail=thumbnail,
    )


async def search_song(query: str, requester: str) -> Optional[Song]:
    """Resolve a YouTube URL or search keyword to a single Song."""
    loop = asyncio.get_running_loop()

    def _run() -> Optional[Song]:
        with yt_dlp.YoutubeDL(_build_opts(_YDL_SINGLE)) as ydl:
            try:
                info = ydl.extract_info(query, download=False)
            except yt_dlp.utils.DownloadError as exc:
                err = str(exc)
                if "429" in err or "Too Many Requests" in err:
                    logger.error("YouTube 限流 (429)：請設定 COOKIES_FILE。%s", exc)
                elif "Sign in" in err or "age" in err.lower():
                    logger.error("影片需要登入/年齡驗證：請設定 COOKIES_FILE。%s", exc)
                elif "unavailable" in err.lower() or "not available" in err.lower():
                    logger.error("影片無法在此區域播放：%s", exc)
                else:
                    logger.error("yt-dlp 搜尋失敗：%s", exc)
                return None
            if info is None:
                return None
            if "entries" in info:
                entries = [e for e in info["entries"] if e]
                if not entries:
                    return None
                info = entries[0]
            return _make_song(info, requester)

    try:
        async with _EXTRACT_SEM:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _run), timeout=_YDL_TIMEOUT
            )
    except asyncio.TimeoutError:
        logger.error("search_song timed out after %ss for query: %s", _YDL_TIMEOUT, query)
        return None


async def search_multiple(query: str, requester: str, count: int = 5) -> list[Song]:
    """Return up to *count* search results for a keyword (no URL)."""
    loop = asyncio.get_running_loop()

    def _run() -> list[Song]:
        opts = _build_opts(_YDL_MULTI)
        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                info = ydl.extract_info(f"ytsearch{count}:{query}", download=False)
            except yt_dlp.utils.DownloadError as exc:
                err = str(exc)
                if "429" in err or "Too Many Requests" in err:
                    logger.error("YouTube 限流 (429)：請設定 COOKIES_FILE。%s", exc)
                else:
                    logger.error("yt-dlp 多項搜尋失敗：%s", exc)
                return []
            if not info or "entries" not in info:
                return []
            songs: list[Song] = []
            for entry in info["entries"]:
                if not entry:
                    continue
                video_id = entry.get("id") or ""
                if not video_id:
                    continue
                songs.append(
                    Song(
                        title=entry.get("title") or "Unknown",
                        url=f"https://www.youtube.com/watch?v={video_id}",
                        duration=int(entry.get("duration") or 0),
                        requester=requester,
                        thumbnail=_thumbnail_from_id(video_id),
                    )
                )
            return songs

    try:
        async with _EXTRACT_SEM:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _run), timeout=_YDL_TIMEOUT
            )
    except asyncio.TimeoutError:
        logger.error("search_multiple timed out after %ss for query: %s", _YDL_TIMEOUT, query)
        return []


async def get_playlist_songs(url: str, requester: str) -> list[Song]:
    """Extract all songs from a YouTube playlist URL."""
    loop = asyncio.get_running_loop()

    def _run() -> list[Song]:
        with yt_dlp.YoutubeDL(_build_opts(_YDL_PLAYLIST)) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except yt_dlp.utils.DownloadError as exc:
                logger.error("Playlist extraction failed: %s", exc)
                return []
            if info is None:
                return []
            songs: list[Song] = []
            for entry in (info.get("entries") or []):
                if not entry:
                    continue
                video_id = entry.get("id") or ""
                video_url = (
                    entry.get("webpage_url")
                    or (f"https://www.youtube.com/watch?v={video_id}" if video_id else None)
                )
                if not video_url:
                    continue
                songs.append(
                    Song(
                        title=entry.get("title") or "Unknown",
                        url=video_url,
                        duration=int(entry.get("duration") or 0),
                        requester=requester,
                        thumbnail=_thumbnail_from_id(video_id),
                    )
                )
            return songs

    try:
        async with _EXTRACT_SEM:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _run), timeout=60.0  # playlists take longer
            )
    except asyncio.TimeoutError:
        logger.error("get_playlist_songs timed out for: %s", url)
        return []


async def get_stream_url(song: Song) -> Optional[str]:
    """Fetch a fresh direct audio stream URL just before playback."""
    if song.source == "bilibili":
        from services.bilibili_service import get_bilibili_stream
        return await get_bilibili_stream(song)

    loop = asyncio.get_running_loop()

    def _run() -> Optional[str]:
        with yt_dlp.YoutubeDL(_build_opts(_YDL_STREAM)) as ydl:
            try:
                info = ydl.extract_info(song.url, download=False)
            except yt_dlp.utils.DownloadError as exc:
                err = str(exc)
                if "429" in err or "Too Many Requests" in err:
                    logger.error("串流取得被限流 (429)，請設定 COOKIES_FILE：%s", exc)
                else:
                    logger.error("串流 URL 取得失敗 '%s'：%s", song.title, exc)
                return None
            if info is None:
                return None
            if "entries" in info:
                entries = [e for e in info["entries"] if e]
                if not entries:
                    return None
                info = entries[0]
            return info.get("url")

    try:
        async with _EXTRACT_SEM:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _run), timeout=_YDL_TIMEOUT
            )
    except asyncio.TimeoutError:
        logger.error("get_stream_url timed out after %ss for: %s", _YDL_TIMEOUT, song.title)
        return None


def is_url(text: str) -> bool:
    return text.startswith(("http://", "https://"))


def is_playlist_url(url: str) -> bool:
    return "playlist?list=" in url or "&list=" in url
