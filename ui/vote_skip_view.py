from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from services.music_player import MusicPlayer


class VoteSkipView(discord.ui.View):
    """Single-button view for collecting skip votes.

    The initiator's vote is counted automatically on creation.
    When *required* votes are reached the current song is skipped.
    """

    def __init__(
        self,
        player: MusicPlayer,
        guild_id: int,
        required: int,
        initiator_id: int,
        song_title: str,
    ) -> None:
        super().__init__(timeout=30)
        self.player = player
        self.guild_id = guild_id
        self.required = required
        self.song_title = song_title
        self.voters: set[int] = {initiator_id}
        self.skipped = False
        self._update_button()

    def _update_button(self) -> None:
        btn: discord.ui.Button = self.vote_button  # type: ignore[assignment]
        count = len(self.voters)
        btn.label = f"✅ 同意跳過 ({count}/{self.required})"

    @discord.ui.button(label="✅ 同意跳過 (1/?)", style=discord.ButtonStyle.success)
    async def vote_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id in self.voters:
            await interaction.response.send_message("你已經投票了。", ephemeral=True)
            return

        self.voters.add(interaction.user.id)
        count = len(self.voters)

        if count >= self.required:
            self.skipped = True
            self.player.skip(self.guild_id)
            button.label = "✅ 跳過成功！"
            button.disabled = True
            self.stop()
            await interaction.response.edit_message(
                content=f"⏭️ 投票通過（{count}/{self.required} 票）— 已跳過 **{self.song_title}**",
                view=self,
            )
        else:
            self._update_button()
            await interaction.response.edit_message(view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]
