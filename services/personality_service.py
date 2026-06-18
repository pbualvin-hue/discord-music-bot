"""Personality, greeting, and seasonal decoration service.

E1: Genre-aware play responses
E2: Random join greetings
E4: Seasonal embed decorations
"""

from __future__ import annotations

import random
from datetime import date

# ── E4: Seasonal decorations ──────────────────────────────────────────

_SEASONS: list[tuple[tuple[int, int], tuple[int, int], str, str]] = [
    # (start_md, end_md, label, decorator)
    ((1, 1),   (1, 3),   "元旦",   "🎆 新年快樂！"),
    ((2, 10),  (2, 17),  "農曆新年", "🧧 新春大吉！恭喜發財！"),
    ((2, 14),  (2, 14),  "情人節",  "💕 情人節快樂！"),
    ((4, 4),   (4, 5),   "清明",   "🌸 清明時節"),
    ((6, 1),   (6, 7),   "端午",   "🐉 端午節快樂！"),
    ((9, 1),   (9, 7),   "中秋",   "🥮 中秋節快樂！"),
    ((10, 31), (10, 31), "萬聖節",  "🎃 Trick or Treat！"),
    ((12, 24), (12, 26), "聖誕節",  "🎄 Merry Christmas！"),
    ((12, 31), (12, 31), "跨年",   "🎊 跨年快樂！"),
]


def get_seasonal_decoration() -> str:
    """Return a seasonal label if today matches a festival, else empty string."""
    today = date.today()
    md = (today.month, today.day)
    for start, end, _, deco in _SEASONS:
        if start <= md <= end:
            return deco
    return ""


# ── E1: Genre detection & personality responses ───────────────────────

_GENRE_KEYWORDS: dict[str, list[str]] = {
    "classical": [
        "piano", "violin", "symphony", "orchestra", "concerto", "sonata",
        "beethoven", "mozart", "bach", "chopin", "schubert", "debussy",
        "古典", "交響", "協奏", "奏鳴",
    ],
    "edm": [
        "edm", "dubstep", "trap", "techno", "house", "trance", "rave",
        "remix", "bass", "drop", "dj ", "nightcore",
        "電音", "舞曲", "夜店",
    ],
    "ballad": [
        "love", "heart", "miss", "sad", "cry", "alone",
        "愛情", "情歌", "思念", "想你", "分手", "傷心", "眼淚", "失戀",
    ],
    "anime": [
        " op", " ed", "ost", "opening", "ending",
        "動漫", "アニメ", "ガンダム", "鬼滅", "進擊", "咒術",
    ],
}

_RESPONSES: dict[str, list[str]] = {
    "classical": [
        "🎻 優雅的選擇，讓心靈沉澱一下。",
        "🎹 古典的力量，超越時代。",
        "🎼 閉上眼睛，感受音樂的流動…",
    ],
    "edm": [
        "⚡ 耳機戴好！重低音來了！",
        "🔥 把音量拉滿，今晚派對不停！",
        "🎧 Drop 要來了，準備起飛！",
    ],
    "ballad": [
        "💕 這首歌…是在想某個人嗎？",
        "🌙 深夜情歌，最適合現在。",
        "😢 有些心情，只有音樂懂。",
    ],
    "anime": [
        "⚔️ 熱血動漫 BGM！戰鬥力爆表！",
        "✨ 聽到這首就想起那個名場面…",
        "🌸 動漫 OST 永遠是最強的！",
    ],
    "default": [
        "🎵 好品味！",
        "🎶 來一首好歌，心情也跟著好起來！",
        "🎸 音樂響起，世界暫停一下。",
        "▶️ 播了！",
        "🔊 享受音樂吧！",
    ],
}


def detect_genre(title: str) -> str:
    lower = title.lower()
    for genre, keywords in _GENRE_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return genre
    return "default"


def get_play_response(title: str, requester: str) -> str:
    genre = detect_genre(title)
    return random.choice(_RESPONSES[genre])


# ── E2: Join greetings ────────────────────────────────────────────────

_GREETINGS = [
    "🎵 準備好搖擺了嗎？",
    "🎶 DJ 在線，派對開始！",
    "🎼 音樂時間到，讓我來接管！",
    "🎸 插電！讓音樂說話！",
    "🎹 今天想聽什麼？",
    "🔊 揚聲器就位，隨時待命！",
    "🎧 耳機插好了嗎？我來了！",
    "🎤 Mic check, 1, 2, 3… 開始！",
]


def get_join_greeting() -> str:
    return random.choice(_GREETINGS)


# ── E3: Achievement labels ─────────────────────────────────────────────

_ACHIEVEMENT_LABELS: dict[int, tuple[str, str]] = {
    10:   ("🌱 初學者",  "恭喜點播達 **10** 首！音樂之旅正式開始！"),
    50:   ("🎵 音樂愛好者", "恭喜點播達 **50** 首！品味越來越好了！"),
    100:  ("🎸 搖滾達人",  "恭喜點播達 **100** 首！真正的音樂迷！"),
    500:  ("🏆 傳奇 DJ",  "恭喜點播達 **500** 首！你就是這個伺服器的傳說！"),
    1000: ("👑 音樂之神",  "恭喜點播達 **1000** 首！無人能及，封神！"),
}


def get_achievement_text(milestone: int) -> tuple[str, str]:
    return _ACHIEVEMENT_LABELS.get(milestone, ("🎖️ 成就", f"點播達 **{milestone}** 首！"))
