"""Domain models shared by agents, services, API and MCP tools.

Money is always `Decimal` (serialized as a string) — A2A data parts carry JSON numbers as
doubles, so amounts must never travel as numbers.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from eth_utils import keccak
from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class RateCard(BaseModel):
    fixed_min: Decimal  # floor for the fixed fee
    cpm_min: Decimal  # floor per 1,000 views
    preferred_fixed: Decimal  # opening counter-offer anchor


class InfluencerProfile(BaseModel):
    id: str
    name: str
    platform: Literal["youtube"] = "youtube"
    channel_id: str  # OAuth-verified at onboarding
    subscribers: int
    niche: str
    wallet: str  # payout address, proven by a wallet signature
    rate_card: RateCard
    # Key the influencer's agent signs DealTerms with; empty means the wallet signs directly.
    # `delegation` is the message the wallet signed to authorize that key.
    agent_signer: str = ""
    delegation: str = ""
    delegation_signature: str = ""
    avatar_url: str = ""


class ChannelInfo(BaseModel):
    channel_id: str
    title: str
    subscribers: int
    thumbnail_url: str = ""
    verified_at: datetime


class User(BaseModel):
    """A signed-in Google account. Influencers prove their YouTube channel through the same OAuth."""

    id: str
    email: str
    name: str
    picture: str = ""
    role: Literal["advertiser", "influencer"]
    created_at: datetime
    wallet: str = ""  # linked with an EIP-191 signature
    # advertiser
    company: str = ""
    brand_handle: str = ""
    # influencer
    channel: ChannelInfo | None = None
    niche: str = ""
    rate_card: RateCard | None = None
    persona: str = ""
    agent_active: bool = False
    agent_signer: str = ""
    agent_key_encrypted: str = ""  # Fernet; never leaves the server
    delegation: str = ""
    delegation_signature: str = ""

    def public(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"agent_key_encrypted"})

    def influencer_profile(self) -> InfluencerProfile | None:
        """The profile advertisers' agents see, once onboarding is complete."""
        if self.role != "influencer" or not (self.channel and self.wallet and self.rate_card and self.niche and self.agent_signer):
            return None
        return InfluencerProfile(
            id=self.id,
            name=self.channel.title,
            channel_id=self.channel.channel_id,
            subscribers=self.channel.subscribers,
            niche=self.niche,
            wallet=self.wallet,
            rate_card=self.rate_card,
            agent_signer=self.agent_signer,
            delegation=self.delegation,
            delegation_signature=self.delegation_signature,
            avatar_url=self.channel.thumbnail_url,
        )


class CampaignSpec(BaseModel):
    id: str = Field(default_factory=lambda: new_id("CMP"))
    advertiser: str
    brand_handle: str  # must appear in the video description, e.g. "@fitfuel"
    niche: str
    budget: Decimal
    max_creators: int = 3
    min_subscribers: int = 0
    max_subscribers: int = 10_000_000
    max_fixed: Decimal  # most we pay as a fixed fee per creator
    max_cpm: Decimal  # most we pay per 1,000 views
    cap_per_creator: Decimal  # escrow amount per deal (fixed + max performance)
    deliverable: Literal["youtube_short", "youtube_video"] = "youtube_short"
    publish_within_days: int = 7
    required_tags: list[str] = Field(default_factory=lambda: ["#ad"])
    brief: str = ""
    owner_id: str = ""  # advertiser user
    payer: str = ""  # advertiser wallet that signs the Payment Mandates


class Offer(BaseModel):
    deal_id: str
    campaign_id: str
    round: int
    fixed_fee: Decimal
    cpm: Decimal
    cap: Decimal
    deliverable: str
    publish_within_days: int
    required_tags: list[str]
    note: str = ""


class NegotiationReply(BaseModel):
    kind: Literal["counter", "accept", "reject"]
    offer: Offer | None = None  # the counter-offer, or the accepted offer
    terms: DealTerms | None = None  # signed terms, on accept
    reason: str = ""


