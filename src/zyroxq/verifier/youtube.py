from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

import httpx
from pydantic import BaseModel

_ID_PATTERNS = (
    re.compile(r"youtube\.com/shorts/([A-Za-z0-9_-]{6,})"),
    re.compile(r"[?&]v=([A-Za-z0-9_-]{6,})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{6,})"),
)


def parse_video_id(url_or_id: str) -> str | None:
    for pattern in _ID_PATTERNS:
        if m := pattern.search(url_or_id):
            return m.group(1)
    return url_or_id if re.fullmatch(r"[A-Za-z0-9_-]{6,}", url_or_id) else None


class VideoInfo(BaseModel):
    video_id: str
    channel_id: str
    title: str
    description: str
    published_at: datetime
    privacy_status: str
    views: int
    duration_seconds: int | None = None


def parse_iso_duration(value: str) -> int | None:
    """YouTube contentDetails.duration, e.g. PT1M5S → 65."""
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value or "")
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(g or 0) for g in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


class VideoSource(Protocol):
    async def get_video(self, video_id: str) -> VideoInfo | None: ...


class YouTubeDataApi:
    """YouTube Data API v3 `videos.list` — public data, API key auth."""

    URL = "https://www.googleapis.com/youtube/v3/videos"

    def __init__(self, api_key: str, http: httpx.AsyncClient | None = None) -> None:
        self.api_key = api_key
        self._http = http or httpx.AsyncClient(timeout=15)

    async def get_video(self, video_id: str) -> VideoInfo | None:
        res = await self._http.get(self.URL, params={"part": "snippet,statistics,status,contentDetails", "id": video_id, "key": self.api_key})
        res.raise_for_status()
        items = res.json().get("items", [])
        if not items:
            return None
        item = items[0]
        return VideoInfo(
            video_id=video_id,
            channel_id=item["snippet"]["channelId"],
            title=item["snippet"]["title"],
            description=item["snippet"].get("description", ""),
            published_at=datetime.fromisoformat(item["snippet"]["publishedAt"].replace("Z", "+00:00")),
            privacy_status=item["status"]["privacyStatus"],
            views=int(item["statistics"].get("viewCount", 0)),
            duration_seconds=parse_iso_duration(item.get("contentDetails", {}).get("duration", "")),
        )


class FixtureVideoSource:
    """Test stand-in for the YouTube API. Each video's views grow linearly to `week_one_views` over 7 days."""

    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now = now
        self.videos: dict[str, VideoInfo] = {}
        self._week_one: dict[str, int] = {}

    def add(self, video: VideoInfo, week_one_views: int = 0) -> None:
        self.videos[video.video_id] = video
        self._week_one[video.video_id] = week_one_views

    def take_down(self, video_id: str) -> None:
        if v := self.videos.get(video_id):
            self.videos[video_id] = v.model_copy(update={"privacy_status": "private"})

    async def get_video(self, video_id: str) -> VideoInfo | None:
        v = self.videos.get(video_id)
        if v is None:
            return None
        progress = min(1.0, max(0.0, (self._now() - v.published_at) / timedelta(days=7)))
        return v.model_copy(update={"views": int(self._week_one[video_id] * progress)})
