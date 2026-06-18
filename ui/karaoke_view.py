"""L1: KTV karaoke view — close button only; the embed is updated by a background task."""

from __future__ import annotations

import discord


class KaraokeView(discord.ui.View):
    """Minimal view attached to the KTV embed; just provides a close button."""

    def __init__(self, guild_id: int) -> None:
        super().__init__(timeout=None)   # task manages lifetime
        self.guild_id = guild_id
        self.closed = False

    @discord.ui.button(emoji="❌", label="關閉 KTV", style=discord.ButtonStyle.danger, row=0)
    async def btn_close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.closed = True
        self.stop()
        try:
            await interaction.message.delete()
        except Exception:
            await interaction.response.defer()
