import asyncio
import glob
import os
import tempfile
from typing import Optional

import yt_dlp

from config import COOKIES_FILE, MAX_PLAYLIST_SONGS, YT_PROXY
from models.song import Song
from utils.logger import logger

# Download cache (Option A): when streaming through a residential proxy (YT_PROXY),
# the extra network hop causes mid-song stutter. Instead, download the audio to
# disk first and play from the local file — no network during playback.
_DL_DIR = os.path.join(tempfile.gettempdir(), "discord-music-bot-cache")
os.makedirs(_DL_DIR, exist_ok=True)
# Full download over the tunnel takes longer than a metadata fetch.
_DL_TIMEOUT = 120.0

# -hide_banner/-loglevel error silence FFmpeg's banner and the benign
# "Will reconnect / Input-output error" chatter it prints when playback is
# stopped or skipped mid-stream (we detect real failures in Python instead).
FFMPEG_BEFORE_OPTIONS = (
    "-hide_banner -loglevel error "
    "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
)
# Live/HLS streams (Twitch, YouTube live, radio) hit transient EOF as their
# segment window rolls; -reconnect_at_eof makes FFmpeg reconnect instead of
# silently stalling (green ring on, no audio). Must NOT be used for normal
# songs or they'd reconnect at the end instead of finishing.
FFMPEG_LIVE_BEFORE_OPTIONS = (
    "-hide_banner -loglevel error "
    "-reconnect 1 -reconnect_streamed 1 -reconnect_at_eof 1 -reconnect_delay_max 5"
)
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
    if YT_PROXY:
        opts["proxy"] = YT_PROXY
    return opts


def _live_opts(base: dict) -> dict:
    """Override the player_client to 'web' — YouTube live streams have no formats
    under the 'tv' client we use for normal videos."""
    opts = _build_opts(base)
    opts["extractor_args"] = {"youtube": {"player_client": ["web"]}}
    return opts


_RADIO_EXTS = (".mp3", ".aac", ".ogg", ".opus", ".m3u8", ".m3u", ".pls", ".flac")


def is_twitch_url(text: str) -> bool:
    return "twitch.tv/" in text


def is_radio_url(text: str) -> bool:
    """A direct audio/stream URL (internet radio) rather than a known platform."""
    low = text.split("?")[0].lower()
    return text.startswith(("http://", "https://")) and low.endswith(_RADIO_EXTS)


