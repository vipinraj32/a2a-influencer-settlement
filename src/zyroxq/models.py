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


# Version of the payable-amount rules (agent_policy.pricing). Signed into DealTerms, so a later rule change
# never re-prices an agreed deal.
PRICING_VERSION = "pricing-2026.10"
DEFAULT_MAX_ROUNDS = 4  # offers the advertiser may send: opening + up to 3 responses to counter-offers
CompensationModel = Literal["fixed", "cpm", "hybrid"]


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
    niche: str  # primary niche
    niches: list[str] = Field(default_factory=list)  # every niche the creator confirmed, primary first
    wallet: str  # payout address, proven by a wallet signature
    # Private to the creator's own agent. The directory the advertiser side reads strips it (registry.public_profile).
    rate_card: RateCard | None = None
    # Key the influencer's agent signs DealTerms with; empty means the wallet signs directly.
    # `delegation` is the message the wallet signed to authorize that key.
    agent_signer: str = ""
    delegation: str = ""
    delegation_signature: str = ""
    avatar_url: str = ""

    @property
    def all_niches(self) -> list[str]:
        return [n.lower() for n in (self.niches or [self.niche])]


class ChannelInfo(BaseModel):
    channel_id: str
    title: str
    subscribers: int
    thumbnail_url: str = ""
    verified_at: datetime  # when the YouTube Data API returned these statistics
    # Channel statistics from the same response; None when YouTube did not return them
    view_count: int | None = None
    video_count: int | None = None
    subscribers_hidden: bool = False  # the creator hides the subscriber count: treat it as unknown


class NicheSuggestion(BaseModel):
    niche: str
    score: float  # 0.8 × share of recent uploads matching + 0.2 when the channel's topics/keywords match
    video_share: float
    matched_videos: int
    keywords: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)  # titles of matching uploads
    channel_signal: bool = False
    llm_confidence: float | None = None  # set when an LLM ranked it (from the same fixed taxonomy)
    reason: str = ""


class NicheAnalysis(BaseModel):
    """A suggestion derived from public YouTube data. The creator confirms or edits it; it is never applied silently."""

    channel_id: str
    analyzed_at: datetime
    videos_analyzed: int
    method: Literal["rules", "llm+rules"]
    suggestions: list[NicheSuggestion] = Field(default_factory=list)
    note: str = ""


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
    niche: str = ""  # primary niche (= niches[0])
    niches: list[str] = Field(default_factory=list)
    niche_source: Literal["", "youtube_analysis", "manual"] = ""  # analysis = the creator kept only suggested niches
    niche_analysis: NicheAnalysis | None = None
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
            niches=self.niches or [self.niche],
            wallet=self.wallet,
            rate_card=self.rate_card,
            agent_signer=self.agent_signer,
            delegation=self.delegation,
            delegation_signature=self.delegation_signature,
            avatar_url=self.channel.thumbnail_url,
        )


class ContentScope(BaseModel):
    """What the advertiser may do with the content, beyond one organic post. Part of the signed terms."""

    usage_rights: Literal["organic_only", "brand_repost", "paid_ads"] = "organic_only"
    license_days: int = Field(default=0, ge=0, le=730)  # how long the brand may reuse the content
    paid_amplification: bool = False  # brand may boost the post with paid media
    exclusivity_days: int = Field(default=0, ge=0, le=365)  # no competing sponsors in `exclusivity_category`
    exclusivity_category: str = Field(default="", max_length=60)
    revisions: int = Field(default=1, ge=0, le=5)  # rounds of changes the brand may request before publishing
    deliverable_count: int = Field(default=1, ge=1, le=1)  # one verifiable post per deal for now


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
    brief: str = ""  # one-paragraph summary
    # Content requirements: video length and the full creative brief (up to ~3 pages).
    # Influencers sign `requirements_hash()`, so the document they agreed to cannot change afterwards.
    min_duration_seconds: int = 0
    max_duration_seconds: int = 0  # 0 = no length rule
    requirements: str = ""
    owner_id: str = ""  # advertiser user
    payer: str = ""  # advertiser wallet that signs the Payment Mandates
    # Commercial structure: fixed (cap = fixed fee), cpm (no fixed fee) or hybrid (fixed + CPM up to the cap)
    compensation_model: CompensationModel = "hybrid"
    scope: ContentScope = Field(default_factory=ContentScope)
    category: str = ""  # brand category, matched against creators' forbidden categories
    # Sponsorship-disclosure rules, snapshotted as "<jurisdiction>@<version>" when the campaign is created
    jurisdictions: list[str] = Field(default_factory=lambda: ["IN", "US"])
    disclosure_policies: list[str] = Field(default_factory=list)
    max_rounds: int = DEFAULT_MAX_ROUNDS
    review_required: bool = False  # advertiser reviews disclosure compliance before the fixed fee is released
    agent_policy_version: int = 0  # advertiser agent policy in force when the campaign started (0 = defaults)


