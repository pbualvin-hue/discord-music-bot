from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from models.loop_mode import LoopMode
from models.song import Song
from services.filter_service import FILTER_LABELS, get_filter_color
from services.lyrics_karaoke_service import (
    build_karaoke_window,
    fetch_synced_lyrics,
    get_current_line_idx,
)
from services.lyrics_service import chunk_lyrics, fetch_lyrics
from services.music_player import MusicPlayer
from services.personality_service import (
    get_achievement_text,
    get_join_greeting,
    get_play_response,
    get_seasonal_decoration,
)
from services.playlist_service import json_to_songs, songs_to_json
from services.stats_service import (
    check_new_achievements,
    clear_music_channel,
    delete_playlist,
    get_music_channel,
    get_my_stats,
    get_play_history,
    get_song_rating,
    get_top_requesters,
    get_top_rated_songs,
    get_top_songs,
    get_total_plays,
    get_year_wrap,
    list_playlists,
    load_playlist,
    save_music_channel,
    save_playlist,
    update_music_channel_message,
)
from services.bilibili_service import (
    is_bilibili_url,
    resolve_bilibili_song,
    search_bilibili,
    strip_bili_prefix,
)
from services.youtube_service import (
    get_playlist_songs,
    is_playlist_url,
    is_soundcloud_url,
    is_url,
    search_multiple,
    search_soundcloud,
    search_song,
)
from ui.control_panel_view import ControlPanelView
from ui.karaoke_view import KaraokeView
from ui.lyrics_paged_view import LyricsPagedView
from ui.queue_panel_view import QueuePanelView
from ui.rating_view import RatingView
from ui.search_select_view import SearchSelectView
from ui.vote_skip_view import VoteSkipView
from utils.logger import logger
from utils.permissions import is_dj

_SOUNDS_DIR = Path(__file__).parent.parent / "sounds"
_BAR_WIDTH = 18
_LIVE_UPDATE_INTERVAL = 15   # seconds
_KARAOKE_UPDATE_INTERVAL = 5  # seconds
_TITLE_FRAMES = ["▶️ 播放中", "🎵 播放中", "🎶 播放中"]


# ── Embed helpers ─────────────────────────────────────────────────────

def _fmt(s: float) -> str:
    s = int(s)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _bar(elapsed: float, total: float) -> str:
    """J4: Progress bar with 🔘 pointer."""
    if total <= 0:
        return "─" * _BAR_WIDTH + "🔘"
    pos = int(min(elapsed / total, 1.0) * _BAR_WIDTH)
    return "━" * pos + "🔘" + "─" * (_BAR_WIDTH - pos)


