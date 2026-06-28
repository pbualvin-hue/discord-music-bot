import asyncio
import json
import urllib.request
from typing import Optional

from utils.logger import logger

_PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"


async def get_latest_ytdlp_version() -> Optional[str]:
    """Return the latest yt-dlp version string on PyPI, or None on failure."""
    loop = asyncio.get_running_loop()

    def _fetch() -> str:
        req = urllib.request.Request(_PYPI_URL, headers={"User-Agent": "discord-music-bot"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
        return data["info"]["version"]

    try:
        return await loop.run_in_executor(None, _fetch)
    except Exception as exc:
        logger.warning("yt-dlp 最新版查詢失敗：%s", exc)
        return None


def is_newer(latest: str, current: str) -> bool:
    """True if *latest* is a newer version than *current*."""
    try:
        from packaging.version import parse
        return parse(latest) > parse(current)
    except Exception:
        # yt-dlp versions are date-based (YYYY.MM.DD) — string compare is chronological.
        return latest > current
