"""I: Multi-page lyrics reader with ◀ / ▶ navigation buttons."""

from __future__ import annotations

import discord

from models.song import Song


class LyricsPagedView(discord.ui.View):
    def __init__(self, song: Song, chunks: list[str]) -> None:
        super().__init__(timeout=120)
        self.song = song
        self.chunks = chunks
        self.page = 0
        self._refresh_buttons()

    def build_embed(self) -> discord.Embed:
        if not self.chunks:
            return discord.Embed(
                title=f"📝 {self.song.title}",
                description="（找不到歌詞）",
                color=discord.Color.yellow(),
            )
        total = len(self.chunks)
        embed = discord.Embed(
            title=f"📝 {self.song.title}",
            description=self.chunks[self.page],
            color=discord.Color.yellow(),
        )
        if self.song.thumbnail:
            embed.set_thumbnail(url=self.song.thumbnail)
        embed.set_footer(text=f"第 {self.page + 1} / {total} 頁　— 資料來源：Genius")
        return embed

    def _refresh_buttons(self) -> None:
        total = len(self.chunks)
        prev: discord.ui.Button = self.btn_prev  # type: ignore[assignment]
        nxt: discord.ui.Button = self.btn_next   # type: ignore[assignment]
        prev.disabled = self.page <= 0
        nxt.disabled = total == 0 or self.page >= total - 1

    @discord.ui.button(emoji="◀", label="上一頁", style=discord.ButtonStyle.secondary, row=0)
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(emoji="▶", label="下一頁", style=discord.ButtonStyle.secondary, row=0)
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = min(len(self.chunks) - 1, self.page + 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(emoji="❌", label="關閉", style=discord.ButtonStyle.danger, row=0)
    async def btn_close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.message.delete()

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]
