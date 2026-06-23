"""K + J: Enhanced two-row control panel with DJ permission layer.

Row 0: ⏮ replay | ⏸/▶ pause | ⏭ skip | 🔀 shuffle | ⏹ stop
Row 1: 🔁 loop  | 🎨 filter  | 📋 queue | 🎤 KTV    | 🔊 volume
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from services.filter_service import ALL_FILTERS, FILTER_LABELS
from utils.permissions import check_dj, check_voice, is_dj

if TYPE_CHECKING:
    from services.music_player import MusicPlayer

_FILTER_EMOJIS = {"off": "🎵", "bass": "🔉", "nightcore": "⚡", "slow": "🌙", "8d": "🎧"}


class VolumeModal(discord.ui.Modal, title="🔊 調整音量"):
    level = discord.ui.TextInput(
        label="音量 (1–200)",
        placeholder="輸入 1–200 的整數，100 = 原始音量",
        min_length=1,
        max_length=3,
    )

    def __init__(self, player: "MusicPlayer", guild_id: int) -> None:
        super().__init__()
        self.player = player
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            vol = int(self.level.value)
        except ValueError:
            await interaction.response.send_message("❌ 請輸入整數。", ephemeral=True)
            return
        if not 1 <= vol <= 200:
            await interaction.response.send_message("❌ 範圍為 1–200。", ephemeral=True)
            return
        self.player.set_volume(self.guild_id, vol / 100.0)
        await interaction.response.send_message(f"🔊 音量設為 **{vol}%**", ephemeral=True)


class ControlPanelView(discord.ui.View):
    """Two-row interactive control panel (K).

    Timeout=None for the dedicated music channel embed so it never expires.
    Pass timeout=180 for inline /nowplaying messages.
    """

    def __init__(
        self,
        player: "MusicPlayer",
        guild_id: int,
        *,
        timeout: float | None = None,
        cog: object | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.player = player
        self.guild_id = guild_id
        self._cog = cog
        self._sync_pause_label()
        self._sync_filter_label()

    # ── Helpers ────────────────────────────────────────────────────────

    def _vc(self) -> discord.VoiceClient | None:
        return self.player.get_state(self.guild_id).voice_client

    def _sync_pause_label(self) -> None:
        btn: discord.ui.Button = self.btn_pause  # type: ignore[assignment]
        if self.player.is_paused(self.guild_id):
            btn.emoji, btn.label = "▶️", "繼續"
        else:
            btn.emoji, btn.label = "⏸", "暫停"

    def _sync_filter_label(self) -> None:
        state = self.player.get_state(self.guild_id)
        f = state.audio_filter
        btn: discord.ui.Button = self.btn_filter  # type: ignore[assignment]
        btn.emoji = _FILTER_EMOJIS.get(f, "🎵")
        btn.label = FILTER_LABELS.get(f, "關閉")

    # ── Row 0 ──────────────────────────────────────────────────────────

    @discord.ui.button(emoji="⏮", label="重播", style=discord.ButtonStyle.secondary, row=0)
    async def btn_replay(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await check_voice(interaction, self._vc()):
            return
        state = self.player.get_state(self.guild_id)
        song = state.last_played_song
        if not song:
            await interaction.response.send_message("❌ 沒有上一首記錄。", ephemeral=True)
            return
        state.queue.insert(0, song)
        if not self.player.is_active(self.guild_id):
            await interaction.response.send_message(f"⏮ 重播：**{song.title}**", ephemeral=True)
            await self.player.play_next(self.guild_id)
        else:
            await interaction.response.send_message(
                f"⏮ 已將 **{song.title}** 插入 Queue 頂端", ephemeral=True
            )

    @discord.ui.button(emoji="⏸", label="暫停", style=discord.ButtonStyle.secondary, row=0)
    async def btn_pause(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await check_voice(interaction, self._vc()):
            return
        if self.player.is_playing(self.guild_id):
            self.player.pause(self.guild_id)
            button.emoji, button.label = "▶️", "繼續"
        elif self.player.is_paused(self.guild_id):
            self.player.resume(self.guild_id)
            button.emoji, button.label = "⏸", "暫停"
        else:
            await interaction.response.send_message("❌ 目前沒有播放中的歌曲。", ephemeral=True)
            return
        await interaction.response.edit_message(view=self)

    @discord.ui.button(emoji="⏭", label="跳過", style=discord.ButtonStyle.secondary, row=0)
    async def btn_skip(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await check_voice(interaction, self._vc()):
            return
        state = self.player.get_state(self.guild_id)
        # Allow: DJ / requester of current song; others are blocked
        requester = state.current_song.requester if state.current_song else None
        if not is_dj(interaction.user) and interaction.user.display_name != requester:  # type: ignore[arg-type]
            await interaction.response.send_message(
                "❌ 只有點歌者或 **DJ** 可以直接跳歌。\n"
                "一般成員請使用 `/voteskip` 發起投票。",
                ephemeral=True,
            )
            return
        title = state.current_song.title if state.current_song else "未知"
        self.player.skip(self.guild_id)
        await interaction.response.send_message(f"⏭️ 已跳過：**{title}**", ephemeral=True)

    @discord.ui.button(emoji="🔀", label="隨機", style=discord.ButtonStyle.secondary, row=0)
    async def btn_shuffle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await check_voice(interaction, self._vc()):
            return
        if self.player.shuffle_queue(self.guild_id):
            state = self.player.get_state(self.guild_id)
            await interaction.response.send_message(
                f"🔀 Queue 已隨機排列（{len(state.queue)} 首）", ephemeral=True
            )
        else:
            await interaction.response.send_message("❌ Queue 是空的。", ephemeral=True)

    @discord.ui.button(emoji="⏹", label="停止", style=discord.ButtonStyle.danger, row=0)
    async def btn_stop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await check_dj(interaction, self._vc()):
            return
        state = self.player.get_state(self.guild_id)
        queue_count = len(state.queue)
        if queue_count > 0:
            confirm_view = _StopConfirmView(self.player, self.guild_id, queue_count, parent=self)
            await interaction.response.send_message(
                f"⚠️ Queue 中還有 **{queue_count}** 首歌，確定要停止並清空嗎？",
                view=confirm_view,
                ephemeral=True,
            )
            return
        self._do_stop()
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("⏹️ 已停止播放", ephemeral=True)

    def _do_stop(self) -> None:
        self.player.stop(self.guild_id)
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]
        self.stop()

    # ── Row 1 ──────────────────────────────────────────────────────────

    @discord.ui.button(emoji="🔁", label="迴圈", style=discord.ButtonStyle.secondary, row=1)
    async def btn_loop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await check_voice(interaction, self._vc()):
            return
        new_mode = self.player.cycle_loop(self.guild_id)
        await interaction.response.send_message(
            f"{new_mode.emoji()} 迴圈模式：**{new_mode.label()}**", ephemeral=True
        )

    @discord.ui.button(emoji="🎵", label="濾鏡:關閉", style=discord.ButtonStyle.secondary, row=1)
    async def btn_filter(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await check_voice(interaction, self._vc()):
            return
        state = self.player.get_state(self.guild_id)
        current_idx = ALL_FILTERS.index(state.audio_filter) if state.audio_filter in ALL_FILTERS else 0
        next_filter = ALL_FILTERS[(current_idx + 1) % len(ALL_FILTERS)]
        self.player.set_filter(self.guild_id, next_filter)

        # Restart current song with new filter
        if self.player.is_active(self.guild_id) and state.current_song:
            state.queue.insert(0, state.current_song)
            self.player.skip(self.guild_id)

        button.emoji = _FILTER_EMOJIS.get(next_filter, "🎵")
        button.label = f"濾鏡:{FILTER_LABELS.get(next_filter, next_filter)}"
        await interaction.response.edit_message(view=self)

    @discord.ui.button(emoji="📋", label="Queue", style=discord.ButtonStyle.secondary, row=1)
    async def btn_queue(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from ui.queue_panel_view import QueuePanelView

        state = self.player.get_state(self.guild_id)
        # Cancel existing panel task
        if state.queue_panel_task and not state.queue_panel_task.done():
            state.queue_panel_task.cancel()
        view = QueuePanelView(self.player, self.guild_id)
        embed = view.build_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(emoji="🎤", label="KTV", style=discord.ButtonStyle.secondary, row=1)
    async def btn_karaoke(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Handled by the Cog via a callback to avoid circular import
        # We defer and let the command system do the heavy lifting
        await interaction.response.defer(ephemeral=True)
        state = self.player.get_state(self.guild_id)
        if not state.current_song:
            await interaction.followup.send("❌ 目前沒有播放中的歌曲。", ephemeral=True)
            return

        # Toggle via guild_state flag; the Cog's on_song_start hook picks it up
        state.karaoke_enabled = not state.karaoke_enabled
        if state.karaoke_enabled:
            await interaction.followup.send(
                "🎤 KTV 模式已開啟，正在載入歌詞…（可能需要 5–10 秒）",
                ephemeral=True,
            )
            # Start karaoke for current song immediately (not just next song)
            state.karaoke_pending = True
            if state.current_song and self._cog:
                import asyncio as _asyncio
                _asyncio.create_task(self._cog._start_karaoke(self.guild_id, state.current_song))
        else:
            await interaction.followup.send("🎤 KTV 模式已關閉。", ephemeral=True)
            if state.karaoke_task and not state.karaoke_task.done():
                state.karaoke_task.cancel()
            if state.karaoke_message:
                try:
                    await state.karaoke_message.delete()
                except Exception:
                    pass
                state.karaoke_message = None

    @discord.ui.button(emoji="🔊", label="音量", style=discord.ButtonStyle.secondary, row=1)
    async def btn_volume(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await check_voice(interaction, self._vc()):
            return
        modal = VolumeModal(self.player, self.guild_id)
        await interaction.response.send_modal(modal)

    # ── Row 2 ──────────────────────────────────────────────────────────

    async def _seek_relative(self, interaction: discord.Interaction, delta: int) -> None:
        if not await check_voice(interaction, self._vc()):
            return
        state = self.player.get_state(self.guild_id)
        song = state.current_song
        if not song or song.is_live or not song.duration:
            await interaction.response.send_message("❌ 直播/無時長歌曲無法跳轉。", ephemeral=True)
            return
        target = self.player.get_progress(self.guild_id) + delta
        await interaction.response.defer()
        result = await self.player.seek(self.guild_id, target)
        if result is None:
            await interaction.followup.send("❌ 無法跳轉。", ephemeral=True)
        else:
            arrow = "⏪" if delta < 0 else "⏩"
            await interaction.followup.send(f"{arrow} {result // 60}:{result % 60:02d}", ephemeral=True)

    @discord.ui.button(emoji="⏪", label="-15s", style=discord.ButtonStyle.secondary, row=2)
    async def btn_rewind(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._seek_relative(interaction, -15)

    @discord.ui.button(emoji="⏩", label="+15s", style=discord.ButtonStyle.secondary, row=2)
    async def btn_forward(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._seek_relative(interaction, 15)

    @discord.ui.button(emoji="🛡️", label="SponsorBlock", style=discord.ButtonStyle.secondary, row=2)
    async def btn_sponsorblock(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await check_voice(interaction, self._vc()):
            return
        on = self.player.set_sponsorblock(self.guild_id)
        msg = (
            "🛡️ SponsorBlock 已開啟 — 自動跳過非音樂/業配段（下一首生效）"
            if on else "🛡️ SponsorBlock 已關閉"
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(emoji="♾️", label="24/7", style=discord.ButtonStyle.secondary, row=2)
    async def btn_247(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await check_voice(interaction, self._vc()):
            return
        on = self.player.set_stay_247(self.guild_id)
        msg = "♾️ 24/7 已開啟 — 沒人也不離開" if on else "⏏️ 24/7 已關閉"
        await interaction.response.send_message(msg, ephemeral=True)


class _StopConfirmView(discord.ui.View):
    """Ephemeral confirmation shown when /stop is pressed with a non-empty queue."""

    def __init__(
        self,
        player: "MusicPlayer",
        guild_id: int,
        queue_count: int,
        *,
        parent: ControlPanelView,
    ) -> None:
        super().__init__(timeout=30)
        self.player = player
        self.guild_id = guild_id
        self.queue_count = queue_count
        self.parent = parent

    @discord.ui.button(label="確定停止", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.parent._do_stop()
        await interaction.response.edit_message(
            content=f"⏹️ 已停止播放並清空 {self.queue_count} 首待播曲目。",
            view=None,
        )

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="✅ 已取消。", view=None)
