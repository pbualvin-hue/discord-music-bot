import discord
from discord.ext import commands

from config import DISCORD_TOKEN, GUILD_ID
from services.music_player import MusicPlayer
from utils.logger import logger


class MusicBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.guilds = True

        super().__init__(
            command_prefix="!",  # Prefix is unused; all commands are slash commands.
            intents=intents,
            help_command=None,
        )
        self.player = MusicPlayer()

    async def setup_hook(self) -> None:
        from commands.music import setup

        await setup(self, self.player)

        if GUILD_ID:
            # Guild-specific sync is instant — recommended for private bots.
            guild_obj = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild_obj)
            await self.tree.sync(guild=guild_obj)
            logger.info("Slash commands synced to guild %s.", GUILD_ID)
        else:
            # Global sync can take up to 1 hour to propagate.
            await self.tree.sync()
            logger.info("Slash commands synced globally.")

    async def on_ready(self) -> None:
        import yt_dlp as _ytdlp
        logger.info("yt-dlp version: %s", _ytdlp.version.__version__)
        logger.info("Bot ready: %s (ID: %s)", self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="/play",
            )
        )


def main() -> None:
    bot = MusicBot()
    # log_handler=None disables discord.py's default logging so our logger is sole handler.
    bot.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
