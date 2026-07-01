from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

import discord

from config import AUTO_DISCONNECT_SECONDS, FFMPEG_PATH, MAX_QUEUE_SIZE, YT_PROXY
from models.guild_state import GuildState
from models.loop_mode import LoopMode
from models.song import Song
from services.filter_service import build_ffmpeg_options
from services.stats_service import record_play
from services.youtube_service import (
    FFMPEG_BEFORE_OPTIONS,
    FFMPEG_LIVE_BEFORE_OPTIONS,
    VideoUnavailable,
    cleanup_download,
    get_playable_source,
    get_stream_url,
    search_song,
)
from utils.logger import logger

_SOUNDS_DIR = Path(__file__).parent.parent / "sounds"

SongHook = Callable[[int, Optional[Song]], Awaitable[None]]


class MusicPlayer:
    """Per-guild playback engine.

    All state is keyed by guild_id; nothing is ever shared between guilds.
    Callers can register async callbacks:
      on_song_start(guild_id, song)  — fired just after playback begins
      on_song_end(guild_id, song)    — fired just before moving to next song
    """

    def __init__(self) -> None:
        self._states: dict[int, GuildState] = {}
        self.on_song_start: Optional[SongHook] = None
        self.on_song_end: Optional[SongHook] = None

    # ── State ──────────────────────────────────────────────────────────

    def get_state(self, guild_id: int) -> GuildState:
        if guild_id not in self._states:
            self._states[guild_id] = GuildState()
        return self._states[guild_id]

    def _clear_prefetch(self, state: GuildState) -> None:
        """Invalidate the prefetched source, deleting its cache file if any."""
        cleanup_download(state.prefetch_url)
        state.prefetch_url = None
        state.prefetch_song = None

    # ── Queue ──────────────────────────────────────────────────────────

    def add_to_queue(self, guild_id: int, song: Song) -> bool:
        state = self.get_state(guild_id)
        if len(state.queue) >= MAX_QUEUE_SIZE:
            return False
        state.queue.append(song)
        return True

    def add_songs_to_queue(self, guild_id: int, songs: list[Song]) -> int:
        state = self.get_state(guild_id)
        added = 0
        for song in songs:
            if len(state.queue) >= MAX_QUEUE_SIZE:
                break
            state.queue.append(song)
            added += 1
        return added

    def shuffle_queue(self, guild_id: int) -> bool:
        state = self.get_state(guild_id)
        if not state.queue:
            return False
        random.shuffle(state.queue)
        self._clear_prefetch(state)  # queue order changed
        logger.info("[Guild %s] Queue shuffled.", guild_id)
        return True

    def add_to_front(self, guild_id: int, song: Song) -> bool:
        """Insert a song at the top of the queue (Play Next)."""
        state = self.get_state(guild_id)
        if len(state.queue) >= MAX_QUEUE_SIZE:
            return False
        state.queue.insert(0, song)
        self._clear_prefetch(state)  # front of queue changed
        return True

    def clear_queue(self, guild_id: int) -> int:
        """Empty the pending queue but keep the current song playing."""
        state = self.get_state(guild_id)
        n = len(state.queue)
        state.queue.clear()
        self._clear_prefetch(state)
        logger.info("[Guild %s] Queue cleared (%d removed).", guild_id, n)
        return n

    def dedupe_queue(self, guild_id: int) -> int:
        """Drop later duplicates (same URL) from the queue. Returns count removed."""
        state = self.get_state(guild_id)
        seen: set[str] = set()
        kept: list[Song] = []
        for song in state.queue:
            if song.url in seen:
                continue
            seen.add(song.url)
            kept.append(song)
        removed = len(state.queue) - len(kept)
        if removed:
            state.queue = kept
            self._clear_prefetch(state)
        return removed

    def skip_to(self, guild_id: int, position: int) -> Optional[Song]:
        """Drop everything before queue position *position* (1-based) and skip,
        so that song plays next. Returns the target song, or None if invalid."""
        state = self.get_state(guild_id)
        if position < 1 or position > len(state.queue):
            return None
        target = state.queue[position - 1]
        del state.queue[: position - 1]
        self._clear_prefetch(state)
        self.skip(guild_id)
        return target

    def set_stay_247(self, guild_id: int) -> bool:
        """Toggle 24/7 mode. Returns the new state."""
        state = self.get_state(guild_id)
        state.stay_247 = not state.stay_247
        if state.stay_247:
            self._cancel_idle_timer(guild_id)
        return state.stay_247

    # ── Status ─────────────────────────────────────────────────────────

    def is_playing(self, guild_id: int) -> bool:
        s = self.get_state(guild_id)
        return bool(s.voice_client and s.voice_client.is_playing())

    def is_paused(self, guild_id: int) -> bool:
        s = self.get_state(guild_id)
        return bool(s.voice_client and s.voice_client.is_paused())

    def is_active(self, guild_id: int) -> bool:
        return self.is_playing(guild_id) or self.is_paused(guild_id)

    # ── Progress ───────────────────────────────────────────────────────

    def get_progress(self, guild_id: int) -> float:
        state = self.get_state(guild_id)
        if state.play_start_time is None:
            return 0.0
        elapsed = time.time() - state.play_start_time - state.total_paused
        if state.paused_at is not None:
            elapsed -= time.time() - state.paused_at
        return max(0.0, elapsed)

    # ── Volume ─────────────────────────────────────────────────────────

    def set_volume(self, guild_id: int, volume: float) -> None:
        state = self.get_state(guild_id)
        state.volume = volume
        if state.voice_client and isinstance(
            state.voice_client.source, discord.PCMVolumeTransformer
        ):
            state.voice_client.source.volume = volume

    # ── Loop ───────────────────────────────────────────────────────────

    def cycle_loop(self, guild_id: int) -> LoopMode:
        state = self.get_state(guild_id)
        state.loop_mode = state.loop_mode.next()
        logger.info("[Guild %s] Loop mode: %s", guild_id, state.loop_mode.value)
        return state.loop_mode

    def set_loop(self, guild_id: int, mode: LoopMode) -> None:
        self.get_state(guild_id).loop_mode = mode

    # ── Filter ─────────────────────────────────────────────────────────

    def set_filter(self, guild_id: int, filter_key: str) -> None:
        self.get_state(guild_id).audio_filter = filter_key

    # ── Auto-radio ─────────────────────────────────────────────────────

    def toggle_auto_radio(self, guild_id: int) -> bool:
        state = self.get_state(guild_id)
        state.auto_radio_enabled = not state.auto_radio_enabled
        return state.auto_radio_enabled

    # ── SFX ────────────────────────────────────────────────────────────

    def toggle_sfx(self, guild_id: int) -> bool:
        state = self.get_state(guild_id)
        state.sfx_enabled = not state.sfx_enabled
        return state.sfx_enabled

    def try_play_sfx(self, guild_id: int, filename: str) -> None:
        state = self.get_state(guild_id)
        if not state.sfx_enabled:
            return
        if not state.voice_client or not state.voice_client.is_connected():
            return
        if state.voice_client.is_playing() or state.voice_client.is_paused():
            return

        sound_path = _SOUNDS_DIR / filename
        if not sound_path.exists():
            return

        try:
            source = discord.FFmpegPCMAudio(str(sound_path), executable=FFMPEG_PATH)
            state.voice_client.play(source)
        except Exception as exc:
            logger.warning("[Guild %s] SFX play failed: %s", guild_id, exc)

    # ── Core playback ──────────────────────────────────────────────────

    async def play_next(self, guild_id: int, _skip_count: int = 0) -> None:
        if _skip_count >= 5:
            logger.error("[Guild %s] 5 consecutive stream failures — stopping.", guild_id)
            state = self.get_state(guild_id)
            cleanup_download(state.current_stream_url)
            state.current_stream_url = None
            state.current_song = None
            if state.last_text_channel:
                try:
                    await state.last_text_channel.send(
                        "❌ 連續 5 首無法播放，已自動停止。請確認網路或稍後再試。"
                    )
                except Exception:
                    pass
            return
        state = self.get_state(guild_id)
        ended_song = state.current_song

        # Live auto-recovery: a live stream that dropped on its own (not skipped /
        # stopped — those null current_song) should reconnect & restart rather than
        # advance. Runs before the lifecycle hooks so a brief drop doesn't churn the
        # panel. Bounded: 3 restarts within 60s of each other = treat as dead.
        if (
            ended_song is not None
            and ended_song.is_live
            and not state.seeking
            and state.voice_client
            and state.voice_client.is_connected()
        ):
            now = time.time()
            if now - state.last_live_restart > 60:
                state.live_restarts = 0
            if state.live_restarts < 3:
                state.live_restarts += 1
                state.last_live_restart = now
                logger.info(
                    "[Guild %s] Live '%s' dropped — reconnecting (restart #%d)",
                    guild_id, ended_song.title, state.live_restarts,
                )
                url = await get_stream_url(ended_song)
                if url:
                    state.current_stream_url = url
                    if await self._play_from(guild_id, ended_song, url, 0.0):
                        return
            else:
                logger.warning("[Guild %s] Live '%s' kept dropping — giving up.", guild_id, ended_song.title)
                if state.last_text_channel:
                    try:
                        await state.last_text_channel.send(
                            f"🔴 直播 **{ended_song.title[:40]}** 連線多次中斷，已停止。"
                        )
                    except Exception:
                        pass

        # Track last played song for F3 /replay
        if ended_song is not None:
            state.last_played_song = ended_song

        # Fire on_song_end callback for the song that just finished
        if ended_song is not None and self.on_song_end:
            try:
                await self.on_song_end(guild_id, ended_song)
            except Exception as exc:
                logger.error("[Guild %s] on_song_end error: %s", guild_id, exc)

        # Loop mode re-insertion (bypassed when stop/skip clears current_song first)
        if state.current_song is not None:
            if state.loop_mode == LoopMode.SONG:
                state.queue.insert(0, state.current_song)
            elif state.loop_mode == LoopMode.QUEUE:
                state.queue.append(state.current_song)

        # Auto-radio: fill queue when empty (never seed from a live stream)
        if (
            not state.queue
            and state.auto_radio_enabled
            and state.current_song
            and not state.current_song.is_live
        ):
            last_title = state.current_song.title
            logger.info("[Guild %s] Auto-radio searching for: %s", guild_id, last_title)
            related = await search_song(last_title, "Auto-Radio")
            if related:
                state.queue.append(related)

        if not state.queue:
            cleanup_download(state.current_stream_url)
            state.current_stream_url = None
            state.current_song = None
            logger.info("[Guild %s] Queue exhausted.", guild_id)
            return

        if state.voice_client is None or not state.voice_client.is_connected():
            cleanup_download(state.current_stream_url)
            state.current_stream_url = None
            state.current_song = None
            return

        song = state.queue.pop(0)
        state.current_song = song
        state.live_restarts = 0   # fresh song — reset live recovery counter

        # Use prefetched source if it's for this exact song, else fetch now
        if state.prefetch_song is song and state.prefetch_url:
            stream_url = state.prefetch_url
            state.prefetch_url = None      # now the current source — don't delete
            state.prefetch_song = None
            logger.info("[Guild %s] Using prefetched source for '%s'", guild_id, song.title)
        else:
            self._clear_prefetch(state)    # stale prefetch for a different song
            try:
                stream_url = await get_playable_source(song)
            except VideoUnavailable as exc:
                # Removed / private / region-blocked video (common in old playlists
                # after copyright takedowns). Skip it, but DON'T count it toward the
                # consecutive-failure stop — it's a dead entry, not a network problem.
                logger.info("[Guild %s] '%s' 已下架/無法播放，跳過：%s", guild_id, song.title, exc)
                state.current_song = None
                await self.play_next(guild_id, _skip_count)
                return

        if not stream_url:
            logger.error("[Guild %s] No source for '%s', skipping.", guild_id, song.title)
            state.current_song = None
            await self.play_next(guild_id, _skip_count + 1)
            return

        # Switch the current source, deleting the previous song's cache file.
        # Guard the != check so a looped song (same path) isn't deleted.
        old_source = state.current_stream_url
        state.current_stream_url = stream_url
        if old_source != stream_url:
            cleanup_download(old_source)

        # SponsorBlock: look up segments to skip (YouTube, non-live only)
        state.sponsor_segments = []
        if state.sponsorblock_enabled and not song.is_live:
            from services.sponsorblock_service import get_sponsor_segments
            state.sponsor_segments = await get_sponsor_segments(song.url)
            if state.sponsor_segments:
                logger.info(
                    "[Guild %s] SponsorBlock: %d segment(s) for '%s'",
                    guild_id, len(state.sponsor_segments), song.title,
                )

        if not await self._play_from(guild_id, song, stream_url, 0.0):
            state.current_song = None
            await self.play_next(guild_id, _skip_count + 1)
            return

        # Fire-and-forget: stats + on_song_start callback
        task = asyncio.create_task(
            record_play(guild_id, song.title, song.url, song.requester)
        )
        task.add_done_callback(
            lambda t: logger.error("Stats record error: %s", t.exception())
            if not t.cancelled() and t.exception()
            else None
        )

        if self.on_song_start:
            asyncio.create_task(self._fire_start_hook(guild_id, song))

        # Prefetch stream URL for the next song in the background
        if state.queue:
            asyncio.create_task(self._prefetch_next(guild_id, state.queue[0]))

    # ── Source build / seek / SponsorBlock ──────────────────────────────

    async def _play_from(
        self, guild_id: int, song: Song, stream_url: str, start_at: float = 0.0
    ) -> bool:
        """Build and start an FFmpeg source for *song*, optionally seeking to
        *start_at* seconds. Returns False if playback couldn't be started."""
        state = self.get_state(guild_id)
        if not state.voice_client or not state.voice_client.is_connected():
            return False

        start_at = self._adjust_for_sponsor(state, start_at)
        ffmpeg_opts = build_ffmpeg_options(state.audio_filter)
        if song.source == "bilibili":
            from services.bilibili_service import BILI_FFMPEG_HEADERS
            before_opts = f'-headers "{BILI_FFMPEG_HEADERS}" {FFMPEG_BEFORE_OPTIONS}'
        elif song.is_live:
            before_opts = FFMPEG_LIVE_BEFORE_OPTIONS  # reconnect at EOF for HLS
        else:
            before_opts = FFMPEG_BEFORE_OPTIONS
        if start_at > 0:
            before_opts = f"-ss {start_at:.2f} {before_opts}"
        # Route the googlevideo fetch through the same proxy yt-dlp used, so the
        # IP that requested the URL matches the IP that streams it (else 403).
        if YT_PROXY and "googlevideo.com" in stream_url:
            before_opts = f'-http_proxy "{YT_PROXY}" {before_opts}'
        try:
            raw = discord.FFmpegPCMAudio(
                stream_url, before_options=before_opts, options=ffmpeg_opts, executable=FFMPEG_PATH
            )
            source = discord.PCMVolumeTransformer(raw, volume=state.volume)
        except Exception as exc:
            logger.error("[Guild %s] FFmpeg error for '%s': %s", guild_id, song.title, exc)
            return False

        # play_start_time is shifted back by start_at so get_progress reads correctly
        state.play_start_time = time.time() - start_at
        state.paused_at = None
        state.total_paused = 0.0
        self._cancel_idle_timer(guild_id)
        state.voice_client.play(source, after=self._make_after(guild_id, song, stream_url))
        logger.info(
            "[Guild %s] Now playing: %s @%.0fs (req: %s)",
            guild_id, song.title, start_at, song.requester,
        )
        self._start_sponsor_watch(guild_id, song)
        return True

    def _make_after(self, guild_id: int, song: Song, stream_url: str):
        loop = asyncio.get_running_loop()

        def _after(error: Optional[Exception]) -> None:
            if error:
                logger.error("[Guild %s] Playback error: %s", guild_id, error)
            state = self.get_state(guild_id)
            if state.seeking:
                state.seeking = False
                asyncio.run_coroutine_threadsafe(
                    self._resume_seek(guild_id, song, stream_url, state.seek_target), loop
                )
            else:
                asyncio.run_coroutine_threadsafe(self.play_next(guild_id), loop)

        return _after

    async def _resume_seek(self, guild_id: int, song: Song, stream_url: str, target: float) -> None:
        if not await self._play_from(guild_id, song, stream_url, target):
            await self.play_next(guild_id)

    async def seek(self, guild_id: int, seconds: float) -> Optional[int]:
        """Seek the current song to *seconds*. Returns the clamped position, or
        None if seeking isn't possible (no song / live stream / not playing)."""
        state = self.get_state(guild_id)
        song = state.current_song
        if not song or song.is_live or not song.duration:
            return None
        seconds = max(0, min(int(seconds), max(0, song.duration - 1)))
        if not state.current_stream_url:
            state.current_stream_url = await get_stream_url(song)
        if not state.current_stream_url:
            return None
        if not (state.voice_client and (state.voice_client.is_playing() or state.voice_client.is_paused())):
            return None
        self._cancel_sponsor_watch(guild_id)
        state.seek_target = float(seconds)
        state.seeking = True
        state.voice_client.stop()  # _after fires -> _resume_seek(seek_target)
        return seconds

    def _adjust_for_sponsor(self, state: GuildState, start_at: float) -> float:
        for (a, b) in state.sponsor_segments:
            if a - 0.6 <= start_at < b:
                return b
        return start_at

    def _start_sponsor_watch(self, guild_id: int, song: Song) -> None:
        self._cancel_sponsor_watch(guild_id)
        state = self.get_state(guild_id)
        if not state.sponsor_segments:
            return
        state.sponsor_watch_task = asyncio.create_task(self._sponsor_watch(guild_id, song))

    def _cancel_sponsor_watch(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        if state.sponsor_watch_task and not state.sponsor_watch_task.done():
            state.sponsor_watch_task.cancel()
        state.sponsor_watch_task = None

    async def _sponsor_watch(self, guild_id: int, song: Song) -> None:
        """Poll playback position and seek past any skip segment we enter."""
        state = self.get_state(guild_id)
        try:
            while True:
                await asyncio.sleep(1.0)
                if state.current_song is not song:
                    return
                prog = self.get_progress(guild_id)
                seg = next(
                    ((a, b) for (a, b) in state.sponsor_segments if a - 0.6 <= prog < b), None
                )
                if seg and state.voice_client and state.voice_client.is_playing():
                    logger.info("[Guild %s] SponsorBlock skip %.1f→%.1f", guild_id, seg[0], seg[1])
                    await self.seek(guild_id, seg[1])
                    return  # the seek restarts the watch
                if not state.sponsor_segments or prog >= state.sponsor_segments[-1][1]:
                    return  # past the last segment — nothing left to skip
        except asyncio.CancelledError:
            return

    def set_sponsorblock(self, guild_id: int) -> bool:
        state = self.get_state(guild_id)
        state.sponsorblock_enabled = not state.sponsorblock_enabled
        if not state.sponsorblock_enabled:
            # turning off mid-song: stop skipping the already-fetched segments
            self._cancel_sponsor_watch(guild_id)
            state.sponsor_segments = []
        return state.sponsorblock_enabled

    async def _fire_start_hook(self, guild_id: int, song: Song) -> None:
        try:
            if self.on_song_start:
                await self.on_song_start(guild_id, song)
        except Exception as exc:
            logger.error("[Guild %s] on_song_start error: %s", guild_id, exc)

    async def _prefetch_next(self, guild_id: int, next_song: Song) -> None:
        state = self.get_state(guild_id)
        try:
            source = await get_playable_source(next_song)
            # Only store if the queue still has this song at front (not shuffled/removed)
            if state.queue and state.queue[0] is next_song and source:
                if state.prefetch_url and state.prefetch_url != source:
                    cleanup_download(state.prefetch_url)
                state.prefetch_url = source
                state.prefetch_song = next_song
                logger.info("[Guild %s] Prefetched source for '%s'", guild_id, next_song.title)
            elif source:
                cleanup_download(source)  # queue changed mid-download — orphan
        except Exception as exc:
            logger.debug("[Guild %s] Prefetch failed for '%s': %s", guild_id, next_song.title, exc)

    def skip(self, guild_id: int) -> bool:
        state = self.get_state(guild_id)
        if state.voice_client and (state.voice_client.is_playing() or state.voice_client.is_paused()):
            state.current_song = None   # bypass loop re-insertion
            self._clear_prefetch(state)  # prefetch is for the old queue order
            state.seeking = False       # this stop() is a real skip, not a seek
            old_source = state.current_stream_url
            state.current_stream_url = None
            self._cancel_sponsor_watch(guild_id)
            state.voice_client.stop()
            cleanup_download(old_source)
            logger.info("[Guild %s] Skipped.", guild_id)
            return True
        return False

    def pause(self, guild_id: int) -> bool:
        state = self.get_state(guild_id)
        if state.voice_client and state.voice_client.is_playing():
            state.voice_client.pause()
            state.paused_at = time.time()
            return True
        return False

    def resume(self, guild_id: int) -> bool:
        state = self.get_state(guild_id)
        if state.voice_client and state.voice_client.is_paused():
            state.voice_client.resume()
            if state.paused_at is not None:
                state.total_paused += time.time() - state.paused_at
                state.paused_at = None
            return True
        return False

    def stop(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        state.queue.clear()
        state.current_song = None
        self._clear_prefetch(state)
        state.play_start_time = None
        state.paused_at = None
        state.total_paused = 0.0
        state.play_message = ""
        state.seeking = False
        old_source = state.current_stream_url
        state.current_stream_url = None
        state.sponsor_segments = []
        self._cancel_sponsor_watch(guild_id)
        self._cancel_idle_timer(guild_id)
        if state.voice_client and (state.voice_client.is_playing() or state.voice_client.is_paused()):
            state.voice_client.stop()
        cleanup_download(old_source)
        logger.info("[Guild %s] Stopped.", guild_id)

    # ── Auto-disconnect ─────────────────────────────────────────────────

    def check_alone_in_channel(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        if not state.voice_client or not state.voice_client.is_connected():
            return
        if state.stay_247:               # 24/7 mode: never leave on its own
            self._cancel_idle_timer(guild_id)
            return
        humans = [m for m in state.voice_client.channel.members if not m.bot]
        if humans:
            self._cancel_idle_timer(guild_id)
        elif state.idle_timer_task is None or state.idle_timer_task.done():
            self._start_idle_timer(guild_id)

    def _start_idle_timer(self, guild_id: int) -> None:
        self._cancel_idle_timer(guild_id)
        state = self.get_state(guild_id)
        state.idle_timer_task = asyncio.create_task(self._idle_timeout(guild_id))

    def _cancel_idle_timer(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        if state.idle_timer_task and not state.idle_timer_task.done():
            state.idle_timer_task.cancel()
        state.idle_timer_task = None

    async def _idle_timeout(self, guild_id: int) -> None:
        try:
            await asyncio.sleep(AUTO_DISCONNECT_SECONDS)
        except asyncio.CancelledError:
            return
        state = self.get_state(guild_id)
        if state.voice_client and state.voice_client.is_connected():
            logger.info("[Guild %s] Auto-disconnecting (no listeners).", guild_id)
            self.stop(guild_id)
            await state.voice_client.disconnect()
            state.voice_client = None
