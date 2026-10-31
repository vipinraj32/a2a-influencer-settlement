from __future__ import annotations

from dataclasses import dataclass

from ...agent_policy.models import InfluencerPolicy
from ...models import InfluencerProfile


@dataclass(frozen=True)
class InfluencerIdentity:
    """What an influencer's agent runs as: the public profile, the agent key that signs DealTerms
    (authorized by the payout wallet), the negotiation style the creator wrote, and their agent policy
    (None = defaults derived from the rate card)."""

    profile: InfluencerProfile
    private_key: str
    persona: str
    policy: InfluencerPolicy | None = None
    lessons: tuple[str, ...] = ()  # owner-approved feedback lessons: prompt guidance only, never limits
