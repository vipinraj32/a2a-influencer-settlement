"""Configurable agent profiles. Three layers per party:

- objective: what the agent optimizes (a primary goal plus weights over secondary goals),
- hard constraints: never crossed, checked by deterministic code, a failure is a reject/counter/escalate,
- flexible preferences: shape scoring and concessions inside the hard limits.

Everything here is private to its owner. Other parties only ever see `public_view()`, which carries no
floors, ceilings, weights or thresholds.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ..models import CompensationModel, RateCard

Money = Decimal
COMPENSATION_MODELS: tuple[CompensationModel, ...] = ("fixed", "cpm", "hybrid")


class Action(StrEnum):
    ACCEPT = "ACCEPT"
    COUNTEROFFER = "COUNTEROFFER"
    REJECT = "REJECT"
    REQUEST_CLARIFICATION = "REQUEST_CLARIFICATION"
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"


def _weights_ok(weights: dict[str, float], allowed: set[str]) -> None:
    unknown = set(weights) - allowed
    if unknown:
        raise ValueError(f"unknown objective weights: {sorted(unknown)}")
    if any(w < 0 or w > 1 for w in weights.values()):
        raise ValueError("weights must be between 0 and 1")


# ---- influencer -------------------------------------------------------------------------

INFLUENCER_GOALS = {"maximize_earnings", "guaranteed_income", "brand_fit", "long_term_partnership", "minimize_workload"}


class InfluencerObjective(BaseModel):
    primary: Literal["maximize_earnings", "guaranteed_income", "brand_fit", "long_term_partnership", "minimize_workload"] = "maximize_earnings"
    weights: dict[str, float] = Field(default_factory=lambda: {"guaranteed_income": 0.3, "brand_fit": 0.2, "minimize_workload": 0.1})

    @model_validator(mode="after")
    def check(self) -> InfluencerObjective:
        _weights_ok(self.weights, INFLUENCER_GOALS)
        return self


class InfluencerHardConstraints(BaseModel):
    min_fixed_fee: Money = Field(ge=0, le=100_000)
    min_cpm: Money = Field(ge=0, le=1_000)
    accepted_models: list[CompensationModel] = Field(default_factory=lambda: list(COMPENSATION_MODELS), min_length=1)
    min_turnaround_days: int = Field(default=1, ge=0, le=90)  # shortest publish window the creator can meet
    max_revisions: int = Field(default=2, ge=0, le=5)
    allow_paid_amplification: bool = False
    max_license_days: int = Field(default=30, ge=0, le=730)
    allow_exclusivity: bool = False
    max_exclusivity_days: int = Field(default=0, ge=0, le=365)
    forbidden_categories: list[str] = Field(default_factory=list, max_length=30)
    require_brief: bool = False  # ask for a written brief instead of deciding without one


class InfluencerPremiums(BaseModel):
    """Raise the creator's fixed-fee floor and anchor for scope beyond one organic post."""

    paid_amplification_pct: Decimal = Field(default=Decimal("25"), ge=0, le=300)
    exclusivity_pct_per_30_days: Decimal = Field(default=Decimal("15"), ge=0, le=300)
    license_pct_per_30_days: Decimal = Field(default=Decimal("10"), ge=0, le=300)
    rush_pct: Decimal = Field(default=Decimal("20"), ge=0, le=300)  # publish window shorter than rush_days
    rush_days: int = Field(default=3, ge=0, le=30)
    extra_revision_fee: Money = Field(default=Decimal("0"), ge=0, le=10_000)  # per revision above one


class InfluencerPreferences(BaseModel):
    preferred_fixed_fee: Money = Field(gt=0, le=100_000)  # opening anchor
    preferred_cpm: Money | None = Field(default=None, ge=0, le=1_000)
    preferred_categories: list[str] = Field(default_factory=list, max_length=30)
    brand_fit_discount_pct: Decimal = Field(default=Decimal("0"), ge=0, le=30)  # concede faster for a good fit, never below the floor
    expected_views: int | None = Field(default=None, ge=0)  # the creator's own estimate, used to value CPM; labelled as such
    min_acceptable_score: float = Field(default=0.0, ge=0, le=1)  # 0 = accept anything that clears the hard constraints
    escalate_instead_of_reject: bool = False  # hand hard-constraint failures to the creator instead of declining
    premiums: InfluencerPremiums = Field(default_factory=InfluencerPremiums)


class CreatorProfile(BaseModel):
    """Self-reported; shown to advertisers labelled as self-reported, never as verified."""

    audience_countries: list[str] = Field(default_factory=list, max_length=10)  # ISO codes, largest first
    content_languages: list[str] = Field(default_factory=list, max_length=10)
    bio: str = Field(default="", max_length=500)


class DisclosurePreferences(BaseModel):
    preferred_label: str = Field(default="#ad", max_length=40)
    jurisdictions: list[str] = Field(default_factory=lambda: ["IN", "US"])  # where the creator's audience is


