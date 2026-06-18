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

# Genius API token for lyrics lookup.
# Get a free token at https://genius.com/developers → New API Client → Client Access Token
GENIUS_API_KEY: str = os.getenv("GENIUS_API_KEY", "")

if not DISCORD_TOKEN:
    raise ValueError(
        "DISCORD_TOKEN is not set. "
        "Copy .env.example to .env and fill in your token."
    )