def _playing_title(is_paused: bool) -> str:
    """J2: Animated title cycles every 15 s."""
    if is_paused:
        return "⏸ 已暫停"
    return _TITLE_FRAMES[(int(time.time()) // _LIVE_UPDATE_INTERVAL) % len(_TITLE_FRAMES)]


def _build_nowplaying_embed(
    song: Song,
    state,
    elapsed: float,
    is_paused: bool,
    *,
    ended: bool = False,
    rating: dict | None = None,
) -> discord.Embed:
    # D3: greyed-out ended state
    if ended:
        embed = discord.Embed(
            title="✅ 已播完",
            description=f"~~{song.title}~~",
            color=discord.Color.dark_grey(),
        )
        embed.set_footer(text="使用 /play 繼續點歌")
        if song.thumbnail:
            embed.set_thumbnail(url=song.thumbnail)
        return embed

    # D2: color by filter; orange when paused
    color = get_filter_color(state.audio_filter) if not is_paused else discord.Color.orange()

    # E4 + J2: seasonal deco + animated title
    season = get_seasonal_decoration()
    title = _playing_title(is_paused) + (f"  {season}" if season else "")

    embed = discord.Embed(title=title, description=f"**{song.title}**", color=color)

    # J4: pointer progress bar
    embed.add_field(
        name="進度",
        value=f"`{_bar(elapsed, float(song.duration))}`\n`{_fmt(elapsed)}` / `{song.duration_str}`",
        inline=False,
    )

    # B2: play message tag
    if state.play_message:
        embed.add_field(name=f"💬 {song.requester} 說", value=state.play_message, inline=False)
    else:
        embed.add_field(name="👤 點歌", value=song.requester, inline=True)

    embed.add_field(name=f"{state.loop_mode.emoji()} 迴圈", value=state.loop_mode.label(), inline=True)
    embed.add_field(name="🔊 音量", value=f"{int(state.volume * 100)}%", inline=True)
    embed.add_field(name="🎨 濾鏡", value=FILTER_LABELS.get(state.audio_filter, "關閉"), inline=True)

    # J3: rating field
    if rating and rating.get("votes", 0) > 0:
        stars = "⭐" * round(rating["avg"]) + f" {rating['avg']}"
        embed.add_field(name="評分", value=f"{stars}（{rating['votes']} 票）", inline=True)

    # J1: large thumbnail at bottom
    if song.thumbnail:
        embed.set_image(url=song.thumbnail)

    return embed


# ── Cog ───────────────────────────────────────────────────────────────

class MusicCog(commands.Cog, name="Music"):
    def __init__(self, bot: commands.Bot, player: MusicPlayer) -> None:
        self.bot = bot
        self.player = player
        player.on_song_start = self._on_song_start
        player.on_song_end = self._on_song_end
        self._status_rotation.start()

    def cog_unload(self) -> None:
        self._status_rotation.cancel()

    # ── Song lifecycle hooks ───────────────────────────────────────────

    async def _on_song_start(self, guild_id: int, song: Song) -> None:
        state = self.player.get_state(guild_id)
        state.play_message = state.play_message  # keep until manually cleared

        # D1: start live embed updater
        self._cancel_live_embed(guild_id)
        state.live_embed_task = asyncio.create_task(self._live_embed_loop(guild_id))

        # K / D4: update dedicated channel
        await self._update_music_channel(guild_id)

        # L1: restart KTV if previously enabled
        if state.karaoke_enabled or getattr(state, "karaoke_pending", False):
            state.karaoke_pending = False
            asyncio.create_task(self._start_karaoke(guild_id, song))

        # E1: personality response
        if state.last_text_channel:
            try:
                msg = get_play_response(song.title, song.requester)
                await state.last_text_channel.send(msg, delete_after=30)
            except Exception:
                pass

        # E3: achievements
        if song.requester not in ("Auto-Radio",):
            try:
                milestones = await check_new_achievements(guild_id, song.requester)
                for ms in milestones:
                    label, text = get_achievement_text(ms)
                    if state.last_text_channel:
                        embed = discord.Embed(
                            title=f"🏅 成就解鎖！{label}",
                            description=f"**{song.requester}** — {text}",
                            color=discord.Color.gold(),
                        )
                        await state.last_text_channel.send(embed=embed)
            except Exception as exc:
                logger.error("[Guild %s] Achievement error: %s", guild_id, exc)

    async def _on_song_end(self, guild_id: int, song: Song) -> None:
        state = self.player.get_state(guild_id)

        # Defence-in-depth: if a song "ends" within a few seconds it never really
        # played (e.g. CDN 403). Treat as a stream failure — warn instead of asking
        # for a rating, so a broken link can never trigger the rating prompt.
        played = self.player.get_progress(guild_id)
        stream_failed = played < 5.0 and (song.duration == 0 or song.duration > 10)

        # D1 + D3: stop updater, grey out embed
        self._cancel_live_embed(guild_id)
        if state.live_embed_message:
            try:
                embed = _build_nowplaying_embed(song, state, float(song.duration), False, ended=True)
                await state.live_embed_message.edit(embed=embed, view=None)
            except Exception:
                pass
            state.live_embed_message = None

        # L1: stop KTV
        self._cancel_karaoke(guild_id)

        # Clear play_message so next song starts fresh
        state.play_message = ""

        # B1: rating prompt — skipped when the song never actually played
        if state.last_text_channel and song.requester != "Auto-Radio":
            try:
                if stream_failed:
                    logger.warning(
                        "[Guild %s] '%s' ended after %.1fs — likely stream failure, no rating.",
                        guild_id, song.title, played,
                    )
                    await state.last_text_channel.send(
                        f"⚠️ **{song.title[:50]}** 串流異常，已自動跳過（未播放）。",
                        delete_after=20,
                    )
                else:
                    view = RatingView(guild_id, song)
                    await state.last_text_channel.send(
                        f"⭐ 幫 **{song.title[:50]}** 評個分吧！（60 秒後關閉）",
                        view=view,
                        delete_after=65,
                    )
            except Exception:
                pass

    # ── D1: Live embed update loop ─────────────────────────────────────

    async def _live_embed_loop(self, guild_id: int) -> None:
        try:
            while True:
                await asyncio.sleep(_LIVE_UPDATE_INTERVAL)
                state = self.player.get_state(guild_id)
                if not state.current_song or not state.live_embed_message:
                    break
                rating = await get_song_rating(guild_id, state.current_song.url)
                elapsed = self.player.get_progress(guild_id)
                embed = _build_nowplaying_embed(
                    state.current_song, state, elapsed,
                    self.player.is_paused(guild_id),
                    rating=rating,
                )
                try:
                    await state.live_embed_message.edit(embed=embed)
                except (discord.NotFound, discord.HTTPException):
                    break
        except asyncio.CancelledError:
            pass

    def _cancel_live_embed(self, guild_id: int) -> None:
        state = self.player.get_state(guild_id)
        if state.live_embed_task and not state.live_embed_task.done():
            state.live_embed_task.cancel()
        state.live_embed_task = None

    # ── K / D4: Dedicated music channel ───────────────────────────────

    async def _update_music_channel(self, guild_id: int) -> None:
        row = await get_music_channel(guild_id)
        if not row:
            return
        state = self.player.get_state(guild_id)
        if not state.current_song:
            return
        channel = self.bot.get_channel(row["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            return

        rating = await get_song_rating(guild_id, state.current_song.url)
        elapsed = self.player.get_progress(guild_id)
        embed = _build_nowplaying_embed(
            state.current_song, state, elapsed,
            self.player.is_paused(guild_id),
            rating=rating,
        )
        view = ControlPanelView(self.player, guild_id, timeout=None, cog=self)

        if row["message_id"]:
            try:
                msg = await channel.fetch_message(row["message_id"])
                await msg.edit(embed=embed, view=view)
                state.live_embed_message = msg
                return
            except (discord.NotFound, discord.HTTPException):
                pass

        try:
            msg = await channel.send(embed=embed, view=view)
            state.live_embed_message = msg
            await update_music_channel_message(guild_id, msg.id)
        except Exception as exc:
            logger.error("[Guild %s] Music channel update failed: %s", guild_id, exc)

    # ── L1: KTV karaoke ────────────────────────────────────────────────

    async def _start_karaoke(self, guild_id: int, song: Song) -> None:
        state = self.player.get_state(guild_id)
        if not state.last_text_channel:
            return

        lines = await fetch_synced_lyrics(song.title)
        if not lines:
            try:
                await state.last_text_channel.send(
                    f"🎤 找不到 **{song.title}** 的 LRC 時間軸歌詞。\n"
                    "使用 `/lyrics` 查看純文字歌詞。",
                    delete_after=15,
                )
            except Exception:
                pass
            state.karaoke_enabled = False
            state.karaoke_pending = False
            return

        state.karaoke_lines = lines
        self._cancel_karaoke(guild_id)

        embed = self._build_karaoke_embed(song, lines, 0, 0.0)
        view = KaraokeView(guild_id)
        try:
            msg = await state.last_text_channel.send(
                "🎤 **KTV 模式**　每 5 秒自動更新", embed=embed, view=view
            )
            state.karaoke_message = msg
        except Exception as exc:
            logger.error("[Guild %s] Karaoke send failed: %s", guild_id, exc)
            return

        state.karaoke_task = asyncio.create_task(
            self._karaoke_loop(guild_id, song, lines, msg, view)
        )

    async def _karaoke_loop(
        self,
        guild_id: int,
        song: Song,
        lines: list,
        msg: discord.Message,
        view: KaraokeView,
    ) -> None:
        try:
            while not view.closed:
                await asyncio.sleep(_KARAOKE_UPDATE_INTERVAL)
                state = self.player.get_state(guild_id)
                if not state.karaoke_enabled or state.current_song != song:
                    break
                elapsed = self.player.get_progress(guild_id)
                idx = get_current_line_idx(lines, elapsed)
                embed = self._build_karaoke_embed(song, lines, idx, elapsed)
                try:
                    await msg.edit(embed=embed)
                except (discord.NotFound, discord.HTTPException):
                    break
        except asyncio.CancelledError:
            pass

    def _build_karaoke_embed(
        self, song: Song, lines: list, idx: int, elapsed: float
    ) -> discord.Embed:
        window = build_karaoke_window(lines, idx)
        embed = discord.Embed(
            title=f"🎤 {song.title}",
            description=window,
            color=discord.Color.dark_gold(),
        )
        embed.add_field(
            name="進度",
            value=f"`{_bar(elapsed, float(song.duration))}`  `{_fmt(elapsed)} / {song.duration_str}`",
            inline=False,
        )
        if song.thumbnail:
            embed.set_thumbnail(url=song.thumbnail)
        embed.set_footer(text="每 5 秒自動同步歌詞　按 ❌ 關閉")
        return embed

    def _cancel_karaoke(self, guild_id: int) -> None:
        state = self.player.get_state(guild_id)
        if state.karaoke_task and not state.karaoke_task.done():
            state.karaoke_task.cancel()
        state.karaoke_task = None
        state.karaoke_lines = []

    # ── Background: Status rotation ────────────────────────────────────

    @tasks.loop(minutes=1)
    async def _status_rotation(self) -> None:
        playing = [
            gid for gid, s in self.player._states.items()
            if s.current_song and self.player.is_playing(gid)
        ]
        song = self.player.get_state(playing[0]).current_song if playing else None
        name = song.title[:128] if song else "/play 開始聆聽"
        await self.bot.change_presence(
            activity=discord.Activity(type=discord.ActivityType.listening, name=name)
        )

    @_status_rotation.before_loop
    async def _before_rotation(self) -> None:
        await self.bot.wait_until_ready()

    # ── Helpers ───────────────────────────────────────────────────────

    async def _ensure_voice(
        self, interaction: discord.Interaction, *, auto_join: bool = False
    ) -> bool:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ 你必須先加入語音頻道。", ephemeral=True)
            return False
        if not auto_join:
            return True
        state = self.player.get_state(interaction.guild_id)
        target = interaction.user.voice.channel
        if state.voice_client and state.voice_client.is_connected():
            if state.voice_client.channel != target:
                await interaction.followup.send(
                    f"❌ Bot 目前在 **{state.voice_client.channel.name}**，"
                    "請先 `/stop` 再切換。", ephemeral=True
                )
                return False
            return True
        try:
            state.voice_client = await target.connect()
        except discord.ClientException as exc:
            logger.error("[Guild %s] Voice connect: %s", interaction.guild_id, exc)
            await interaction.followup.send("❌ 無法加入語音頻道。", ephemeral=True)
            return False
        return True

    def _track(self, interaction: discord.Interaction) -> None:
        if isinstance(interaction.channel, discord.TextChannel):
            self.player.get_state(interaction.guild_id).last_text_channel = interaction.channel

    # ── /join ─────────────────────────────────────────────────────────

    @app_commands.command(name="join", description="讓 Bot 加入你的語音頻道")
    async def join(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        self._track(interaction)
        if not interaction.user.voice:
            await interaction.followup.send("❌ 你必須先加入語音頻道。", ephemeral=True)
            return
        channel = interaction.user.voice.channel
        state = self.player.get_state(interaction.guild_id)
        if state.voice_client and state.voice_client.is_connected():
            if state.voice_client.channel == channel:
                await interaction.followup.send(f"✅ 已在 **{channel.name}**。", ephemeral=True)
                return
            await state.voice_client.move_to(channel)
            await interaction.followup.send(f"✅ 已移至 **{channel.name}**", ephemeral=True)
            return
        try:
            state.voice_client = await channel.connect()
            if state.last_text_channel:
                await state.last_text_channel.send(get_join_greeting())
            await interaction.followup.send(f"✅ 已加入 **{channel.name}**", ephemeral=True)
        except discord.ClientException as exc:
            logger.error("[Guild %s] Join: %s", interaction.guild_id, exc)
            await interaction.followup.send("❌ 無法加入語音頻道。", ephemeral=True)

    # ── /leave ────────────────────────────────────────────────────────

    @app_commands.command(name="leave", description="讓 Bot 離開語音頻道")
    async def leave(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        state = self.player.get_state(interaction.guild_id)
        if not state.voice_client or not state.voice_client.is_connected():
            await interaction.followup.send("❌ Bot 不在語音頻道。", ephemeral=True)
            return
        name = state.voice_client.channel.name
        self.player.stop(interaction.guild_id)
        await state.voice_client.disconnect()
        state.voice_client = None
        await interaction.followup.send(f"✅ 已離開 **{name}**", ephemeral=True)

    # ── /play ─────────────────────────────────────────────────────────

    @app_commands.command(name="play", description="播放音樂 — YT/Bilibili/SoundCloud 連結、Playlist 或關鍵字")
    @app_commands.describe(
        query="連結（YT/Bilibili/SoundCloud/Playlist）或搜尋關鍵字",
        source="指定來源；不選＝自動判定",
        position="插播位置：排到最後（預設）、下一首、立即播放",
        message="附上留言，顯示在 Now Playing 卡片上",
    )
    @app_commands.choices(
        source=[
            app_commands.Choice(name="自動判定", value="auto"),
            app_commands.Choice(name="YouTube", value="youtube"),
            app_commands.Choice(name="Bilibili", value="bilibili"),
            app_commands.Choice(name="SoundCloud", value="soundcloud"),
        ],
        position=[
            app_commands.Choice(name="排到最後", value="last"),
            app_commands.Choice(name="下一首", value="next"),
            app_commands.Choice(name="立即播放", value="now"),
        ],
    )
    async def play(
        self,
        interaction: discord.Interaction,
        query: str,
        source: str = "auto",
        position: str = "last",
        message: Optional[str] = None,
    ) -> None:
        await interaction.response.defer()
        self._track(interaction)
        if not await self._ensure_voice(interaction, auto_join=True):
            return

        requester = interaction.user.display_name
        guild_id = interaction.guild_id
        was_active = self.player.is_active(guild_id)
        state = self.player.get_state(guild_id)
        if message:
            state.play_message = message[:100]

        # ── resolve effective source + keyword ──────────────────────────
        url_mode = is_url(query)
        eff, kw = source, query
        if source == "auto":
            if is_bilibili_url(query):
                eff = "bilibili"
            elif is_soundcloud_url(query):
                eff = "soundcloud"
            elif url_mode:
                eff = "youtube"
            else:
                bili_kw = strip_bili_prefix(query)
                if bili_kw is not None:
                    eff, kw, url_mode = "bilibili", bili_kw, False
                else:
                    eff = "youtube"

        # ── Playlist (YouTube only) ─────────────────────────────────────
        if url_mode and eff == "youtube" and is_playlist_url(query):
            songs = await get_playlist_songs(query, requester)
            if not songs:
                await interaction.followup.send("❌ 無法解析 Playlist。")
                return
            added = self.player.add_songs_to_queue(guild_id, songs)
            suffix = f"（共 {len(songs)} 首，已達上限）" if added < len(songs) else ""
            await interaction.followup.send(f"📋 已將 **{added}** 首加入 Queue{suffix}")

        else:
            # ── single URL, or keyword → selection menu ─────────────────
            if url_mode:
                if eff == "bilibili":
                    song = await resolve_bilibili_song(query, requester)
                elif eff == "soundcloud":
                    song = await search_song(query, requester, source="soundcloud")
                else:
                    song = await search_song(query, requester)
                if not song:
                    await interaction.followup.send("❌ 無法解析連結。")
                    return
            else:
                if eff == "bilibili":
                    results = await search_bilibili(kw, requester)
                    label = "Bilibili"
                elif eff == "soundcloud":
                    results = await search_soundcloud(kw, requester)
                    label = "SoundCloud"
                else:
                    results = await search_multiple(kw, requester)
                    label = "YouTube"
                if not results:
                    await interaction.followup.send(f"❌ 在 {label} 找不到「{kw}」。")
                    return
                embed = discord.Embed(title=f"🔍 {label} 搜尋：{kw}", color=discord.Color.blurple())
                for i, s in enumerate(results, 1):
                    embed.add_field(name=f"{i}. {s.title}", value=f"⏱ {s.duration_str}", inline=False)
                if results[0].thumbnail:
                    embed.set_image(url=results[0].thumbnail)
                embed.set_footer(text="請從下方選單選擇，30 秒後自動取消")
                view = SearchSelectView(results)
                msg = await interaction.followup.send(embed=embed, view=view)
                song = await view.wait_for_selection()
                if song is None:
                    await msg.edit(content="❌ 選曲逾時，已取消。", embed=None, view=None)
                    return
                await msg.delete()

            # ── enqueue at the chosen position ──────────────────────────
            if position in ("next", "now"):
                ok = self.player.add_to_front(guild_id, song)
            else:
                ok = self.player.add_to_queue(guild_id, song)
            if not ok:
                await interaction.followup.send("❌ Queue 已達上限。")
                return
            if position == "now" and was_active:
                self.player.skip(guild_id)  # play the inserted song immediately
            if was_active:
                pos_tag = {"next": "（下一首）", "now": "（立即播放）"}.get(position, "")
                await interaction.followup.send(
                    f"✅ 已加入 Queue{pos_tag}：**{song.title}** `[{song.duration_str}]`"
                )

        if not was_active:
            await self.player.play_next(guild_id)
            if state.current_song:
                s = state.current_song
                rating = await get_song_rating(guild_id, s.url)
                embed = _build_nowplaying_embed(s, state, self.player.get_progress(guild_id), False, rating=rating)
                view = ControlPanelView(self.player, guild_id, timeout=180, cog=self)
                np_msg = await interaction.followup.send(embed=embed, view=view)
                state.live_embed_message = np_msg
            else:
                await interaction.followup.send("❌ 播放失敗，無法取得串流。")

    # ── /nowplaying ───────────────────────────────────────────────────

    @app_commands.command(name="nowplaying", description="顯示 Now Playing 控制台（自動更新）")
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        self._track(interaction)
        state = self.player.get_state(interaction.guild_id)
        if not state.current_song:
            await interaction.response.send_message("❌ 目前沒有播放中的歌曲。", ephemeral=True)
            return
        rating = await get_song_rating(interaction.guild_id, state.current_song.url)
        elapsed = self.player.get_progress(interaction.guild_id)
        embed = _build_nowplaying_embed(
            state.current_song, state, elapsed,
            self.player.is_paused(interaction.guild_id), rating=rating,
        )
        view = ControlPanelView(self.player, interaction.guild_id, timeout=180, cog=self)
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        state.live_embed_message = msg
        self._cancel_live_embed(interaction.guild_id)
        state.live_embed_task = asyncio.create_task(self._live_embed_loop(interaction.guild_id))

    # ── /queue ────────────────────────────────────────────────────────

    @app_commands.command(name="queue", description="開啟互動式 Queue 面板")
    async def queue(self, interaction: discord.Interaction) -> None:
        state = self.player.get_state(interaction.guild_id)
        if state.queue_panel_task and not state.queue_panel_task.done():
            state.queue_panel_task.cancel()
        view = QueuePanelView(self.player, interaction.guild_id)
        embed = view.build_embed()
        await interaction.response.send_message(embed=embed, view=view)

    # ── /skip / /pause / /resume / /stop ──────────────────────────────

    @app_commands.command(name="skip", description="跳過目前歌曲")
    async def skip(self, interaction: discord.Interaction) -> None:
        if not self.player.is_active(interaction.guild_id):
            await interaction.response.send_message("❌ 目前沒有播放中的歌曲。", ephemeral=True)
            return
        state = self.player.get_state(interaction.guild_id)
        title = state.current_song.title if state.current_song else "未知"
        requester = state.current_song.requester if state.current_song else None
        if not is_dj(interaction.user) and interaction.user.display_name != requester:  # type: ignore[arg-type]
            await interaction.response.send_message(
                "❌ 只有點歌者或 **DJ** 可以直接跳歌。請使用 `/voteskip`。", ephemeral=True
            )
            return
        self.player.skip(interaction.guild_id)
        await interaction.response.send_message(f"⏭️ 已跳過：**{title}**")

    @app_commands.command(name="pause", description="暫停播放")
    async def pause(self, interaction: discord.Interaction) -> None:
        if self.player.pause(interaction.guild_id):
            await interaction.response.send_message("⏸️ 已暫停")
        else:
            await interaction.response.send_message("❌ 目前沒有播放中的歌曲。", ephemeral=True)

    @app_commands.command(name="resume", description="繼續播放")
    async def resume(self, interaction: discord.Interaction) -> None:
        if self.player.resume(interaction.guild_id):
            await interaction.response.send_message("▶️ 已繼續播放")
        else:
            await interaction.response.send_message("❌ 目前沒有暫停中的歌曲。", ephemeral=True)

    @app_commands.command(name="stop", description="停止播放並清空 Queue（DJ）")
    async def stop(self, interaction: discord.Interaction) -> None:
        if not is_dj(interaction.user):  # type: ignore[arg-type]
            await interaction.response.send_message("❌ 需要 **DJ** 身份組或管理員權限。", ephemeral=True)
            return
        self.player.stop(interaction.guild_id)
        await interaction.response.send_message("⏹️ 已停止並清空 Queue")

    # ── /volume / /loop / /shuffle / /filter ──────────────────────────

    @app_commands.command(name="volume", description="調整音量（1–200）")
    @app_commands.describe(level="音量百分比（100 = 原始音量）")
    async def volume(self, interaction: discord.Interaction, level: int) -> None:
        if not 1 <= level <= 200:
            await interaction.response.send_message("❌ 範圍為 1–200。", ephemeral=True)
            return
        self.player.set_volume(interaction.guild_id, level / 100.0)
        await interaction.response.send_message(f"🔊 音量設為 **{level}%**")

    @app_commands.command(name="loop", description="設定迴圈模式（不填則依序切換）")
    @app_commands.choices(mode=[
        app_commands.Choice(name="off   — 關閉", value="off"),
        app_commands.Choice(name="song  — 單曲迴圈", value="song"),
        app_commands.Choice(name="queue — Queue 迴圈", value="queue"),
    ])
    async def loop(self, interaction: discord.Interaction, mode: Optional[str] = None) -> None:
        if mode:
            self.player.set_loop(interaction.guild_id, LoopMode(mode))
            new_mode = LoopMode(mode)
        else:
            new_mode = self.player.cycle_loop(interaction.guild_id)
        await interaction.response.send_message(f"{new_mode.emoji()} 迴圈：**{new_mode.label()}**")

    @app_commands.command(name="shuffle", description="隨機打亂 Queue 順序")
    async def shuffle(self, interaction: discord.Interaction) -> None:
        if self.player.shuffle_queue(interaction.guild_id):
            state = self.player.get_state(interaction.guild_id)
            await interaction.response.send_message(f"🔀 Queue 已隨機排列（{len(state.queue)} 首）")
        else:
            await interaction.response.send_message("❌ Queue 是空的。", ephemeral=True)

    @app_commands.command(name="skipto", description="跳到 Queue 中第 N 首（中間的歌會被略過）")
    @app_commands.describe(position="Queue 中的位置（1 = 下一首）")
    async def skipto(self, interaction: discord.Interaction, position: int) -> None:
        target = self.player.skip_to(interaction.guild_id, position)
        if target is None:
            await interaction.response.send_message("❌ 位置超出 Queue 範圍。", ephemeral=True)
            return
        await interaction.response.send_message(f"⏭️ 跳到第 **{position}** 首：**{target.title}**")

    @app_commands.command(name="clear", description="清空 Queue（保留目前播放中的歌）")
    async def clear(self, interaction: discord.Interaction) -> None:
        n = self.player.clear_queue(interaction.guild_id)
        if n == 0:
            await interaction.response.send_message("❌ Queue 已經是空的。", ephemeral=True)
        else:
            await interaction.response.send_message(f"🧹 已清空 Queue（移除 **{n}** 首），目前歌曲繼續播放。")

    @app_commands.command(name="dedupe", description="移除 Queue 中重複的歌曲")
    async def dedupe(self, interaction: discord.Interaction) -> None:
        removed = self.player.dedupe_queue(interaction.guild_id)
        if removed == 0:
            await interaction.response.send_message("✨ Queue 沒有重複歌曲。", ephemeral=True)
        else:
            await interaction.response.send_message(f"🧹 已移除 **{removed}** 首重複歌曲。")

    @app_commands.command(name="247", description="開關 24/7 模式（無人時也不自動離開）")
    async def stay247(self, interaction: discord.Interaction) -> None:
        on = self.player.set_stay_247(interaction.guild_id)
        if on:
            await interaction.response.send_message("🔁 **24/7 模式已開啟** — 即使沒人聆聽也會留在語音頻道。")
        else:
            await interaction.response.send_message("⏏️ **24/7 模式已關閉** — 無人聆聽時將自動離開。")

    @app_commands.command(name="filter", description="套用音訊濾鏡（立即重播）")
    @app_commands.choices(effect=[
        app_commands.Choice(name="off       — 關閉", value="off"),
        app_commands.Choice(name="bass      — 🔉 低音增強", value="bass"),
        app_commands.Choice(name="nightcore — ⚡ Nightcore", value="nightcore"),
        app_commands.Choice(name="slow      — 🌙 Slowed", value="slow"),
        app_commands.Choice(name="8d        — 🎧 8D 環繞", value="8d"),
    ])
    async def filter_cmd(self, interaction: discord.Interaction, effect: str) -> None:
        guild_id = interaction.guild_id
        state = self.player.get_state(guild_id)
        self.player.set_filter(guild_id, effect)
        label = FILTER_LABELS.get(effect, effect)
        if self.player.is_active(guild_id) and state.current_song:
            state.queue.insert(0, state.current_song)
            self.player.skip(guild_id)
            await interaction.response.send_message(f"🎨 濾鏡 **{label}**，重新播放…")
        else:
            await interaction.response.send_message(f"🎨 濾鏡設為 **{label}**，下首生效。")

    # ── /remove / /move / /replay ─────────────────────────────────────

    @app_commands.command(name="remove", description="從 Queue 移除指定位置的歌曲（F4）")
    @app_commands.describe(position="要移除的編號（/queue 看到的數字）")
    async def remove(self, interaction: discord.Interaction, position: int) -> None:
        state = self.player.get_state(interaction.guild_id)
        if not state.queue:
            await interaction.response.send_message("❌ Queue 是空的。", ephemeral=True)
            return
        if not 1 <= position <= len(state.queue):
            await interaction.response.send_message(
                f"❌ 請輸入 1–{len(state.queue)} 的數字。", ephemeral=True
            )
            return
        removed = state.queue.pop(position - 1)
        await interaction.response.send_message(f"🗑️ 已移除第 {position} 首：**{removed.title}**")

    @app_commands.command(name="move", description="調整 Queue 中歌曲的順序（F1）")
    @app_commands.describe(
        from_pos="要移動的歌曲編號",
        to_pos="要插入到的目標位置",
    )
    async def move(self, interaction: discord.Interaction, from_pos: int, to_pos: int) -> None:
        state = self.player.get_state(interaction.guild_id)
        n = len(state.queue)
        if n == 0:
            await interaction.response.send_message("❌ Queue 是空的。", ephemeral=True)
            return
        if not (1 <= from_pos <= n and 1 <= to_pos <= n):
            await interaction.response.send_message(f"❌ 請輸入 1–{n} 的數字。", ephemeral=True)
            return
        if from_pos == to_pos:
            await interaction.response.send_message("❌ 來源與目標相同。", ephemeral=True)
            return
        song = state.queue.pop(from_pos - 1)
        state.queue.insert(to_pos - 1, song)
        await interaction.response.send_message(
            f"↕️ 已將 **{song.title}** 從第 {from_pos} 位移到第 {to_pos} 位"
        )

    @app_commands.command(name="replay", description="重播上一首歌曲（F3）")
    async def replay(self, interaction: discord.Interaction) -> None:
        state = self.player.get_state(interaction.guild_id)
        song = state.last_played_song
        if not song:
            await interaction.response.send_message("❌ 沒有上一首記錄。", ephemeral=True)
            return
        state.queue.insert(0, song)
        await interaction.response.send_message(f"⏮ 已將 **{song.title}** 插入 Queue 頂端")
        if not self.player.is_active(interaction.guild_id):
            state2 = self.player.get_state(interaction.guild_id)
            if state2.voice_client and state2.voice_client.is_connected():
                await self.player.play_next(interaction.guild_id)

    # ── /karaoke ─────────────────────────────────────────────────────

    @app_commands.command(name="karaoke", description="開啟 / 關閉 KTV 歌詞滾動模式（L1）")
    async def karaoke(self, interaction: discord.Interaction) -> None:
        self._track(interaction)
        state = self.player.get_state(interaction.guild_id)
        if not state.current_song:
            await interaction.response.send_message("❌ 目前沒有播放中的歌曲。", ephemeral=True)
            return

        if state.karaoke_enabled:
            state.karaoke_enabled = False
            self._cancel_karaoke(interaction.guild_id)
            if state.karaoke_message:
                try:
                    await state.karaoke_message.delete()
                except Exception:
                    pass
                state.karaoke_message = None
            await interaction.response.send_message("🎤 KTV 模式已關閉。", ephemeral=True)
        else:
            state.karaoke_enabled = True
            await interaction.response.send_message(
                "🎤 正在載入同步歌詞，請稍候…（5–10 秒）", ephemeral=True
            )
            await self._start_karaoke(interaction.guild_id, state.current_song)

    # ── /lyrics ───────────────────────────────────────────────────────

    @app_commands.command(name="lyrics", description="查詢歌詞（I: 翻頁式閱讀器）")
    async def lyrics(self, interaction: discord.Interaction) -> None:
        from config import GENIUS_API_KEY
        if not GENIUS_API_KEY:
            await interaction.response.send_message(
                "❌ 尚未設定 Genius API Key。前往 <https://genius.com/developers> 申請。",
                ephemeral=True,
            )
            return
        state = self.player.get_state(interaction.guild_id)
        if not state.current_song:
            await interaction.response.send_message("❌ 目前沒有播放中的歌曲。", ephemeral=True)
            return
        await interaction.response.defer()
        song = state.current_song
        raw = await fetch_lyrics(song.title)
        if not raw:
            await interaction.followup.send(f"❌ 找不到 **{song.title}** 的歌詞。")
            return
        chunks = chunk_lyrics(raw)
        view = LyricsPagedView(song, chunks)
        await interaction.followup.send(embed=view.build_embed(), view=view)

    # ── /stats ────────────────────────────────────────────────────────

    @app_commands.command(name="stats", description="查看播放統計排行榜")
    @app_commands.choices(
        category=[
            app_commands.Choice(name="🎵 最多播放", value="songs"),
            app_commands.Choice(name="🎤 最活躍點歌者", value="users"),
            app_commands.Choice(name="⭐ 評分最高", value="rated"),
            app_commands.Choice(name="📊 全部", value="all"),
        ],
        period=[
            app_commands.Choice(name="本週", value="week"),
            app_commands.Choice(name="本月", value="month"),
            app_commands.Choice(name="全部時間", value="all"),
        ],
    )
    async def stats(
        self,
        interaction: discord.Interaction,
        category: str = "all",
        period: str = "all",
    ) -> None:
        await interaction.response.defer()
        period_label = {"week": "本週", "month": "本月", "all": "全部時間"}[period]
        gid = interaction.guild_id
        total = await get_total_plays(gid, period)
        medals = ["🥇", "🥈", "🥉"] + ["▪️"] * 7

        embed = discord.Embed(
            title=f"📊 播放統計 — {period_label}",
            description=f"共 **{total}** 次播放記錄",
            color=discord.Color.gold(),
        )
        if category in ("songs", "all"):
            rows = await get_top_songs(gid, period)
            lines = [f"{medals[i]} **{r['song_title'][:45]}** × {r['cnt']}" for i, r in enumerate(rows)]
            embed.add_field(name="🎵 最多播放 Top 10", value="\n".join(lines) or "（尚無資料）", inline=False)
        if category in ("users", "all"):
            rows = await get_top_requesters(gid, period)
            lines = [f"{medals[i]} **{r['requester'][:30]}** — {r['cnt']} 首" for i, r in enumerate(rows)]
            embed.add_field(name="🎤 最活躍點歌者", value="\n".join(lines) or "（尚無資料）", inline=False)
        if category in ("rated", "all"):
            rows = await get_top_rated_songs(gid)
            lines = [
                f"{medals[i]} **{r['song_title'][:40]}** — ⭐{r['avg_rating']} ({r['votes']} 票)"
                for i, r in enumerate(rows)
            ]
            embed.add_field(name="⭐ 評分最高 Top 10", value="\n".join(lines) or "（尚無評分）", inline=False)

        await interaction.followup.send(embed=embed)

    # ── /mystats ──────────────────────────────────────────────────────

    @app_commands.command(name="mystats", description="查看自己的個人播放統計（G2）")
    async def mystats(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        data = await get_my_stats(interaction.guild_id, interaction.user.display_name)

        embed = discord.Embed(
            title=f"🎧 {interaction.user.display_name} 的播放統計",
            color=discord.Color.teal(),
        )
        embed.add_field(name="總點播數", value=f"**{data['total']}** 首", inline=True)
        embed.add_field(name="本週", value=f"**{data['week']}** 首", inline=True)
        if data["top_song"]:
            embed.add_field(
                name="最愛歌曲",
                value=f"**{data['top_song']['song_title'][:45]}**（{data['top_song']['cnt']} 次）",
                inline=False,
            )
        if data["avg_rating_given"]:
            embed.add_field(name="平均給分", value=f"⭐ {data['avg_rating_given']}", inline=True)
        if data["milestones"]:
            ms_text = "　".join(f"🏅 {m}" for m in data["milestones"])
            embed.add_field(name="解鎖成就", value=ms_text, inline=False)
        if data["total"] == 0:
            embed.description = "你還沒有點過歌！使用 `/play` 開始吧～"

        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /yearwrap ─────────────────────────────────────────────────────

    @app_commands.command(name="yearwrap", description="伺服器年度音樂回顧（G3）")
    @app_commands.describe(year="要查詢的年份（預設今年）")
    async def yearwrap(
        self, interaction: discord.Interaction, year: Optional[int] = None
    ) -> None:
        import datetime
        await interaction.response.defer()
        y = year or datetime.date.today().year
        data = await get_year_wrap(interaction.guild_id, y)

        if data["total"] == 0:
            await interaction.followup.send(f"❌ {y} 年沒有任何播放記錄。")
            return

        embed = discord.Embed(
            title=f"🎊 {y} 年度音樂回顧",
            description=f"共播放 **{data['total']}** 首歌，涵蓋 **{data['unique_songs']}** 首不重複歌曲！",
            color=discord.Color.blurple(),
        )
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        if data["top_songs"]:
            lines = [f"{medals[i]} **{r['song_title'][:45]}** × {r['cnt']}" for i, r in enumerate(data["top_songs"])]
            embed.add_field(name="🎵 年度 Top 5 歌曲", value="\n".join(lines), inline=False)
        if data["top_users"]:
            lines = [f"{medals[i]} **{r['requester'][:30]}** — {r['cnt']} 首" for i, r in enumerate(data["top_users"])]
            embed.add_field(name="🎤 年度最活躍 Top 5", value="\n".join(lines), inline=False)
        if data["peak_day"]:
            embed.add_field(
                name="🔥 播放高峰",
                value=f"**{data['peak_day']['day']}** — {data['peak_day']['cnt']} 首",
                inline=True,
            )

        await interaction.followup.send(embed=embed)

    # ── /songinfo ─────────────────────────────────────────────────────

    @app_commands.command(name="songinfo", description="顯示目前歌曲的詳細資訊（G1）")
    async def songinfo(self, interaction: discord.Interaction) -> None:
        state = self.player.get_state(interaction.guild_id)
        if not state.current_song:
            await interaction.response.send_message("❌ 目前沒有播放中的歌曲。", ephemeral=True)
            return
        await interaction.response.defer()
        song = state.current_song
        rating = await get_song_rating(interaction.guild_id, song.url)

        embed = discord.Embed(
            title=song.title,
            url=song.url,
            color=get_filter_color(state.audio_filter),
        )
        embed.add_field(name="👤 點歌者", value=song.requester, inline=True)
        embed.add_field(name="⏱ 時長", value=song.duration_str, inline=True)
        if rating["votes"] > 0:
            stars = "⭐" * round(rating["avg"])
            embed.add_field(name="評分", value=f"{stars} {rating['avg']}（{rating['votes']} 票）", inline=True)
        elapsed = self.player.get_progress(interaction.guild_id)
        embed.add_field(
            name="進度",
            value=f"`{_bar(elapsed, float(song.duration))}`  `{_fmt(elapsed)} / {song.duration_str}`",
            inline=False,
        )
        if song.thumbnail:
            embed.set_image(url=song.thumbnail)
        embed.set_footer(text=song.url)
        await interaction.followup.send(embed=embed)

    # ── /history ──────────────────────────────────────────────────────

    @app_commands.command(name="history", description="查看最近 10 首播放記錄")
    async def history(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        rows = await get_play_history(interaction.guild_id, limit=10)
        if not rows:
            await interaction.followup.send("❌ 尚無播放記錄。", ephemeral=True)
            return
        embed = discord.Embed(title="🕓 最近播放記錄", color=discord.Color.blurple())
        lines = []
        for i, r in enumerate(rows, 1):
            played_at = r["played_at"][:16].replace("T", " ")
            lines.append(f"`{i}.` **{r['song_title'][:45]}** — {r['requester']}　`{played_at}`")
        embed.description = "\n".join(lines)
        embed.set_footer(text="使用 /play <YouTube URL> 重播")
        await interaction.followup.send(embed=embed)

    # ── /playlist ─────────────────────────────────────────────────────

    @app_commands.command(name="playlist", description="管理收藏清單")
    @app_commands.describe(action="操作類型", name="清單名稱")
    @app_commands.choices(action=[
        app_commands.Choice(name="save   — 儲存目前 Queue", value="save"),
        app_commands.Choice(name="load   — 載入清單到 Queue", value="load"),
        app_commands.Choice(name="list   — 顯示所有清單", value="list"),
        app_commands.Choice(name="delete — 刪除清單", value="delete"),
    ])
    async def playlist(
        self,
        interaction: discord.Interaction,
        action: str,
        name: Optional[str] = None,
    ) -> None:
        await interaction.response.defer()
        gid = interaction.guild_id
        requester = interaction.user.display_name

        if action == "list":
            rows = await list_playlists(gid)
            if not rows:
                await interaction.followup.send("❌ 目前沒有任何儲存的清單。")
                return
            embed = discord.Embed(title="📚 收藏清單", color=discord.Color.green())
            lines = [
                f"**{r['name']}** — {r['song_count']} 首　建立者：{r['creator']}　`{r['created_at'][:10]}`"
                for r in rows
            ]
            embed.description = "\n".join(lines)
            await interaction.followup.send(embed=embed)
            return

        if not name:
            await interaction.followup.send("❌ 請填入清單名稱。", ephemeral=True)
            return

        if action == "save":
            state = self.player.get_state(gid)
            songs = ([state.current_song] if state.current_song else []) + list(state.queue)
            if not songs:
                await interaction.followup.send("❌ Queue 是空的，無法儲存。", ephemeral=True)
                return
            ok = await save_playlist(gid, requester, name, songs_to_json(songs))
            await interaction.followup.send(
                f"✅ 已儲存清單 **{name}**（{len(songs)} 首）" if ok else "❌ 儲存失敗。"
            )

        elif action == "load":
            row = await load_playlist(gid, name)
            if not row:
                await interaction.followup.send(f"❌ 找不到清單 **{name}**。", ephemeral=True)
                return
            songs = json_to_songs(row["songs_json"], requester)
            if not songs:
                await interaction.followup.send("❌ 清單是空的或資料損毀。", ephemeral=True)
                return
            added = self.player.add_songs_to_queue(gid, songs)
            await interaction.followup.send(f"📂 已載入清單 **{name}**，加入 **{added}** 首")
            state = self.player.get_state(gid)
            if not self.player.is_active(gid) and state.voice_client and state.voice_client.is_connected():
                await self.player.play_next(gid)

        elif action == "delete":
            ok = await delete_playlist(gid, name)
            await interaction.followup.send(
                f"🗑️ 已刪除清單 **{name}**" if ok else f"❌ 找不到清單 **{name}**。"
            )

    # ── /setchannel / /clearchannel ───────────────────────────────────

    @app_commands.command(name="setchannel", description="設定此頻道為專屬音樂控制台（K / D4）")
    async def setchannel(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("❌ 只能在文字頻道使用。", ephemeral=True)
            return
        gid = interaction.guild_id
        await save_music_channel(gid, interaction.channel_id)
        state = self.player.get_state(gid)
        state.last_text_channel = interaction.channel
        await interaction.response.send_message(
            f"✅ 已將 **{interaction.channel.name}** 設為音樂控制台！\n"
            "每首歌開始時 Bot 會自動在這裡更新 Now Playing 卡片（含 2 排控制按鈕）。"
        )
        if state.current_song:
            await self._update_music_channel(gid)

    @app_commands.command(name="clearchannel", description="取消專屬音樂控制台設定")
    async def clearchannel(self, interaction: discord.Interaction) -> None:
        await clear_music_channel(interaction.guild_id)
        await interaction.response.send_message("✅ 已清除音樂控制台設定。", ephemeral=True)

    # ── /voteskip ─────────────────────────────────────────────────────

    @app_commands.command(name="voteskip", description="發起投票跳歌（過半數同意）")
    async def voteskip(self, interaction: discord.Interaction) -> None:
        if not self.player.is_active(interaction.guild_id):
            await interaction.response.send_message("❌ 目前沒有播放中的歌曲。", ephemeral=True)
            return
        state = self.player.get_state(interaction.guild_id)
        if state.vote_skip_message:
            await interaction.response.send_message("❌ 已有進行中的投票。", ephemeral=True)
            return
        if not state.voice_client:
            await interaction.response.send_message("❌ Bot 不在語音頻道。", ephemeral=True)
            return
        humans = [m for m in state.voice_client.channel.members if not m.bot]
        required = max(2, (len(humans) + 1) // 2)
        song_title = state.current_song.title if state.current_song else "未知"
        if state.current_song and state.current_song.requester == interaction.user.display_name:
            self.player.skip(interaction.guild_id)
            await interaction.response.send_message(f"⏭️ 點歌者跳過：**{song_title}**")
            return
        view = VoteSkipView(self.player, interaction.guild_id, required, interaction.user.id, song_title)
        await interaction.response.send_message(
            f"🗳️ **{interaction.user.display_name}** 發起跳歌投票！\n"
            f"播放：**{song_title}**　需要 **{required}** 票（{len(humans)} 人在頻道）",
            view=view,
        )
        state.vote_skip_message = await interaction.original_response()
        await view.wait()
        state.vote_skip_message = None
        if not view.skipped:
            await interaction.edit_original_response(
                content=f"❌ 投票未通過（{len(view.voters)}/{required} 票），繼續播放。",
                view=None,
            )

    # ── /autoradio / /sfx ─────────────────────────────────────────────

    @app_commands.command(name="autoradio", description="開關自動推薦")
    async def autoradio(self, interaction: discord.Interaction) -> None:
        enabled = self.player.toggle_auto_radio(interaction.guild_id)
        await interaction.response.send_message(
            f"📻 自動推薦 **{'✅ 開啟' if enabled else '❌ 關閉'}**"
            + ("\nQueue 空時自動搜尋相關歌曲繼續播放。" if enabled else "")
        )

    @app_commands.command(name="sfx", description="開關入場音效")
    async def sfx(self, interaction: discord.Interaction) -> None:
        join_exists = (_SOUNDS_DIR / "join.mp3").exists()
        leave_exists = (_SOUNDS_DIR / "leave.mp3").exists()
        if not join_exists and not leave_exists:
            await interaction.response.send_message(
                "❌ 找不到音效檔。請在 `sounds/` 放入 `join.mp3` 和/或 `leave.mp3`",
                ephemeral=True,
            )
            return
        enabled = self.player.toggle_sfx(interaction.guild_id)
        await interaction.response.send_message(
            f"🔔 入場音效 **{'✅ 開啟' if enabled else '❌ 關閉'}**\n"
            f"• join.mp3 {'✅' if join_exists else '❌'}\n"
            f"• leave.mp3 {'✅' if leave_exists else '❌'}",
            ephemeral=True,
        )

    # ── /ping (診斷) ──────────────────────────────────────────────────

    @app_commands.command(name="ping", description="Bot 狀態與 yt-dlp 診斷（管理員用）")
    async def ping(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        import shutil
        import sys
        import yt_dlp as _ytdlp
        from services.youtube_service import _YDL_SINGLE, _build_opts

        version = _ytdlp.version.__version__
        node_path = shutil.which("node") or shutil.which("nodejs") or "❌ 找不到 (node/nodejs 未安裝)"

        try:
            loop = asyncio.get_running_loop()
            def _test():
                opts = _build_opts(_YDL_SINGLE)
                with _ytdlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info("五月天 倔強", download=False)
                    if not info:
                        return False, "info is None"
                    if "entries" in info:
                        entries = [e for e in (info["entries"] or []) if e]
                        if not entries:
                            return False, "entries list empty"
                        info = entries[0]
                    title = info.get("title") or info.get("id") or ""
                    return bool(title), title or "no title"

            ok, detail = await asyncio.wait_for(
                loop.run_in_executor(None, _test), timeout=35.0
            )
            search_status = f"✅ 搜尋正常：{detail[:50]}" if ok else f"❌ 搜尋失敗：{detail}"
        except asyncio.TimeoutError:
            search_status = "❌ 搜尋超時（>35s），YouTube 可能封鎖此 IP"
        except Exception as exc:
            search_status = f"❌ 搜尋例外：{type(exc).__name__}: {str(exc)[:60]}"

        state = self.player.get_state(interaction.guild_id)
        embed = discord.Embed(title="🔧 Bot 診斷", color=discord.Color.blurple())
        embed.add_field(name="Python", value=f"`{sys.version.split()[0]}`", inline=True)
        embed.add_field(name="yt-dlp", value=f"`{version}`", inline=True)
        embed.add_field(name="Node.js", value=f"`{node_path}`", inline=False)
        embed.add_field(name="YouTube 搜尋測試", value=search_status, inline=False)
        embed.add_field(
            name="播放器狀態",
            value=(
                f"語音連線：{'✅' if state.voice_client and state.voice_client.is_connected() else '❌'}\n"
                f"播放中：{'✅' if self.player.is_playing(interaction.guild_id) else '❌'}\n"
                f"Queue：{len(state.queue)} 首"
            ),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /help ─────────────────────────────────────────────────────────

    @app_commands.command(name="help", description="顯示所有指令說明")
    async def help(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(title="🎵 Music Bot — 指令列表", color=discord.Color.blurple())
        embed.add_field(name="🔊 語音", inline=False, value="`/join`  `/leave`")
        embed.add_field(
            name="▶️ 播放控制", inline=False,
            value=(
                "`/play <連結 · Playlist · 關鍵字>`\n"
                "　🔹 `來源` 下拉：YouTube／Bilibili／SoundCloud（不選＝自動判定）\n"
                "　🔹 `插播` 下拉：排到最後／下一首／立即播放\n"
                "`/pause`  `/resume`  `/skip`（DJ/點歌者）  `/stop`（DJ）"
            ),
        )
        embed.add_field(
            name="📋 Queue 管理", inline=False,
            value=(
                "`/queue` — 互動式面板　`/shuffle`　`/skipto <編號>`\n"
                "`/clear`（清空保留當前）　`/dedupe`（去重）\n"
                "`/remove <編號>`  `/move <從> <到>`  `/replay`  `/voteskip`"
            ),
        )
        embed.add_field(
            name="🎛️ Now Playing 控制台", inline=False,
            value=(
                "`/nowplaying` — 顯示控制台（2 排按鈕）\n"
                "`/setchannel` — 設定永久控制台頻道  `/clearchannel`\n"
                "控制台按鈕：⏮⏸⏭🔀⏹ / 🔁🎨📋🎤🔊（含音量 Modal）"
            ),
        )
        embed.add_field(
            name="📚 收藏清單", inline=False,
            value="`/playlist save|load|list|delete <名稱>`",
        )
        embed.add_field(
            name="⚙️ 設定", inline=False,
            value="`/volume <1-200>`  `/loop`  `/filter`  `/autoradio`  `/sfx`  `/247`（24/7 常駐）",
        )
        embed.add_field(
            name="✨ 特殊功能", inline=False,
            value=(
                "`/karaoke` — KTV 歌詞滾動（每 5 秒同步，可開關）\n"
                "`/lyrics` — 翻頁式歌詞閱讀器（Genius）\n"
                "`/songinfo` — 目前歌曲詳細資訊\n"
                "`/stats`  `/mystats`  `/history`  `/yearwrap [年份]`\n"
                "⭐ 歌曲評分　🏅 成就公告　🎭 個性回應　🎊 節慶裝飾"
            ),
        )
        embed.add_field(
            name="🔒 權限說明", inline=False,
            value=(
                "**所有人（在語音頻道中）**：播放、暫停、隨機、迴圈、投票跳歌\n"
                "**點歌者本人**：直接跳過自己的歌\n"
                "**DJ 身份組 / 管理員**：強制跳歌、停止、清空 Queue"
            ),
        )
        embed.set_footer(text="Now Playing 每 15 秒自動更新 · KTV 每 5 秒同步 · 歌曲結束後變灰色")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Events ────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return
        guild_id = member.guild.id
        state = self.player.get_state(guild_id)
        if not state.voice_client or not state.voice_client.is_connected():
            return
        bot_ch = state.voice_client.channel
        if after.channel == bot_ch and before.channel != bot_ch:
            self.player.try_play_sfx(guild_id, "join.mp3")
        elif before.channel == bot_ch and after.channel != bot_ch:
            self.player.try_play_sfx(guild_id, "leave.mp3")
        if before.channel == bot_ch or after.channel == bot_ch:
            self.player.check_alone_in_channel(guild_id)


async def setup(bot: commands.Bot, player: MusicPlayer) -> None:
    await bot.add_cog(MusicCog(bot, player))
