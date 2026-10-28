import asyncio
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from zyroxq.agents.influencer.identity import InfluencerIdentity
from zyroxq.models import CampaignSpec, DealTerms, InfluencerProfile, Offer, RateCard
from zyroxq.wallet.signer import sign_terms

INFLUENCER_KEY = "0x" + "11" * 32
ADVERTISER_KEY = "0x" + "22" * 32
VERIFIER_KEY = "0x" + "33" * 32


@pytest.fixture(scope="session")
def event_loop_policy():
    # psycopg's async driver cannot use Windows' default Proactor loop
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
def campaign() -> CampaignSpec:
    return CampaignSpec(
        id="CMP-test",
        advertiser="FitFuel",
        brand_handle="@fitfuel",
        niche="fitness",
        budget=Decimal("120"),
        max_fixed=Decimal("25"),
        max_cpm=Decimal("3"),
        cap_per_creator=Decimal("40"),
    )


@pytest.fixture
def influencer() -> InfluencerProfile:
    return InfluencerProfile(
        id="inf-ava",
        name="Ava",
        channel_id="UC_ava",
        subscribers=42_000,
        niche="fitness",
        wallet=Account.from_key(INFLUENCER_KEY).address,
        rate_card=RateCard(fixed_min=Decimal("18"), cpm_min=Decimal("2"), preferred_fixed=Decimal("24")),
    )


@pytest.fixture
def offer(campaign: CampaignSpec) -> Offer:
    return Offer(
        deal_id="DEAL-1",
        campaign_id=campaign.id,
        round=1,
        fixed_fee=Decimal("20"),
        cpm=Decimal("2.5"),
        cap=campaign.cap_per_creator,
        deliverable="youtube_short",
        publish_within_days=7,
        required_tags=["#ad"],
    )


@pytest.fixture
def terms(campaign: CampaignSpec, influencer: InfluencerProfile, offer: Offer) -> DealTerms:
    raw = DealTerms(
        deal_id=offer.deal_id,
        campaign_id=campaign.id,
        advertiser=campaign.advertiser,
        influencer_id=influencer.id,
        channel_id=influencer.channel_id,
        payee=influencer.wallet,
        fixed_fee=offer.fixed_fee,
        cpm=offer.cpm,
        cap=offer.cap,
        deliverable=offer.deliverable,
        publish_by=datetime.now(UTC) + timedelta(days=7),
        required_tags=offer.required_tags,
    )
    return sign_terms(INFLUENCER_KEY, raw)


AGENT_KEY = "0x" + "44" * 32


@pytest.fixture
def identity(influencer: InfluencerProfile) -> InfluencerIdentity:
    """The influencer's agent: its own key, authorized by the payout wallet's signature."""
    agent = Account.from_key(AGENT_KEY).address
    delegation = f"ZyroXQ wallet link\nChannel: {influencer.channel_id}\nAgent signer: {agent}"
    signature = Account.sign_message(encode_defunct(text=delegation), INFLUENCER_KEY).signature.hex()
    profile = influencer.model_copy(update={"agent_signer": agent, "delegation": delegation, "delegation_signature": "0x" + signature.removeprefix("0x")})
    return InfluencerIdentity(profile, AGENT_KEY, "friendly, flexible on CPM")
