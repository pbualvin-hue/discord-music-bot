from dataclasses import dataclass, field


@dataclass
class Song:
    title: str
    url: str
    duration: int       # seconds; 0 if unknown
    requester: str
    thumbnail: str = field(default="")   # thumbnail URL for embeds
    source: str = field(default="youtube")   # youtube | bilibili | soundcloud | spotify | twitch | radio
    is_live: bool = field(default=False)     # live stream / radio — infinite, no progress/rating

    @property
    def duration_str(self) -> str:
        if not self.duration:
            return "??:??"
        minutes, seconds = divmod(self.duration, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"
