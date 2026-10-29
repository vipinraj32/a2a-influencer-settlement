"""Configurable, explainable negotiation policies for influencer and advertiser agents.

The LLM proposes and explains; this package decides. See engine.py for the decision order."""

from .engine import Decision, decide_for_advertiser, decide_for_influencer, influencer_floors, opening, screen_creator, shape
from .evidence import EvidenceItem, creator_evidence, evidence_quality, verified_value
from .models import (
    Action,
    AdvertiserPolicy,
    CheckResult,
    DecisionRecord,
    InfluencerPolicy,
)
from .pricing import PriceBreakdown, calculate, rules

__all__ = [
    "Action",
    "AdvertiserPolicy",
    "CheckResult",
    "Decision",
    "DecisionRecord",
    "EvidenceItem",
    "InfluencerPolicy",
    "PriceBreakdown",
    "calculate",
    "creator_evidence",
    "decide_for_advertiser",
    "decide_for_influencer",
    "evidence_quality",
    "influencer_floors",
    "opening",
    "rules",
    "screen_creator",
    "shape",
    "verified_value",
]
