"""H: Interactive paginated Queue panel.

Shows 5 songs per page with navigation buttons.
Everyone can view; voice-channel members can shuffle; DJs can clear.
One active panel per guild (enforced by the Cog).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from utils.permissions import check_dj, check_voice, is_dj

if TYPE_CHECKING:
    from services.music_player import MusicPlayer

_PAGE_SIZE = 5
_BAR = 16


def _fmt(s: float) -> str:
    s = int(s)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _bar(elapsed: float, total: float) -> str:
    if total <= 0:
        return "─" * _BAR + "🔘"
    pos = int(min(elapsed / total, 1.0) * _BAR)
    return "━" * pos + "🔘" + "─" * (_BAR - pos)


class QueuePanelView(discord.ui.View):
    def __init__(self, player: "MusicPlayer", guild_id: int) -> None:
        super().__init__(timeout=120)
        self.player = player
        self.guild_id = guild_id
        self.page = 0
        self._refresh_nav()

    # ── Embed builder ──────────────────────────────────────────────────

    def build_embed(self) -> discord.Embed:
        state = self.player.get_state(self.guild_id)
        queue = state.queue

        embed = discord.Embed(
            title="📋 播放 Queue",
            color=discord.Color.blue(),
        )

        # Now playing row
        if state.current_song:
            s = state.current_song
            elapsed = self.player.get_progress(self.guild_id)
            paused = self.player.is_paused(self.guild_id)
            icon = "⏸" if paused else "▶️"
            progress_line = (
                "🔴 直播中 LIVE"
                if s.is_live
                else f"`[{_bar(elapsed, float(s.duration))}]`\n`{_fmt(elapsed)} / {s.duration_str}`"
            )
            embed.add_field(
                name=f"{icon} 正在播放",
                value=(
                    f"{s.source_emoji} **{s.title}**\n"
                    f"{progress_line}　👤 {s.requester}"
                ),
                inline=False,
            )

        if not queue:
            embed.add_field(name="Queue", value="Queue 是空的，使用 `/play` 新增歌曲！", inline=False)
        else:
            total_pages = max(1, (len(queue) + _PAGE_SIZE - 1) // _PAGE_SIZE)
            page = min(self.page, total_pages - 1)
            start = page * _PAGE_SIZE
            end = min(start + _PAGE_SIZE, len(queue))

            # C3: cumulative wait
            remaining = max(0.0, float(state.current_song.duration) - self.player.get_progress(self.guild_id)) if state.current_song else 0.0
            wait_before_page = remaining + sum(s.duration for s in queue[:start])

            lines = []
            for i, s in enumerate(queue[start:end], start + 1):
                lines.append(
                    f"`{i}.` {s.source_emoji} **{s.title[:40]}**\n"
                    f"　　⏱ {s.duration_str}　👤 {s.requester}　⏳ ~{_fmt(wait_before_page)}"
                )
                wait_before_page += s.duration

            total_dur = sum(s.duration for s in queue)
            embed.add_field(
                name=f"📋 Queue — 第 {page + 1}/{total_pages} 頁（共 {len(queue)} 首 · {_fmt(total_dur)}）",
                value="\n".join(lines),
                inline=False,
            )

        from services.filter_service import FILTER_LABELS
        s_ = state
        embed.set_footer(
            text=(
                f"{s_.loop_mode.emoji()} {s_.loop_mode.label()}　"
                f"🔊 {int(s_.volume * 100)}%　"
                f"🎨 {FILTER_LABELS.get(s_.audio_filter, '關閉')}　"
                f"📻 自動推薦 {'✅' if s_.auto_radio_enabled else '❌'}"
            )
        )
        self._refresh_nav()
        return embed

    # ── Navigation sync ────────────────────────────────────────────────

    def _refresh_nav(self) -> None:
        state = self.player.get_state(self.guild_id)
        total = len(state.queue)
        total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
        page = min(self.page, total_pages - 1)

        prev_btn: discord.ui.Button = self.btn_prev  # type: ignore[assignment]
        next_btn: discord.ui.Button = self.btn_next  # type: ignore[assignment]
        prev_btn.disabled = page <= 0
        next_btn.disabled = page >= total_pages - 1

        clear_btn: discord.ui.Button = self.btn_clear  # type: ignore[assignment]
        clear_btn.disabled = total == 0

    # ── Buttons ────────────────────────────────────────────────────────

    @discord.ui.button(emoji="◀", label="上一頁", style=discord.ButtonStyle.secondary, row=0)
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(emoji="🔄", label="重新整理", style=discord.ButtonStyle.secondary, row=0)
    async def btn_refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(emoji="▶", label="下一頁", style=discord.ButtonStyle.secondary, row=0)
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        state = self.player.get_state(self.guild_id)
        total_pages = max(1, (len(state.queue) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        self.page = min(total_pages - 1, self.page + 1)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(emoji="🔀", label="隨機", style=discord.ButtonStyle.secondary, row=1)
    async def btn_shuffle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        vc = self.player.get_state(self.guild_id).voice_client
        if not await check_voice(interaction, vc):
            return
        if self.player.shuffle_queue(self.guild_id):
            self.page = 0
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        else:
            await interaction.response.send_message("❌ Queue 是空的。", ephemeral=True)

    @discord.ui.button(emoji="🗑", label="清空 Queue（DJ）", style=discord.ButtonStyle.danger, row=1)
    async def btn_clear(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        vc = self.player.get_state(self.guild_id).voice_client
        if not await check_dj(interaction, vc):
            return
        state = self.player.get_state(self.guild_id)
        state.queue.clear()
        self.page = 0
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
        await interaction.followup.send("🗑️ Queue 已清空。", ephemeral=True)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]
