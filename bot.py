import asyncio

import discord
from discord.ext import commands, tasks

from config import (
    DISCORD_TOKEN,
    ERROR_LOG_CHANNEL_ID,
    GUILD_ID,
    YTDLP_CHECK_INTERVAL_HOURS,
    YTDLP_NOTIFY_CHANNEL_ID,
)
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

        if YTDLP_NOTIFY_CHANNEL_ID:
            self._ytdlp_update_check.change_interval(hours=YTDLP_CHECK_INTERVAL_HOURS)
            self._ytdlp_update_check.start()
            logger.info(
                "yt-dlp update check enabled (channel %s, every %sh).",
                YTDLP_NOTIFY_CHANNEL_ID, YTDLP_CHECK_INTERVAL_HOURS,
            )

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
        from config import COOKIES_FILE, YT_PROXY
        from services.youtube_service import clear_download_cache
        clear_download_cache()  # clear orphaned cache files from a previous crash
        logger.info("yt-dlp version: %s", _ytdlp.version.__version__)
        logger.info("Cookies: %s", COOKIES_FILE or "(none)")
        logger.info("YouTube proxy: %s", YT_PROXY or "(none)")
        logger.info("Bot ready: %s (ID: %s)", self.user, self.user.id)
        self._wire_error_sink()
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="/play",
            )
        )

    def _wire_error_sink(self) -> None:
        """Mirror each captured ERROR to a Discord channel in real time (opt-in)."""
        if not ERROR_LOG_CHANNEL_ID:
            return
        from utils.logger import set_error_sink
        loop = self.loop

        def _sink(text: str) -> None:
            # Called from the logging handler (possibly off the loop thread);
            # schedule the actual send on the bot loop, swallowing all errors.
            try:
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(self._post_error(text), loop)
            except Exception:
                pass

        set_error_sink(_sink)
        logger.info("Error log channel: %s", ERROR_LOG_CHANNEL_ID)

    async def _post_error(self, text: str) -> None:
        # MUST NOT log at ERROR level here (would recurse into the sink).
        try:
            channel = self.get_channel(ERROR_LOG_CHANNEL_ID)
            if channel is None:
                return
            body = text if len(text) <= 1800 else text[:1800] + " …"
            await channel.send(f"🐞 錯誤事件\n```\n{body}\n```")
        except Exception:
            pass

    @tasks.loop(hours=24)
    async def _ytdlp_update_check(self) -> None:
        """Notify the configured channel when a newer yt-dlp version is on PyPI."""
        import yt_dlp as _ytdlp
        from services.ytdlp_update_service import (
            get_latest_ytdlp_version,
            is_newer,
            load_notified,
            save_notified,
        )

        current = _ytdlp.version.__version__
        latest = await get_latest_ytdlp_version()
        if not latest:
            return
        newer = is_newer(latest, current)
        logger.info("yt-dlp version check: current=%s latest=%s newer=%s", current, latest, newer)
        if not newer:
            return
        # Persisted across restarts so a restart doesn't re-notify the same version.
        if load_notified() == latest:
            return
        channel = self.get_channel(YTDLP_NOTIFY_CHANNEL_ID)
        if channel is None:
            logger.warning("yt-dlp 通知頻道 %s 找不到。", YTDLP_NOTIFY_CHANNEL_ID)
            return
        try:
            await channel.send(
                f"🔄 **yt-dlp 有新版可用**：`{latest}`（目前 `{current}`）\n"
                "請在伺服器執行 `bash ~/discord-music-bot/scripts/update.sh` 更新，"
                "以免 YouTube 解析失敗或被限流。"
            )
            save_notified(latest)
            logger.info("Sent yt-dlp update notice: %s (current %s)", latest, current)
        except Exception as exc:
            logger.warning("yt-dlp 更新通知發送失敗：%s", exc)

    @_ytdlp_update_check.before_loop
    async def _before_ytdlp_update_check(self) -> None:
        await self.wait_until_ready()


def main() -> None:
    bot = MusicBot()
    # log_handler=None disables discord.py's default logging so our logger is sole handler.
    bot.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
