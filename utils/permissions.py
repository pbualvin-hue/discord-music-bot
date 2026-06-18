"""Shared permission helpers used by all UI views and command handlers."""

from __future__ import annotations

import discord


def is_dj(member: discord.Member) -> bool:
    """True if member has administrator/manage_guild permission OR a role named 'DJ' (case-insensitive)."""
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return True
    return any(r.name.lower() == "dj" for r in member.roles)


def in_voice_with_bot(member: discord.Member, voice_client: discord.VoiceClient | None) -> bool:
    """True if the member is in the same voice channel as the bot."""
    if not voice_client or not voice_client.is_connected():
        return False
    return member.voice is not None and member.voice.channel == voice_client.channel


async def check_voice(
    interaction: discord.Interaction,
    voice_client: discord.VoiceClient | None,
) -> bool:
    """Send an ephemeral error and return False if the user is not in the bot's channel."""
    if not in_voice_with_bot(interaction.user, voice_client):  # type: ignore[arg-type]
        await interaction.response.send_message(
            "❌ 你必須在與 Bot 相同的語音頻道才能操作。", ephemeral=True
        )
        return False
    return True


async def check_dj(
    interaction: discord.Interaction,
    voice_client: discord.VoiceClient | None,
) -> bool:
    """check_voice + DJ/admin check. Sends ephemeral error on failure."""
    if not await check_voice(interaction, voice_client):
        return False
    if not is_dj(interaction.user):  # type: ignore[arg-type]
        await interaction.response.send_message(
            "❌ 此操作需要 **DJ** 身份組或管理員權限。", ephemeral=True
        )
        return False
    return True
