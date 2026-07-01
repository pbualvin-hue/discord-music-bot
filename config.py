import os

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")

# Optional: set to a specific guild ID for instant slash command sync (dev/private use).
# Leave empty to sync globally (takes up to 1 hour to propagate).
_raw_guild_id = os.getenv("GUILD_ID", "")
GUILD_ID: int | None = int(_raw_guild_id) if _raw_guild_id.strip() else None

# Path to ffmpeg binary. Defaults to system PATH.
FFMPEG_PATH: str = os.getenv("FFMPEG_PATH", "ffmpeg")

# Seconds of no human listeners before the bot auto-disconnects.
AUTO_DISCONNECT_SECONDS: int = int(os.getenv("AUTO_DISCONNECT_SECONDS", "300"))

# Maximum number of songs allowed in a guild's queue.
MAX_QUEUE_SIZE: int = int(os.getenv("MAX_QUEUE_SIZE", "100"))

# Maximum songs imported from a single playlist.
MAX_PLAYLIST_SONGS: int = int(os.getenv("MAX_PLAYLIST_SONGS", "50"))

# Default playback volume on join (0.0–1.0). 0.2 = 20%.
DEFAULT_VOLUME: float = float(os.getenv("DEFAULT_VOLUME", "0.2"))

# Path to a Netscape-format cookies.txt file for authenticated yt-dlp requests.
# Export via browser extension "Get cookies.txt LOCALLY" while logged into YouTube.
# Leave empty to skip cookie authentication.
COOKIES_FILE: str = os.getenv("COOKIES_FILE", "")

# Optional HTTP proxy for YouTube traffic only (yt-dlp extraction + ffmpeg
# streaming). Used to route YouTube requests through a residential IP (e.g. a
# home NAS over a reverse SSH tunnel) so a datacenter host stops hitting the
# "Sign in to confirm you're not a bot" / 403 checks. Format: http://host:port
# Both yt-dlp AND ffmpeg must use it — otherwise the googlevideo URL (issued for
# the proxy's IP) gets fetched from the host IP and 403s. Leave empty to disable.
YT_PROXY: str = os.getenv("YT_PROXY", "")

# Genius API token for lyrics lookup.
# Get a free token at https://genius.com/developers → New API Client → Client Access Token
GENIUS_API_KEY: str = os.getenv("GENIUS_API_KEY", "")

# Optional: Discord channel ID where the bot posts a reminder when a newer
# yt-dlp version is available (so you remember to run update.sh). Leave empty
# to disable the check entirely.
_raw_ytdlp_ch = os.getenv("YTDLP_NOTIFY_CHANNEL_ID", "")
YTDLP_NOTIFY_CHANNEL_ID: int | None = (
    int(_raw_ytdlp_ch) if _raw_ytdlp_ch.strip() else None
)
# How often (hours) to check PyPI for a newer yt-dlp version.
YTDLP_CHECK_INTERVAL_HOURS: int = int(os.getenv("YTDLP_CHECK_INTERVAL_HOURS", "24"))

# Optional: Discord channel ID where the bot posts each ERROR in real time
# (the "error event book"). Errors are always recorded to data/errors.log and
# viewable via /errors regardless; this just mirrors them to a channel live.
_raw_err_ch = os.getenv("ERROR_LOG_CHANNEL_ID", "")
ERROR_LOG_CHANNEL_ID: int | None = int(_raw_err_ch) if _raw_err_ch.strip() else None

if not DISCORD_TOKEN:
    raise ValueError(
        "DISCORD_TOKEN is not set. "
        "Copy .env.example to .env and fill in your token."
    )
