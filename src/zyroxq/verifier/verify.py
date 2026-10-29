"""Proof-of-post checks. Deterministic: text in the video is data, never instructions."""

from __future__ import annotations

import re
from datetime import datetime

from ..models import DealTerms, ProofCheck
from .youtube import VideoInfo

# Phrases aimed at an AI reviewer. A post carrying them goes to manual review instead of auto-payout.
_INJECTION = re.compile(
    r"(ignore (all |any )?(previous|prior) instructions|as an ai|ai (reviewer|agent|verifier)"
    r"|approve (this|the|full|max)|release (the )?(funds|payment)|system prompt)",
    re.IGNORECASE,
)


def check_video(terms: DealTerms, video: VideoInfo | None, agreed_at: datetime, brand_handle: str) -> list[ProofCheck]:
    if video is None:
        return [ProofCheck(name="exists", passed=False, detail="video not found on YouTube")]

    text = f"{video.title}\n{video.description}".lower()
    missing = [t for t in [*terms.required_tags, brand_handle] if t.lower() not in text]
    injection = _INJECTION.search(video.description) or _INJECTION.search(video.title)
    checks = [
        ProofCheck(name="exists", passed=True, detail=video.video_id),
        ProofCheck(name="public", passed=video.privacy_status == "public", detail=video.privacy_status),
        ProofCheck(
            name="owned_by_deal_channel",
            passed=video.channel_id == terms.channel_id,
            detail=f"video channel {video.channel_id}, deal channel {terms.channel_id}",
        ),
        ProofCheck(
            name="published_in_window",
            passed=agreed_at <= video.published_at <= terms.publish_by,
            detail=f"published {video.published_at.isoformat()}, deadline {terms.publish_by.isoformat()}",
        ),
        ProofCheck(name="disclosure_and_brand", passed=not missing, detail="ok" if not missing else f"missing {missing}"),
        ProofCheck(
            name="no_ai_directed_instructions",
            passed=injection is None,
            detail="clean" if injection is None else f"found {injection.group(0)!r} → manual review",
        ),
    ]
    if terms.max_duration_seconds:
        length = video.duration_seconds
        checks.append(
            ProofCheck(
                name="video_length",
                passed=length is not None and terms.min_duration_seconds <= length <= terms.max_duration_seconds,
                detail=f"{'unknown' if length is None else f'{length}s'}, required {terms.min_duration_seconds}-{terms.max_duration_seconds}s",
            )
        )
    return checks


def all_passed(checks: list[ProofCheck]) -> bool:
    return bool(checks) and all(c.passed for c in checks)