def _is_youtube(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url


def make_radio_song(url: str, requester: str) -> Song:
    """Wrap a direct internet-radio stream URL as a live Song."""
    from urllib.parse import urlparse
    host = urlparse(url).netloc or "網路電台"
    return Song(
        title=f"📻 {host}",
        url=url,
        duration=0,
        requester=requester,
        source="radio",
        is_live=True,
    )


def _thumbnail_from_id(video_id: str) -> str:
    return f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg" if video_id else ""


def _make_song(info: dict, requester: str, source: str = "youtube") -> Song:
    video_id = info.get("id") or ""
    thumbnail = info.get("thumbnail") or (
        _thumbnail_from_id(video_id) if source == "youtube" else ""
    )
    return Song(
        title=info.get("title") or "Unknown",
        url=info.get("webpage_url") or info.get("url") or "",
        duration=int(info.get("duration") or 0),
        requester=requester,
        thumbnail=thumbnail,
        source=source,
        is_live=bool(info.get("is_live")),
    )


async def search_song(query: str, requester: str, source: str = "youtube") -> Optional[Song]:
    """Resolve a YouTube/SoundCloud URL or search keyword to a single Song."""
    loop = asyncio.get_running_loop()

    def _extract(opts: dict) -> Optional[dict]:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
        if info is None:
            return None
        if "entries" in info:
            entries = [e for e in info["entries"] if e]
            if not entries:
                return None
            info = entries[0]
        return info

    def _run() -> Optional[Song]:
        try:
            info = _extract(_build_opts(_YDL_SINGLE))
        except yt_dlp.utils.DownloadError as exc:
            err = str(exc)
            # YouTube live: the 'tv' client reports no formats — retry with 'web'.
            if _is_youtube(query) and ("No video formats" in err or "not available" in err.lower()):
                try:
                    info = _extract(_live_opts(_YDL_SINGLE))
                except Exception as exc2:
                    logger.error("YouTube live 解析失敗：%s", exc2)
                    return None
            elif "429" in err or "Too Many Requests" in err:
                logger.error("YouTube 限流 (429)：請設定 COOKIES_FILE。%s", exc)
                return None
            elif "Sign in" in err or "age" in err.lower():
                logger.error("影片需要登入/年齡驗證：請設定 COOKIES_FILE。%s", exc)
                return None
            else:
                logger.error("yt-dlp 搜尋失敗：%s", exc)
                return None
        if info is None:
            return None
        return _make_song(info, requester, source)

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


async def youtube_stream_from_query(query: str) -> Optional[str]:
    """Search YouTube for *query* and return the top result's stream URL.
    Used to resolve Spotify tracks (DRM) to a playable YouTube match at play time."""
    loop = asyncio.get_running_loop()

    def _run() -> Optional[str]:
        with yt_dlp.YoutubeDL(_build_opts(_YDL_STREAM)) as ydl:
            try:
                info = ydl.extract_info(f"ytsearch1:{query}", download=False)
            except yt_dlp.utils.DownloadError as exc:
                logger.error("YouTube 比對失敗 '%s'：%s", query, exc)
                return None
            if not info:
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
        logger.error("youtube_stream_from_query timed out for: %s", query)
        return None


async def get_stream_url(song: Song) -> Optional[str]:
    """Fetch a fresh direct audio stream URL just before playback."""
    if song.source == "bilibili":
        from services.bilibili_service import get_bilibili_stream
        return await get_bilibili_stream(song)
    if song.source == "spotify":
        # DRM — resolve to a YouTube match (title is "artist - track")
        return await youtube_stream_from_query(song.title)
    if song.source == "radio":
        # direct internet-radio stream — FFmpeg reads the URL as-is
        return song.url

    loop = asyncio.get_running_loop()
    is_yt = _is_youtube(song.url)
    # YouTube live needs the 'web' client; everything else uses the default opts.
    stream_opts = _live_opts(_YDL_STREAM) if (song.is_live and is_yt) else _build_opts(_YDL_STREAM)

    def _extract(opts: dict) -> Optional[dict]:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(song.url, download=False)
        if info is None:
            return None
        if "entries" in info:
            entries = [e for e in info["entries"] if e]
            if not entries:
                return None
            info = entries[0]
        return info

    def _run() -> Optional[str]:
        try:
            info = _extract(stream_opts)
        except yt_dlp.utils.DownloadError as exc:
            err = str(exc)
            # The 'tv' client returns no formats for live streams (and occasionally
            # other videos) — retry with the 'web' client before giving up.
            if is_yt and not (song.is_live) and "No video formats" in err:
                try:
                    info = _extract(_live_opts(_YDL_STREAM))
                except Exception as exc2:
                    logger.error("串流 URL 取得失敗 '%s'：%s", song.title, exc2)
                    return None
            elif "429" in err or "Too Many Requests" in err:
                logger.error("串流取得被限流 (429)，請設定 COOKIES_FILE：%s", exc)
                return None
            else:
                logger.error("串流 URL 取得失敗 '%s'：%s", song.title, exc)
                return None
        if info is None:
            return None
        return info.get("url")

    try:
        async with _EXTRACT_SEM:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _run), timeout=_YDL_TIMEOUT
            )
    except asyncio.TimeoutError:
        logger.error("get_stream_url timed out after %ss for: %s", _YDL_TIMEOUT, song.title)
        return None


# ── Download-to-disk (Option A: smooth playback through a proxy) ──────────

def _download_target(song: Song) -> str:
    """The yt-dlp input used to download *song*'s audio."""
    if song.source == "spotify":
        # Spotify is DRM — match it to a YouTube result by "artist - track".
        return f"ytsearch1:{song.title}"
    return song.url


