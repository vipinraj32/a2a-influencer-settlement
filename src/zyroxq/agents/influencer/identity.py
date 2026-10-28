from __future__ import annotations

from dataclasses import dataclass

from ...models import InfluencerProfile


@dataclass(frozen=True)
class InfluencerIdentity:
    """What an influencer's agent runs as: the public profile, the agent key that signs DealTerms
    (authorized by the payout wallet), and the negotiation style the creator wrote."""

    profile: InfluencerProfile
    private_key: str
    persona: str
