"""Influencer directory, read from the user store: every creator who finished onboarding (Google +
YouTube channel, wallet signature, rate card) and switched their agent on."""

from __future__ import annotations

from dataclasses import dataclass

from ..agent_policy.models import InfluencerPolicy
from ..agents.influencer.identity import InfluencerIdentity
from ..auth.crypto import decrypt_agent_key
from ..feedback import lessons_for
from ..models import InfluencerProfile, User
from ..policy import delegation_valid
from ..store import Store


@dataclass
class Registration:
    profile: InfluencerProfile
    agent_url: str


POLICY_KIND = "agent_policy"


class InfluencerRegistry:
    def __init__(self, store: Store, base_url: str, secret: str) -> None:
        self.store = store
        self.base_url = base_url.rstrip("/")
        self.secret = secret

    def agent_url(self, influencer_id: str) -> str:
        return f"{self.base_url}/a2a/influencers/{influencer_id}/"

    @staticmethod
    def _listed(user: User | None) -> InfluencerProfile | None:
        profile = user.influencer_profile() if user and user.agent_active else None
        return profile if profile and delegation_valid(profile) else None

    # The directory is what the advertiser side reads: the creator's floors (rate card) are stripped
    async def all(self) -> list[Registration]:
        users = await self.store.list_users("influencer")
        return [Registration(public_profile(p), self.agent_url(p.id)) for u in users if (p := self._listed(u))]

    async def get(self, influencer_id: str) -> Registration | None:
        profile = self._listed(await self.store.get_user(influencer_id))
        return Registration(public_profile(profile), self.agent_url(profile.id)) if profile else None

    async def identity(self, influencer_id: str) -> InfluencerIdentity | None:
        user = await self.store.get_user(influencer_id)
        profile = self._listed(user)
        if profile is None or user is None:
            return None
        doc = await self.store.get_version(POLICY_KIND, user.id)
        policy = InfluencerPolicy.model_validate(doc) if doc and doc.get("role") == "influencer" else None
        persona = policy.persona if policy else user.persona
        lessons = tuple(x["guidance"] for x in await lessons_for(self.store, user.id, "influencer", niche=profile.niche))
        return InfluencerIdentity(profile, decrypt_agent_key(self.secret, user.agent_key_encrypted), persona, policy, lessons)


def public_profile(profile: InfluencerProfile) -> InfluencerProfile:
    return profile.model_copy(update={"rate_card": None})
