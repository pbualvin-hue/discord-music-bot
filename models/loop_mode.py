from enum import Enum


class LoopMode(Enum):
    OFF = "off"
    SONG = "song"
    QUEUE = "queue"

    def next(self) -> "LoopMode":
        """Cycle: OFF → SONG → QUEUE → OFF."""
        order = [LoopMode.OFF, LoopMode.SONG, LoopMode.QUEUE]
        idx = order.index(self)
        return order[(idx + 1) % len(order)]

    def label(self) -> str:
        return {
            LoopMode.OFF: "關閉",
            LoopMode.SONG: "單曲迴圈",
            LoopMode.QUEUE: "整個 Queue 迴圈",
        }[self]

    def emoji(self) -> str:
        return {
            LoopMode.OFF: "➡️",
            LoopMode.SONG: "🔂",
            LoopMode.QUEUE: "🔁",
        }[self]
