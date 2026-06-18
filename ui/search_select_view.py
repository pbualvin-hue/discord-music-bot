from __future__ import annotations

import asyncio
from typing import Optional

import discord

from models.song import Song


class SearchSelectView(discord.ui.View):
    """Dropdown that lets the user pick one song from a search result list.

    A3: The embed caller builds will set_image() to the first result's thumbnail.

    Usage::
        view = SearchSelectView(songs)
        msg  = await interaction.followup.send(embed=..., view=view)
        song = await view.wait_for_selection()
        if song is None:
            await msg.edit(content="❌ 逾時取消", view=None, embed=None)
    """

    def __init__(self, songs: list[Song]) -> None:
        super().__init__(timeout=30)
        self.chosen_song: Optional[Song] = None
        self._songs = songs
        self._event = asyncio.Event()

        options = [
            discord.SelectOption(
                label=f"{i + 1}. {song.title[:90]}",
                description=f"⏱ {song.duration_str}" if song.duration else "⏱ 未知時長",
                value=str(i),
            )
            for i, song in enumerate(songs[:5])
        ]

        select = discord.ui.Select(
            placeholder="選擇要播放的歌曲…",
            options=options,
            min_values=1,
            max_values=1,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        idx = int(interaction.data["values"][0])
        self.chosen_song = self._songs[idx]
        self._event.set()
        await interaction.response.defer()

    async def wait_for_selection(self) -> Optional[Song]:
        try:
            await asyncio.wait_for(self._event.wait(), timeout=29.0)
            return self.chosen_song
        except asyncio.TimeoutError:
            return None

    async def on_timeout(self) -> None:
        self._event.set()