class InfluencerPolicy(BaseModel):
    role: Literal["influencer"] = "influencer"
    version: int = 0  # assigned by the store on save
    updated_at: datetime | None = None
    objective: InfluencerObjective = Field(default_factory=InfluencerObjective)
    hard: InfluencerHardConstraints
    preferences: InfluencerPreferences
    creator: CreatorProfile = Field(default_factory=CreatorProfile)
    disclosure: DisclosurePreferences = Field(default_factory=DisclosurePreferences)
    persona: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def check(self) -> InfluencerPolicy:
        if self.preferences.preferred_fixed_fee < self.hard.min_fixed_fee:
            raise ValueError("the preferred fixed fee must be at least the minimum fixed fee")
        if self.preferences.preferred_cpm is not None and self.preferences.preferred_cpm < self.hard.min_cpm:
            raise ValueError("the preferred CPM must be at least the minimum CPM")
        if self.hard.max_exclusivity_days and not self.hard.allow_exclusivity:
            raise ValueError("set allow_exclusivity to allow exclusivity days")
        return self

    @classmethod
    def from_rate_card(cls, rc: RateCard, persona: str = "") -> InfluencerPolicy:
        """Defaults for creators who never opened the profile builder: exactly the old rate-card behaviour."""
        return cls(
            hard=InfluencerHardConstraints(min_fixed_fee=rc.fixed_min, min_cpm=rc.cpm_min),
            preferences=InfluencerPreferences(preferred_fixed_fee=rc.preferred_fixed),
            persona=persona,
        )

    def rate_card(self) -> RateCard:
        return RateCard(fixed_min=self.hard.min_fixed_fee, cpm_min=self.hard.min_cpm, preferred_fixed=self.preferences.preferred_fixed_fee)

    def public_view(self) -> dict[str, Any]:
        """What an advertiser may learn: no floors, anchors, weights, thresholds or premiums."""
        return {
            "accepted_models": self.hard.accepted_models,
            "creator": self.creator.model_dump(),
            "disclosure_label": self.disclosure.preferred_label,
            "source": "self_reported",
        }


# ---- advertiser -------------------------------------------------------------------------

ADVERTISER_GOALS = {"minimize_cost", "maximize_reach", "maximize_roi", "audience_relevance", "evidence_quality", "speed_to_close"}


class AdvertiserObjective(BaseModel):
    primary: Literal["minimize_cost", "maximize_reach", "maximize_roi", "audience_relevance", "evidence_quality", "speed_to_close"] = "maximize_roi"
    weights: dict[str, float] = Field(default_factory=lambda: {"minimize_cost": 0.3, "audience_relevance": 0.2, "evidence_quality": 0.2})

    @model_validator(mode="after")
    def check(self) -> AdvertiserObjective:
        _weights_ok(self.weights, ADVERTISER_GOALS)
        return self


class AdvertiserHardConstraints(BaseModel):
    """Money ceilings live on each campaign (max fixed, max CPM, cap, budget); these apply on top."""

    min_verified_subscribers: int = Field(default=0, ge=0)  # from the YouTube Data API, never self-reported
    require_verified_channel: bool = True
    blocked_niches: list[str] = Field(default_factory=list, max_length=30)


class AdvertiserPreferences(BaseModel):
    opening_fixed_pct: Decimal = Field(default=Decimal("60"), gt=0, le=100)  # of the campaign's max fixed fee
    opening_cpm_pct: Decimal = Field(default=Decimal("70"), gt=0, le=100)
    concession_pct: Decimal = Field(default=Decimal("50"), gt=0, le=100)  # share of the gap we close per round
    escalate_fixed_above: Money | None = Field(default=None, gt=0)  # fixed fees above this go to a human
    min_acceptable_score: float = Field(default=0.0, ge=0, le=1)
    fit_premium_pct: Decimal = Field(default=Decimal("0"), ge=0, le=50)  # concede faster for a niche match, within the ceilings


class AdvertiserPolicy(BaseModel):
    role: Literal["advertiser"] = "advertiser"
    version: int = 0
    updated_at: datetime | None = None
    objective: AdvertiserObjective = Field(default_factory=AdvertiserObjective)
    hard: AdvertiserHardConstraints = Field(default_factory=AdvertiserHardConstraints)
    preferences: AdvertiserPreferences = Field(default_factory=AdvertiserPreferences)
    persona: str = Field(default="", max_length=500)

    def public_view(self) -> dict[str, Any]:
        return {"source": "advertiser"}  # nothing: the brief and the offer are what the creator sees


AgentPolicy = InfluencerPolicy | AdvertiserPolicy


# ---- decision records -----------------------------------------------------------------


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""  # private: may name the owner's limits


class DecisionRecord(BaseModel):
    """One deterministic decision, stored for its owner. `public_explanation` is the only text the other party sees."""

    id: str
    deal_id: str
    campaign_id: str
    party: Literal["advertiser", "influencer"]
    actor_id: str
    round: int
    objective: str
    hard_checks: list[CheckResult]
    preferences_applied: list[str] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict)
    score: float | None = None
    action: Action
    public_explanation: str
    private_rationale: str = ""
    decided_by: str = "rules"  # rules | llm | rules (LLM overridden)
    llm_suggestion: dict[str, Any] | None = None
    llm_overridden: list[str] = Field(default_factory=list)
    policy_version: int
    pricing_version: str
    offer_id: str  # the offer this decision answers
    response_offer_id: str | None = None
    at: datetime