def requirements_hash(text: str) -> str:
    """keccak of the content brief; empty when the campaign has none."""
    return "0x" + keccak(text=text).hex() if text else ""


class Offer(BaseModel):
    deal_id: str
    campaign_id: str
    round: int
    proposer: Literal["advertiser", "influencer"] = "advertiser"
    max_rounds: int = DEFAULT_MAX_ROUNDS
    compensation_model: CompensationModel = "hybrid"
    fixed_fee: Decimal
    cpm: Decimal
    cap: Decimal
    deliverable: str
    publish_within_days: int
    required_tags: list[str]
    min_duration_seconds: int = 0
    max_duration_seconds: int = 0
    requirements_hash: str = ""
    scope: ContentScope = Field(default_factory=ContentScope)
    category: str = ""
    disclosure_policies: list[str] = Field(default_factory=list)
    pricing_version: str = PRICING_VERSION
    note: str = ""

    @property
    def key(self) -> str:
        """Stable id of this offer within its negotiation, used by decision records."""
        return f"{self.deal_id}:r{self.round}:{self.proposer}"


class NegotiationReply(BaseModel):
    # clarify: the creator's agent needs something before it can decide; escalate: a human must decide
    kind: Literal["counter", "accept", "reject", "clarify", "escalate"]
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
    compensation_model: CompensationModel = "hybrid"
    pricing_version: str = PRICING_VERSION
    fixed_fee: Decimal
    cpm: Decimal
    cap: Decimal
    deliverable: str
    publish_by: datetime
    required_tags: list[str]
    min_duration_seconds: int = 0
    max_duration_seconds: int = 0
    requirements_hash: str = ""  # keccak of the campaign's requirements document
    scope: ContentScope = Field(default_factory=ContentScope)
    disclosure_policies: list[str] = Field(default_factory=list)  # "<jurisdiction>@<version>" the post must satisfy
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
    ESCALATED = "ESCALATED"  # an agent's policy handed the decision to a human; nothing was agreed or reserved
    AGREED = "AGREED"
    CART_CREATED = "CART_CREATED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"  # waiting for the advertiser's wallet signature
    BLOCKED = "BLOCKED"  # policy refused to sign
    AWAITING_CHECKOUT = "AWAITING_CHECKOUT"  # hosted mode: advertiser signs in HSP checkout
    SIGNED = "SIGNED"  # payment submitted to HSP, no settlement evidence yet
    FUNDING = "FUNDING"  # HSP reports the transfer in progress
    PAYMENT_UNKNOWN = "PAYMENT_UNKNOWN"  # submission outcome or settlement evidence ambiguous: reconcile, never re-sign
    FUNDED = "FUNDED"  # settlement verified on chain (or in the simulated ledger) and bound in escrow
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_EXPIRED = "PAYMENT_EXPIRED"  # authorization expired before it was submitted
    PROOF_SUBMITTED = "PROOF_SUBMITTED"
    PROOF_REJECTED = "PROOF_REJECTED"
    VERIFIED = "VERIFIED"
    FIXED_RELEASED = "FIXED_RELEASED"
    DISPUTED = "DISPUTED"
    COMPLETED = "COMPLETED"
    EXPIRED_REFUNDED = "EXPIRED_REFUNDED"


