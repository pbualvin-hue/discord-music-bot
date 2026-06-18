"""Playlist serialization helpers.

Converts between list[Song] and JSON for SQLite storage.
"""

from __future__ import annotations

import json

from models.song import Song


def songs_to_json(songs: list[Song]) -> str:
    return json.dumps([
        {
            "title": s.title,
            "url": s.url,
            "duration": s.duration,
            "requester": s.requester,
            "thumbnail": s.thumbnail,
        }
        for s in songs
    ], ensure_ascii=False)


def json_to_songs(raw: str, requester: str) -> list[Song]:
    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [
        Song(
            title=item.get("title", "Unknown"),
            url=item["url"],
            duration=int(item.get("duration") or 0),
            requester=requester,
            thumbnail=item.get("thumbnail", ""),
        )
        for item in items
        if item.get("url")
    ]