def is_cached_file(path: Optional[str]) -> bool:
    """True if *path* is one of our downloaded cache files (not a stream URL)."""
    return bool(path) and os.path.isabs(path) and path.startswith(_DL_DIR)


def cleanup_download(path: Optional[str]) -> None:
    """Delete a downloaded cache file. No-op for stream URLs / missing files."""
    if is_cached_file(path) and os.path.exists(path):
        try:
            os.remove(path)
        except OSError as exc:
            logger.debug("Cache cleanup failed for %s: %s", path, exc)


def clear_download_cache() -> None:
    """Wipe the whole cache dir — call on startup to clear orphans from crashes."""
    for f in glob.glob(os.path.join(_DL_DIR, "*")):
        try:
            os.remove(f)
        except OSError:
            pass


async def _download_audio(song: Song) -> Optional[str]:
    """Download *song*'s audio to the cache dir and return the local file path."""
    loop = asyncio.get_running_loop()
    target = _download_target(song)
    opts = _build_opts(_YDL_STREAM)
    opts = {**opts, "outtmpl": os.path.join(_DL_DIR, "%(id)s.%(ext)s")}

    def _run() -> Optional[str]:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=True)
            if info is None:
                return None
            if "entries" in info:
                entries = [e for e in info["entries"] if e]
                if not entries:
                    return None
                info = entries[0]
            path = ydl.prepare_filename(info)
        if os.path.exists(path):
            return path
        # Fallback: extension may differ from the template — match by video id.
        matches = glob.glob(os.path.join(_DL_DIR, f"{info.get('id', '')}.*"))
        return matches[0] if matches else None

    try:
        async with _EXTRACT_SEM:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _run), timeout=_DL_TIMEOUT
            )
    except asyncio.TimeoutError:
        logger.error("Download timed out after %ss for: %s", _DL_TIMEOUT, song.title)
        return None
    except Exception as exc:
        logger.error("Download failed for '%s': %s", song.title, exc)
        return None


async def get_playable_source(song: Song) -> Optional[str]:
    """Return a playable source for *song*: a local file path (download mode —
    used when YT_PROXY is set and the song would otherwise stream through the
    proxy and stutter) or a direct stream URL (everything else).

    Falls back to streaming if the download fails."""
    if YT_PROXY and not song.is_live and song.source in ("youtube", "spotify"):
        path = await _download_audio(song)
        if path:
            logger.info("Downloaded '%s' for gapless local playback", song.title)
            return path
        logger.warning("Download failed for '%s' — falling back to streaming", song.title)
    return await get_stream_url(song)


def is_url(text: str) -> bool:
    return text.startswith(("http://", "https://"))


def is_playlist_url(url: str) -> bool:
    return "playlist?list=" in url or "&list=" in url


def is_soundcloud_url(text: str) -> bool:
    return "soundcloud.com/" in text


async def search_soundcloud(query: str, requester: str, count: int = 5) -> list[Song]:
    """Return up to *count* SoundCloud search results for a keyword."""
    loop = asyncio.get_running_loop()

    def _run() -> list[Song]:
        with yt_dlp.YoutubeDL(_build_opts(_YDL_MULTI)) as ydl:
            try:
                info = ydl.extract_info(f"scsearch{count}:{query}", download=False)
            except yt_dlp.utils.DownloadError as exc:
                logger.error("SoundCloud 搜尋失敗：%s", exc)
                return []
            if not info or "entries" not in info:
                return []
            songs: list[Song] = []
            for entry in info["entries"]:
                if not entry:
                    continue
                url = entry.get("url") or entry.get("webpage_url")
                if not url:
                    continue
                songs.append(
                    Song(
                        title=entry.get("title") or "Unknown",
                        url=url,
                        duration=int(entry.get("duration") or 0),
                        requester=requester,
                        thumbnail=entry.get("thumbnail") or "",
                        source="soundcloud",
                    )
                )
            return songs

    try:
        async with _EXTRACT_SEM:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _run), timeout=_YDL_TIMEOUT
            )
    except asyncio.TimeoutError:
        logger.error("search_soundcloud timed out for query: %s", query)
        return []
