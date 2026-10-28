"""Demo influencers and their wallets. Keys are derived from public seeds — demo only, never fund them on mainnet."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from eth_account import Account
from eth_utils import keccak

from ..models import InfluencerProfile, RateCard


def demo_key(label: str) -> str:
    return "0x" + keccak(text=f"zyroxq-demo-key:{label}").hex()


@dataclass(frozen=True)
class DemoInfluencer:
    profile: InfluencerProfile
    private_key: str
    week_one_views: int  # views the fixture video reaches by T+7d
    persona: str  # negotiation style for the LLM


def _make(id_: str, name: str, subs: int, niche: str, fixed_min: str, cpm_min: str, preferred: str, views: int, persona: str) -> DemoInfluencer:
    key = demo_key(id_)
    return DemoInfluencer(
        profile=InfluencerProfile(
            id=id_,
            name=name,
            channel_id=f"UC{keccak(text=id_).hex()[:22]}",
            subscribers=subs,
            niche=niche,
            wallet=Account.from_key(key).address,
            rate_card=RateCard(fixed_min=Decimal(fixed_min), cpm_min=Decimal(cpm_min), preferred_fixed=Decimal(preferred)),
        ),
        private_key=key,
        week_one_views=views,
        persona=persona,
    )


DEMO_INFLUENCERS: tuple[DemoInfluencer, ...] = (
    _make("ava-fit", "Ava Fitness", 42_000, "fitness", "15", "2", "22", 9_400, "friendly, flexible on CPM, firm on fixed fee"),
    _make("raj-lifts", "Raj Lifts", 88_000, "fitness", "20", "2.5", "28", 16_200, "confident, anchors high, concedes slowly"),
    _make("mia-moves", "Mia Moves", 15_000, "fitness", "8", "1.5", "12", 4_100, "new creator, eager to close quickly"),
    _make("leo-gains", "Leo Gains", 260_000, "fitness", "60", "4", "75", 51_000, "premium creator, rarely discounts"),
    _make("zoe-cooks", "Zoe Cooks", 33_000, "food", "14", "2", "18", 7_300, "food creator"),
)


def demo_influencer(influencer_id: str) -> DemoInfluencer:
    for d in DEMO_INFLUENCERS:
        if d.profile.id == influencer_id:
            return d
    raise KeyError(influencer_id)
