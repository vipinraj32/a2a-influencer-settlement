"""Public YouTube channel data for creator profiles: channel statistics, recent uploads with their
statistics, and top comments. YouTube Data API v3 with an API key (public data only).

Quota: every call here is a 1-unit `list` call. Recent uploads come from the channel's uploads playlist
(channels → playlistItems → videos), never from `search.list` (100 units). Responses are cached briefly.
Nothing is estimated: a statistic YouTube does not return (hidden likes, disabled comments) stays None.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from statistics import median
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, computed_field

from .youtube import parse_iso_duration

API = "https://www.googleapis.com/youtube/v3"


def parse_channel_ref(ref: str) -> tuple[str, str] | None:
    """("id", "UC…") or ("handle", "@name") from a channel id, handle or channel URL."""
    ref = ref.strip()
    if m := re.search(r"youtube\.com/channel/(UC[A-Za-z0-9_-]{6,})", ref):
        return "id", m.group(1)
    if m := re.search(r"youtube\.com/(@[A-Za-z0-9._-]{3,30})", ref):
        return "handle", m.group(1)
    if re.fullmatch(r"UC[A-Za-z0-9_-]{6,}", ref):
        return "id", ref
    if re.fullmatch(r"@[A-Za-z0-9._-]{3,30}", ref):
        return "handle", ref
    return None


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _int(stats: dict[str, Any], key: str) -> int | None:
    return int(stats[key]) if key in stats else None


class ChannelDetails(BaseModel):
    channel_id: str
    title: str
    handle: str = ""
    description: str = ""
    country: str | None = None  # as set by the creator in their channel settings
    published_at: datetime | None = None
    thumbnail_url: str = ""
    banner_url: str = ""
    subscribers: int | None = None  # None when hidden; YouTube rounds public counts
    total_views: int | None = None
    video_count: int | None = None
    topics: list[str] = []  # Wikipedia topic URLs YouTube associates with the channel
    keywords: str = ""
    uploads_playlist: str = ""
    observed_at: datetime

    @computed_field
    @property
    def url(self) -> str:
        return f"https://www.youtube.com/{self.handle}" if self.handle else f"https://www.youtube.com/channel/{self.channel_id}"


class VideoStats(BaseModel):
    video_id: str
    channel_id: str
    title: str
    description: str = ""
    published_at: datetime
    thumbnail_url: str = ""
    duration_seconds: int | None = None
    privacy_status: str = "public"
    made_for_kids: bool | None = None
    tags: list[str] = []
    views: int | None = None
    likes: int | None = None  # None when the creator hides likes
    comments: int | None = None  # None when comments are disabled
    observed_at: datetime

    @computed_field
    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @computed_field
    @property
    def engagement_rate(self) -> float | None:
        """(likes + comments) / views, only when YouTube returned all three."""
        if not self.views or self.likes is None or self.comments is None:
            return None
        return round((self.likes + self.comments) / self.views, 5)


class Comment(BaseModel):
    author: str
    text: str
    likes: int
    replies: int
    published_at: datetime | None = None


class CommentsPage(BaseModel):
    video_id: str
    enabled: bool
    comments: list[Comment] = []
    observed_at: datetime


class EngagementSummary(BaseModel):
    """Derived from the sampled recent uploads; a description of the past, not a forecast."""

    sample_size: int
    median_views: int | None = None
    mean_views: int | None = None
    mean_engagement_rate: float | None = None  # over videos where likes and comments are visible
    engagement_sample: int = 0
    uploads_per_week: float | None = None
    window_days: float | None = None
    limitations: str = (
        "Computed from the latest public uploads returned by the YouTube Data API. View counts include all traffic"
        " sources; likes and comments may be hidden. Not a forecast for a new sponsored post."
    )


def summarize(videos: list[VideoStats]) -> EngagementSummary:
    views = [v.views for v in videos if v.views is not None]
    rates = [r for v in videos if (r := v.engagement_rate) is not None]
    summary = EngagementSummary(
        sample_size=len(videos),
        median_views=int(median(views)) if views else None,
        mean_views=sum(views) // len(views) if views else None,
        mean_engagement_rate=round(sum(rates) / len(rates), 5) if rates else None,
        engagement_sample=len(rates),
    )
    if len(videos) >= 2:
        dates = sorted(v.published_at for v in videos)
        days = (dates[-1] - dates[0]).total_seconds() / 86400
        if days > 0:
            summary.window_days = round(days, 1)
            summary.uploads_per_week = round((len(videos) - 1) / days * 7, 2)
    return summary


class ChannelSource(Protocol):
    async def channel(self, ref: str) -> ChannelDetails | None: ...
    async def recent_videos(self, channel_id: str, limit: int = 12) -> list[VideoStats]: ...
    async def videos(self, video_ids: list[str]) -> list[VideoStats]: ...
    async def comments(self, video_id: str, limit: int = 20) -> CommentsPage: ...


class YouTubeChannelApi:
    """Channel, uploads and comments lookups. `now` stamps when YouTube returned each value."""

    TTL_SECONDS = 600

    def __init__(self, api_key: str, http: httpx.AsyncClient | None = None, now: Any = None) -> None:
        self.api_key = api_key
        self._http = http or httpx.AsyncClient(timeout=15)
        self._now = now or (lambda: datetime.now().astimezone())
        self._cache: dict[tuple[str, str], tuple[float, Any]] = {}

    async def _list(self, resource: str, **params: Any) -> dict[str, Any]:
        key = (resource, repr(sorted(params.items())))
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.TTL_SECONDS:
            return hit[1]
        res = await self._http.get(f"{API}/{resource}", params={**params, "key": self.api_key})
        res.raise_for_status()
        data = res.json()
        self._cache[key] = (time.monotonic(), data)
        return data

    async def channel(self, ref: str) -> ChannelDetails | None:
        parsed = parse_channel_ref(ref)
        if parsed is None:
            return None
        kind, value = parsed
        lookup = {"id": value} if kind == "id" else {"forHandle": value}
        data = await self._list("channels", part="snippet,statistics,contentDetails,brandingSettings,topicDetails", **lookup)
        items = data.get("items", [])
        if not items:
            return None
        item = items[0]
        sn, st = item.get("snippet", {}), item.get("statistics", {})
        thumbs = sn.get("thumbnails", {})
        return ChannelDetails(
            channel_id=item["id"],
            title=sn.get("title", ""),
            handle=sn.get("customUrl", ""),
            description=sn.get("description", ""),
            country=sn.get("country"),
            published_at=_ts(sn.get("publishedAt")),
            thumbnail_url=(thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", ""),
            banner_url=item.get("brandingSettings", {}).get("image", {}).get("bannerExternalUrl", ""),
            subscribers=None if st.get("hiddenSubscriberCount") else _int(st, "subscriberCount"),
            total_views=_int(st, "viewCount"),
            video_count=_int(st, "videoCount"),
            topics=item.get("topicDetails", {}).get("topicCategories", []),
            keywords=item.get("brandingSettings", {}).get("channel", {}).get("keywords", ""),
            uploads_playlist=item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", ""),
            observed_at=self._now(),
        )

    async def videos(self, video_ids: list[str]) -> list[VideoStats]:
        if not video_ids:
            return []
        data = await self._list("videos", part="snippet,statistics,contentDetails,status", id=",".join(video_ids[:50]))
        out = []
        for item in data.get("items", []):
            sn, st = item.get("snippet", {}), item.get("statistics", {})
            thumbs = sn.get("thumbnails", {})
            out.append(
                VideoStats(
                    video_id=item["id"],
                    channel_id=sn.get("channelId", ""),
                    title=sn.get("title", ""),
                    description=sn.get("description", ""),
                    published_at=_ts(sn.get("publishedAt")) or self._now(),
                    thumbnail_url=(thumbs.get("medium") or thumbs.get("default") or {}).get("url", ""),
                    duration_seconds=parse_iso_duration(item.get("contentDetails", {}).get("duration", "")),
                    privacy_status=item.get("status", {}).get("privacyStatus", "public"),
                    made_for_kids=item.get("status", {}).get("madeForKids"),
                    tags=sn.get("tags", []),
                    views=_int(st, "viewCount"),
                    likes=_int(st, "likeCount"),
                    comments=_int(st, "commentCount"),
                    observed_at=self._now(),
                )
            )
        return out

    async def recent_videos(self, channel_id: str, limit: int = 12) -> list[VideoStats]:
        details = await self.channel(channel_id)
        if details is None or not details.uploads_playlist:
            return []
        data = await self._list("playlistItems", part="contentDetails", playlistId=details.uploads_playlist, maxResults=min(max(limit, 1), 50))
        ids = [i["contentDetails"]["videoId"] for i in data.get("items", []) if "contentDetails" in i]
        found = {v.video_id: v for v in await self.videos(ids)}
        return [found[i] for i in ids if i in found]  # newest first, as the playlist lists them

    async def comments(self, video_id: str, limit: int = 20) -> CommentsPage:
        try:
            data = await self._list("commentThreads", part="snippet", videoId=video_id, maxResults=min(max(limit, 1), 100),
                                    order="relevance", textFormat="plainText")  # fmt: skip
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403 and "commentsDisabled" in e.response.text:
                return CommentsPage(video_id=video_id, enabled=False, observed_at=self._now())
            raise
        comments = []
        for item in data.get("items", []):
            top = item["snippet"]["topLevelComment"]["snippet"]
            comments.append(
                Comment(
                    author=top.get("authorDisplayName", ""),
                    text=top.get("textDisplay", ""),
                    likes=int(top.get("likeCount", 0)),
                    replies=int(item["snippet"].get("totalReplyCount", 0)),
                    published_at=_ts(top.get("publishedAt")),
                )
            )
        return CommentsPage(video_id=video_id, enabled=True, comments=comments, observed_at=self._now())


class FixtureChannelSource:
    """Test stand-in for YouTubeChannelApi."""

    def __init__(self) -> None:
        self.channels: dict[str, ChannelDetails] = {}
        self.uploads: dict[str, list[VideoStats]] = {}
        self.comment_pages: dict[str, CommentsPage] = {}

    async def channel(self, ref: str) -> ChannelDetails | None:
        parsed = parse_channel_ref(ref)
        if parsed is None:
            return None
        kind, value = parsed
        return next((c for c in self.channels.values() if (c.channel_id if kind == "id" else c.handle) == value), None)

    async def recent_videos(self, channel_id: str, limit: int = 12) -> list[VideoStats]:
        return self.uploads.get(channel_id, [])[:limit]

    async def videos(self, video_ids: list[str]) -> list[VideoStats]:
        every = {v.video_id: v for vs in self.uploads.values() for v in vs}
        return [every[i] for i in video_ids if i in every]

    async def comments(self, video_id: str, limit: int = 20) -> CommentsPage:
        page = self.comment_pages.get(video_id)
        if page is None:
            raise LookupError(video_id)
        return page.model_copy(update={"comments": page.comments[:limit]})
