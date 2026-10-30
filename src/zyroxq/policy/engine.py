"""Deterministic payment policy. Pure functions only — no LLM, no I/O.

Every value the LLM proposes and every artifact another agent sends passes through here before
anything is signed. A non-empty violation list means "do not sign".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from eth_account import Account
from eth_account.messages import encode_defunct

from ..hsp.cart import cart_method, cart_total
from ..hsp.networks import TokenInfo
from ..models import DEFAULT_MAX_ROUNDS, CampaignSpec, DealTerms, InfluencerProfile, Offer, requirements_hash
from ..wallet.signer import recover_personal_sign

MAX_ROUNDS = DEFAULT_MAX_ROUNDS  # campaigns may set their own `max_rounds`


@dataclass
class PolicyResult:
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            self.violations.append(message)


def check_offer(campaign: CampaignSpec, offer: Offer) -> PolicyResult:
    """Bounds for anything the advertiser agent proposes or accepts."""
    r = PolicyResult()
    zero = Decimal("0")
    r.require(offer.campaign_id == campaign.id, "offer is for a different campaign")
    r.require(offer.round <= campaign.max_rounds, f"negotiation exceeded {campaign.max_rounds} rounds")
    r.require(offer.compensation_model == campaign.compensation_model, "compensation model changed")
    match campaign.compensation_model:
        case "fixed":
            r.require(zero < offer.fixed_fee <= campaign.max_fixed, f"fixed fee {offer.fixed_fee} outside (0, {campaign.max_fixed}]")
            r.require(offer.cpm == zero, "a fixed-fee deal has no CPM")
            r.require(offer.cap == offer.fixed_fee <= campaign.cap_per_creator, f"cap {offer.cap} must equal the fixed fee, at most {campaign.cap_per_creator}")
        case "cpm":
            r.require(offer.fixed_fee == zero, "a CPM deal has no fixed fee")
            r.require(zero < offer.cpm <= campaign.max_cpm, f"CPM {offer.cpm} outside (0, {campaign.max_cpm}]")
            r.require(offer.cap == campaign.cap_per_creator, f"cap {offer.cap} must equal campaign cap {campaign.cap_per_creator}")
        case _:
            r.require(zero < offer.fixed_fee <= campaign.max_fixed, f"fixed fee {offer.fixed_fee} outside (0, {campaign.max_fixed}]")
            r.require(zero <= offer.cpm <= campaign.max_cpm, f"CPM {offer.cpm} outside [0, {campaign.max_cpm}]")
            r.require(offer.cap == campaign.cap_per_creator, f"cap {offer.cap} must equal campaign cap {campaign.cap_per_creator}")
    r.require(offer.fixed_fee <= offer.cap, "fixed fee exceeds cap")
    r.require(offer.scope == campaign.scope, "content scope changed")
    r.require(offer.disclosure_policies == campaign.disclosure_policies, "disclosure policies changed")
    r.require(offer.deliverable == campaign.deliverable, "deliverable changed")
    r.require(set(campaign.required_tags) <= set(offer.required_tags), "required tags were dropped")
    r.require(_content_rules(offer) == _campaign_content_rules(campaign), "video length or requirements changed")
    return r


def _content_rules(x: Offer | DealTerms) -> tuple[int, int, str]:
    return (x.min_duration_seconds, x.max_duration_seconds, x.requirements_hash)


def _campaign_content_rules(campaign: CampaignSpec) -> tuple[int, int, str]:
    return (campaign.min_duration_seconds, campaign.max_duration_seconds, requirements_hash(campaign.requirements_document()))


def recover_terms_signer(terms: DealTerms) -> str | None:
    try:
        return Account.recover_message(encode_defunct(hexstr=terms.terms_hash()), signature=terms.influencer_signature)
    except Exception:
        return None


def check_terms(campaign: CampaignSpec, terms: DealTerms, influencer: InfluencerProfile, agreed: Offer) -> PolicyResult:
    """Signed terms coming back over A2A must match what was negotiated and be signed by the payee."""
    r = PolicyResult()
    r.require(terms.campaign_id == campaign.id and terms.deal_id == agreed.deal_id, "terms reference another deal")
    r.require(terms.influencer_id == influencer.id, "terms name a different influencer")
    r.require(terms.channel_id == influencer.channel_id, "channel does not match the OAuth-verified channel")
    r.require(terms.payee.lower() == influencer.wallet.lower(), "payee is not the influencer's registered wallet")
    r.require(
        (terms.fixed_fee, terms.cpm, terms.cap) == (agreed.fixed_fee, agreed.cpm, agreed.cap),
        "amounts differ from the agreed offer",
    )
    r.require(set(campaign.required_tags) <= set(terms.required_tags), "required tags were dropped")
    r.require(_content_rules(terms) == _campaign_content_rules(campaign), "signed terms cover different video length or requirements")
    r.require(
        (terms.compensation_model, terms.scope, terms.disclosure_policies) == (campaign.compensation_model, campaign.scope, campaign.disclosure_policies),
        "signed terms cover a different compensation model, scope or disclosure policy",
    )
    r.require(terms.pricing_version == agreed.pricing_version, "signed terms use a different pricing version")
    signer = recover_terms_signer(terms)
    expected = influencer.agent_signer or influencer.wallet
    r.require(signer is not None and signer.lower() == expected.lower(), "influencer signature invalid")
    if influencer.agent_signer and influencer.agent_signer.lower() != influencer.wallet.lower():
        r.require(delegation_valid(influencer), "agent key is not authorized by the influencer's wallet")
    return r


def delegation_valid(influencer: InfluencerProfile) -> bool:
    """The payout wallet signed a message naming this agent key and channel."""
    lines = set(influencer.delegation.splitlines())
    signer = recover_personal_sign(influencer.delegation, influencer.delegation_signature)
    return (
        signer is not None
        and signer.lower() == influencer.wallet.lower()
        and f"Agent signer: {influencer.agent_signer}" in lines
        and f"Channel: {influencer.channel_id}" in lines
    )


def check_cart(
    campaign: CampaignSpec,
    terms: DealTerms,
    cart_contents: dict[str, Any],
    token: TokenInfo,
    expected_pay_to: str,
    committed_spend: Decimal,
    now: datetime,
) -> PolicyResult:
    """Gate before the wallet signs the Payment Mandate for a Cart Mandate."""
    r = PolicyResult()
    method = cart_method(cart_contents)
    total = cart_total(cart_contents)
    r.require(cart_contents["id"] == terms.deal_id, "cart mandate id does not match the deal")
    r.require(total == terms.cap, f"cart total {total} != agreed cap {terms.cap}")
    r.require(method["pay_to"].lower() == expected_pay_to.lower(), f"pay_to {method['pay_to']} is not the expected payee")
    r.require(
        method["chain_id"] == token.chain_id and method["contract_address"].lower() == token.contract_address.lower(),
        "chain or token not allowed",
    )
    r.require(method["coin"] == terms.asset, "asset differs from the terms")
    r.require(committed_spend + total <= campaign.budget, f"budget exceeded: {committed_spend} + {total} > {campaign.budget}")
    expiry = datetime.fromisoformat(cart_contents["cart_expiry"].replace("Z", "+00:00"))
    r.require(expiry > now, "cart mandate expired")
    return r


def needs_human_approval(amount: Decimal, threshold: Decimal) -> bool:
    return amount > threshold


def performance_payout(terms: DealTerms, views: int) -> Decimal:
    """min(cap - fixed, views / 1000 × CPM), rounded down to cents (agent_policy.pricing)."""
    from ..agent_policy.pricing import calculate

    return calculate(terms.compensation_model, terms.fixed_fee, terms.cpm, views, terms.cap).performance
