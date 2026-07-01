import logging
import logging.handlers
import sys
from collections import deque
from pathlib import Path
from typing import Callable, Optional

# ── Error event book ───────────────────────────────────────────────────
# Every ERROR-level log is captured (a) into a persistent rotating file so you
# can review past errors without re-running journalctl, and (b) into an
# in-memory ring buffer that the /errors command reads, and (c) optionally
# pushed to a Discord channel in real time via a registered sink.
ERROR_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "errors.log"
ERROR_EVENTS: deque = deque(maxlen=100)
_SINK: Optional[Callable[[str], None]] = None


def set_error_sink(fn: Optional[Callable[[str], None]]) -> None:
    """Register a callback(text) invoked for each captured ERROR (e.g. post to Discord)."""
    global _SINK
    _SINK = fn


def get_recent_errors(n: int = 10) -> list[str]:
    return list(ERROR_EVENTS)[-n:]


def clear_errors() -> None:
    ERROR_EVENTS.clear()


class _ErrorBook(logging.Handler):
    """Capture ERROR+ records into the ring buffer and (optionally) a live sink."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            ERROR_EVENTS.append(msg)
            if _SINK is not None:
                try:
                    _SINK(msg)
                except Exception:
                    pass  # a failing sink must never break logging
        except Exception:
            pass


def setup_logger(name: str = "music_bot") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "[%(asctime)s] [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    handler.setFormatter(fmt)
    logger.addHandler(handler)

    # Persistent error file (rotating, ERROR+ only)
    try:
        ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_h = logging.handlers.RotatingFileHandler(
            ERROR_LOG_PATH, maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
        )
        file_h.setLevel(logging.ERROR)
        file_h.setFormatter(fmt)
        logger.addHandler(file_h)
    except Exception:
        pass

    # In-memory buffer + live sink (ERROR+ only)
    book = _ErrorBook()
    book.setLevel(logging.ERROR)
    book.setFormatter(fmt)
    logger.addHandler(book)

    return logger


logger = setup_logger()