# Every state change goes through DealService.save, which refuses anything not listed here.
DEAL_TRANSITIONS: dict[DealState, frozenset[DealState]] = {
    k: frozenset(v)
    for k, v in {
        DealState.NEGOTIATING: {DealState.AGREED, DealState.REJECTED, DealState.ESCALATED},
        DealState.ESCALATED: {DealState.REJECTED},
        DealState.AGREED: {DealState.CART_CREATED, DealState.REJECTED},
        DealState.CART_CREATED: {DealState.AWAITING_APPROVAL, DealState.BLOCKED, DealState.REJECTED},
        DealState.AWAITING_APPROVAL: {
            DealState.SIGNED, DealState.AWAITING_CHECKOUT, DealState.PAYMENT_FAILED, DealState.PAYMENT_EXPIRED, DealState.REJECTED,
        },
        DealState.AWAITING_CHECKOUT: {DealState.FUNDING, DealState.FUNDED, DealState.PAYMENT_FAILED, DealState.PAYMENT_UNKNOWN},
        DealState.SIGNED: {DealState.FUNDING, DealState.FUNDED, DealState.PAYMENT_FAILED, DealState.PAYMENT_UNKNOWN},
        DealState.FUNDING: {DealState.FUNDED, DealState.PAYMENT_FAILED, DealState.PAYMENT_UNKNOWN},
        DealState.PAYMENT_UNKNOWN: {DealState.FUNDING, DealState.FUNDED, DealState.PAYMENT_FAILED},
        DealState.FUNDED: {DealState.PROOF_SUBMITTED, DealState.PROOF_REJECTED, DealState.VERIFIED, DealState.EXPIRED_REFUNDED},
        DealState.PROOF_SUBMITTED: {DealState.PROOF_REJECTED, DealState.VERIFIED},
        DealState.PROOF_REJECTED: {DealState.PROOF_SUBMITTED, DealState.VERIFIED, DealState.EXPIRED_REFUNDED},
        DealState.VERIFIED: {DealState.FIXED_RELEASED, DealState.PROOF_REJECTED, DealState.DISPUTED},
        DealState.FIXED_RELEASED: {DealState.COMPLETED},
    }.items()
}  # fmt: skip


def transition_allowed(current: DealState, new: DealState) -> bool:
    return current == new or new in DEAL_TRANSITIONS.get(current, frozenset())


class PaymentAttempt(BaseModel):
    """One submission of a signed authorization to the gateway. `idempotency_key` is the EIP-3009 nonce:
    the token contract accepts it once, so the same attempt can be looked up but never re-signed blindly."""

    idempotency_key: str
    payer: str
    to: str
    value_units: int
    valid_before: int
    status: Literal["submitted", "pending", "succeeded", "failed", "unknown"] = "submitted"
    gateway: str
    simulated: bool
    tx_hash: str | None = None
    error: str = ""
    created_at: datetime
    updated_at: datetime


class Approval(BaseModel):
    """A human decision bound to the exact terms it approved."""

    kind: Literal["payment", "compliance_review"]
    by: str  # user id
    approved: bool
    terms_hash: str
    note: str = ""
    at: datetime


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
    # The creator's own statements about what the API cannot see (e.g. an on-screen disclosure). Recorded, not verified.
    attestations: dict[str, bool] = Field(default_factory=dict)


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
    simulated: bool = False  # True: an in-process ledger entry, no token moved


class Deal(BaseModel):
    id: str
    campaign_id: str
    influencer_id: str
    state: DealState = DealState.NEGOTIATING
    rounds: list[dict[str, Any]] = Field(default_factory=list)
    negotiation_outcome: str = ""  # why negotiation ended (agreed, round limit, no movement, escalated, ...)
    terms: DealTerms | None = None
    approved_terms_hash: str | None = None  # terms hash the payment authorization was drafted for
    approvals: list[Approval] = Field(default_factory=list)
    agreed_at: datetime | None = None
    cart_contents: dict[str, Any] | None = None
    cart_mandate_id: str | None = None
    payment_request_id: str | None = None
    payment_url: str | None = None
    payment_status: str | None = None
    payer: str | None = None
    mandate_draft: dict[str, Any] | None = None  # EIP-3009 authorization the payer's wallet signs
    payment_attempts: list[PaymentAttempt] = Field(default_factory=list)
    funding_tx: str | None = None  # set only once settlement evidence was verified and bound in escrow
    funding_simulated: bool = False
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
