"""Creator evidence: every metric carries its source, when it was observed and whether it is verified.
Unknown stays unknown — nothing here estimates demographics, engagement or audience quality it cannot see."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from ..models import User
from .models import InfluencerPolicy

Source = Literal["youtube_data_api", "self_reported", "derived_estimate", "unavailable"]


class EvidenceItem(BaseModel):
    metric: str
    value: Any = None  # None when unavailable
    source: Source
    verified: bool
    observed_at: datetime | None = None
    limitations: str = ""


def creator_evidence(user: User, policy: InfluencerPolicy | None) -> list[EvidenceItem]:
    ch = user.channel
    items: list[EvidenceItem] = []
    if ch is None:
        return [EvidenceItem(metric="channel", source="unavailable", verified=False, limitations="no YouTube channel connected")]
    api = {"source": "youtube_data_api", "verified": True, "observed_at": ch.verified_at}
    items.append(EvidenceItem(metric="channel_id", value=ch.channel_id, limitations="proved by the creator's Google sign-in", **api))
    if ch.subscribers_hidden:
        items.append(EvidenceItem(metric="subscribers", source="unavailable", verified=False, limitations="the creator hides the subscriber count"))
    else:
        items.append(EvidenceItem(metric="subscribers", value=ch.subscribers, limitations="YouTube rounds public subscriber counts", **api))
    for metric, value in (("total_views", ch.view_count), ("public_videos", ch.video_count)):
        if value is None:
            items.append(EvidenceItem(metric=metric, source="unavailable", verified=False, limitations="not returned at sign-in; reconnect to refresh"))
        else:
            items.append(EvidenceItem(metric=metric, value=value, **api))
    if ch.view_count is not None and ch.video_count:
        items.append(
            EvidenceItem(
                metric="average_views_per_video",
                value=ch.view_count // ch.video_count,
                source="derived_estimate",
                verified=False,
                observed_at=ch.verified_at,
                limitations="lifetime views ÷ public videos; not a forecast for a new Short",
            )
        )
    else:
        items.append(EvidenceItem(metric="average_views_per_video", source="unavailable", verified=False))
    countries = policy.creator.audience_countries if policy else []
    if countries:
        items.append(EvidenceItem(metric="audience_countries", value=countries, source="self_reported", verified=False,
                                  limitations="stated by the creator; ZyroXQ has no YouTube Analytics access"))  # fmt: skip
    else:
        items.append(EvidenceItem(metric="audience_countries", source="unavailable", verified=False, limitations="needs YouTube Analytics access"))
    for metric in ("audience_age_gender", "engagement_rate"):
        items.append(EvidenceItem(metric=metric, source="unavailable", verified=False, limitations="needs YouTube Analytics access"))
    return items


def verified_value(items: list[EvidenceItem], metric: str) -> Any:
    """The value only if an API verified it; None otherwise."""
    item = next((i for i in items if i.metric == metric), None)
    return item.value if item and item.verified else None


def evidence_quality(items: list[EvidenceItem]) -> float:
    """Share of the tracked metrics that are verified. Self-reported and estimates count for nothing here."""
    return sum(1 for i in items if i.verified) / len(items) if items else 0.0
