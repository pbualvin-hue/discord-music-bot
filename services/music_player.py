from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

import discord

from config import AUTO_DISCONNECT_SECONDS, FFMPEG_PATH, MAX_QUEUE_SIZE
from models.guild_state import GuildState
from models.loop_mode import LoopMode
from models.song import Song
from services.filter_service import build_ffmpeg_options
from services.stats_service import record_play
from services.youtube_service import (
    FFMPEG_BEFORE_OPTIONS,
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
        state.prefetch_url = None  # queue order changed
        state.prefetch_song = None
        logger.info("[Guild %s] Queue shuffled.", guild_id)
        return True

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
        if _skip_count >= 3:
            logger.error("[Guild %s] 3 consecutive stream failures — stopping.", guild_id)
            state = self.get_state(guild_id)
            state.current_song = None
            if state.last_text_channel:
                try:
                    await state.last_text_channel.send(
                        "❌ 連續 3 首無法播放，已自動停止。請確認網路或稍後再試。"
                    )
                except Exception:
                    pass
            return
        state = self.get_state(guild_id)
        ended_song = state.current_song

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

        # Auto-radio: fill queue when empty
        if not state.queue and state.auto_radio_enabled and state.current_song:
            last_title = state.current_song.title
            logger.info("[Guild %s] Auto-radio searching for: %s", guild_id, last_title)
            related = await search_song(last_title, "Auto-Radio")
            if related:
                state.queue.append(related)

        if not state.queue:
            state.current_song = None
            logger.info("[Guild %s] Queue exhausted.", guild_id)
            return

        if state.voice_client is None or not state.voice_client.is_connected():
            state.current_song = None
            return

        song = state.queue.pop(0)
        state.current_song = song

        # Use prefetched URL if it's for this exact song, else fetch now
        if state.prefetch_song is song and state.prefetch_url:
            stream_url = state.prefetch_url
            logger.info("[Guild %s] Using prefetched stream for '%s'", guild_id, song.title)
        else:
            stream_url = await get_stream_url(song)
        state.prefetch_url = None
        state.prefetch_song = None

        if not stream_url:
            logger.error("[Guild %s] No stream URL for '%s', skipping.", guild_id, song.title)
            state.current_song = None
            await self.play_next(guild_id, _skip_count + 1)
            return

        ffmpeg_opts = build_ffmpeg_options(state.audio_filter)
        before_opts = FFMPEG_BEFORE_OPTIONS
        if song.source == "bilibili":
            # bilivideo CDN rejects requests without a bilibili Referer/UA.
            from services.bilibili_service import BILI_FFMPEG_HEADERS
            before_opts = f'-headers "{BILI_FFMPEG_HEADERS}" {FFMPEG_BEFORE_OPTIONS}'
        try:
            raw = discord.FFmpegPCMAudio(
                stream_url,
                before_options=before_opts,
                options=ffmpeg_opts,
                executable=FFMPEG_PATH,
            )
            source = discord.PCMVolumeTransformer(raw, volume=state.volume)
        except Exception as exc:
            logger.error("[Guild %s] FFmpeg error for '%s': %s", guild_id, song.title, exc)
            state.current_song = None
            await self.play_next(guild_id, _skip_count + 1)
            return

        state.play_start_time = time.time()
        state.paused_at = None
        state.total_paused = 0.0

        event_loop = asyncio.get_running_loop()

        def _after(error: Optional[Exception]) -> None:
            if error:
                logger.error("[Guild %s] Playback error: %s", guild_id, error)
            asyncio.run_coroutine_threadsafe(self.play_next(guild_id), event_loop)

        self._cancel_idle_timer(guild_id)
        state.voice_client.play(source, after=_after)
        logger.info("[Guild %s] Now playing: %s (req: %s)", guild_id, song.title, song.requester)

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

    async def _fire_start_hook(self, guild_id: int, song: Song) -> None:
        try:
            if self.on_song_start:
                await self.on_song_start(guild_id, song)
        except Exception as exc:
            logger.error("[Guild %s] on_song_start error: %s", guild_id, exc)

    async def _prefetch_next(self, guild_id: int, next_song: Song) -> None:
        state = self.get_state(guild_id)
        try:
            url = await get_stream_url(next_song)
            # Only store if the queue still has this song at front (not shuffled/removed)
            if state.queue and state.queue[0] is next_song and url:
                state.prefetch_url = url
                state.prefetch_song = next_song
                logger.info("[Guild %s] Prefetched stream for '%s'", guild_id, next_song.title)
        except Exception as exc:
            logger.debug("[Guild %s] Prefetch failed for '%s': %s", guild_id, next_song.title, exc)

    def skip(self, guild_id: int) -> bool:
        state = self.get_state(guild_id)
        if state.voice_client and (state.voice_client.is_playing() or state.voice_client.is_paused()):
            state.current_song = None   # bypass loop re-insertion
            state.prefetch_url = None   # prefetch is for the old queue order
            state.prefetch_song = None
            state.voice_client.stop()
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
        state.prefetch_url = None
        state.prefetch_song = None
        state.play_start_time = None
        state.paused_at = None
        state.total_paused = 0.0
        state.play_message = ""
        self._cancel_idle_timer(guild_id)
        if state.voice_client and (state.voice_client.is_playing() or state.voice_client.is_paused()):
            state.voice_client.stop()
        logger.info("[Guild %s] Stopped.", guild_id)

    # ── Auto-disconnect ─────────────────────────────────────────────────

    def check_alone_in_channel(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        if not state.voice_client or not state.voice_client.is_connected():
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
