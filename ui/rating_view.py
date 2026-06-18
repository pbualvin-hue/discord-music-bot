from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from models.song import Song


_STARS = ["1⭐", "2⭐", "3⭐", "4⭐", "5⭐"]
_STAR_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]


class RatingView(discord.ui.View):
    """Five star-rating buttons shown after a song finishes.

    Voters call record_rating themselves through the callback.
    The view stays alive for 60 s or until dismissed.
    """

    def __init__(self, guild_id: int, song: Song) -> None:
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.song = song
        self.voters: set[int] = set()

        for i in range(5):
            btn = discord.ui.Button(
                label=_STARS[i],
                style=discord.ButtonStyle.secondary,
                custom_id=f"rate_{i + 1}",
                row=0,
            )
            btn.callback = self._make_callback(i + 1)
            self.add_item(btn)

    def _make_callback(self, rating: int):
        async def _cb(interaction: discord.Interaction) -> None:
            from services.stats_service import record_rating

            if interaction.user.id in self.voters:
                await interaction.response.send_message(
                    "❌ 你已經評分過了。", ephemeral=True
                )
                return

            self.voters.add(interaction.user.id)
            await record_rating(
                self.guild_id,
                self.song.url,
                self.song.title,
                str(interaction.user.id),
                rating,
            )
            stars = "⭐" * rating
            await interaction.response.send_message(
                f"{stars} 你給了 **{self.song.title[:50]}** {rating} 顆星，謝謝！",
                ephemeral=True,
            )

        return _cb

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