class DealTerms(BaseModel):
    """What both agents agreed. The influencer signs `terms_hash()`; the hash anchors every later step."""

    deal_id: str
    campaign_id: str
    advertiser: str
    influencer_id: str
    channel_id: str
    payee: str  # influencer wallet
    asset: str = "USDC"
    chain_id: int = 133
    fixed_fee: Decimal
    cpm: Decimal
    cap: Decimal
    deliverable: str
    publish_by: datetime
    required_tags: list[str]
    min_live_hours: int = 48
    views_measured_after_days: int = 7
    review_window_hours: int = 48
    influencer_signature: str = ""

    def signing_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"influencer_signature"})

    def terms_hash(self) -> str:
        canonical = json.dumps(self.signing_payload(), sort_keys=True, separators=(",", ":"))
        return "0x" + keccak(text=canonical).hex()


class DealState(StrEnum):
    NEGOTIATING = "NEGOTIATING"
    REJECTED = "REJECTED"
    AGREED = "AGREED"
    CART_CREATED = "CART_CREATED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    BLOCKED = "BLOCKED"  # policy refused to sign
    AWAITING_CHECKOUT = "AWAITING_CHECKOUT"  # hosted mode: advertiser signs in HSP checkout
    SIGNED = "SIGNED"
    FUNDING = "FUNDING"
    FUNDED = "FUNDED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PROOF_SUBMITTED = "PROOF_SUBMITTED"
    PROOF_REJECTED = "PROOF_REJECTED"
    VERIFIED = "VERIFIED"
    FIXED_RELEASED = "FIXED_RELEASED"
    DISPUTED = "DISPUTED"
    COMPLETED = "COMPLETED"
    EXPIRED_REFUNDED = "EXPIRED_REFUNDED"


class ProofCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class Proof(BaseModel):
    video_id: str
    submitted_at: datetime
    checks: list[ProofCheck] = Field(default_factory=list)
    published_at: datetime | None = None
    views: int | None = None


class Attestation(BaseModel):
    deal_id: str
    video_id: str
    payee: str
    amount: Decimal
    kind: Literal["fixed", "performance"]
    nonce: int
    evidence_hash: str
    verifier: str
    signature: str


class Payout(BaseModel):
    kind: Literal["fixed", "performance", "refund"]
    to: str
    amount: Decimal
    tx_hash: str
    at: datetime


class Deal(BaseModel):
    id: str
    campaign_id: str
    influencer_id: str
    state: DealState = DealState.NEGOTIATING
    rounds: list[dict[str, Any]] = Field(default_factory=list)
    terms: DealTerms | None = None
    agreed_at: datetime | None = None
    cart_contents: dict[str, Any] | None = None
    cart_mandate_id: str | None = None
    payment_request_id: str | None = None
    payment_url: str | None = None
    payment_status: str | None = None
    payer: str | None = None
    mandate_draft: dict[str, Any] | None = None  # EIP-3009 authorization the payer's wallet signs
    funding_tx: str | None = None
    proof: Proof | None = None
    attestations: list[Attestation] = Field(default_factory=list)
    payouts: list[Payout] = Field(default_factory=list)
    fixed_release_due: datetime | None = None
    performance_due: datetime | None = None
    policy_violations: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class Campaign(BaseModel):
    spec: CampaignSpec
    status: Literal["planning", "negotiating", "awaiting_approval", "funding", "active", "done", "failed"] = "planning"
    created_at: datetime
    error: str = ""


class AuditEvent(BaseModel):
    at: datetime
    campaign_id: str
    deal_id: str | None = None
    actor: str  # advertiser-agent, influencer-agent:<id>, policy, hsp, verifier, escrow, human
    kind: str
    detail: dict[str, Any] = Field(default_factory=dict)


NegotiationReply.model_rebuild()
