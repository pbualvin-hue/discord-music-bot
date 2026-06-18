from __future__ import annotations

import discord

# Map of filter key → FFmpeg -af expression (None = no filter)
FILTER_AF: dict[str, str | None] = {
    "off":       None,
    "bass":      "bass=g=15",
    "nightcore": "aresample=48000,asetrate=48000*1.25",
    "slow":      "aresample=48000,asetrate=48000*0.8",
    "8d":        "apulsator=hz=0.125",
}

FILTER_LABELS: dict[str, str] = {
    "off":       "關閉",
    "bass":      "🔉 低音增強",
    "nightcore": "⚡ Nightcore",
    "slow":      "🌙 Slowed",
    "8d":        "🎧 8D 環繞",
}

ALL_FILTERS = list(FILTER_AF.keys())

# D2: Embed color per filter
FILTER_COLORS: dict[str, discord.Color] = {
    "off":       discord.Color.green(),
    "bass":      discord.Color.orange(),
    "nightcore": discord.Color.purple(),
    "slow":      discord.Color.blue(),
    "8d":        discord.Color.teal(),
}


def get_filter_color(filter_key: str) -> discord.Color:
    return FILTER_COLORS.get(filter_key, discord.Color.green())


def build_ffmpeg_options(filter_key: str) -> str:
    """Return the complete FFmpeg -options string for the given filter."""
    af = FILTER_AF.get(filter_key)
    return f"-vn -af {af}" if af else "-vn"
