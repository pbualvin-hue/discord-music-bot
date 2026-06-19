from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import discord

from config import DEFAULT_VOLUME
from models.loop_mode import LoopMode
from models.song import Song


@dataclass
class GuildState:
    queue: list[Song] = field(default_factory=list)
    current_song: Optional[Song] = None
    last_played_song: Optional[Song] = None      # F3: /replay
    voice_client: Optional[discord.VoiceClient] = None
    idle_timer_task: Optional[asyncio.Task] = None

    # Playback settings
    loop_mode: LoopMode = field(default_factory=lambda: LoopMode.OFF)
    volume: float = DEFAULT_VOLUME
    audio_filter: str = "off"

    # Progress tracking
    play_start_time: Optional[float] = None
    paused_at: Optional[float] = None
    total_paused: float = 0.0

    # Feature toggles
    auto_radio_enabled: bool = False
    sfx_enabled: bool = False
    stay_247: bool = False        # 24/7: never auto-disconnect when alone

    # B2: point-of-play message tag
    play_message: str = ""

    # Vote-skip
    vote_skip_message: Optional[discord.Message] = None

    # D1 / K: Live Now Playing embed (updated every 15 s)
    live_embed_message: Optional[discord.Message] = None
    live_embed_task: Optional[asyncio.Task] = None

    # D4 / K: Dedicated music channel
    music_channel_id: Optional[int] = None

    # H: Interactive Queue panel (one per guild)
    queue_panel_message: Optional[discord.Message] = None
    queue_panel_task: Optional[asyncio.Task] = None

    # L1: KTV karaoke mode
    karaoke_enabled: bool = False
    karaoke_pending: bool = False   # set by control panel before next song starts
    karaoke_message: Optional[discord.Message] = None
    karaoke_task: Optional[asyncio.Task] = None
    karaoke_lines: list = field(default_factory=list)  # [(float, str)]

    # Prefetch: stream URL pre-fetched for queue[0] while current song is playing
    prefetch_url: Optional[str] = None
    prefetch_song: Optional[Song] = None

    # Seek: reuse the current stream URL and replay from an offset
    current_stream_url: Optional[str] = None
    seeking: bool = False
    seek_target: float = 0.0

    # SponsorBlock: skip non-music / sponsor segments of the current song
    sponsorblock_enabled: bool = False
    sponsor_segments: list = field(default_factory=list)   # [(start, end)] seconds
    sponsor_watch_task: Optional[asyncio.Task] = None

    # Context: last text channel that issued a command (for announcements)
    last_text_channel: Optional[discord.TextChannel] = None
