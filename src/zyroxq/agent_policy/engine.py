"""Agent Policy Engine: deterministic decisions for both negotiating parties. No LLM, no I/O.

    hard constraints ──fail──▶ REJECT / REQUEST_CLARIFICATION / ESCALATE_TO_HUMAN
          │ pass
    money limits ─────fail──▶ COUNTEROFFER (concession schedule) or REJECT at the round limit / no movement
          │ pass
    utility score ────────────▶ ACCEPT if ≥ threshold, else COUNTEROFFER

An LLM suggestion may replace the rules' action or amounts only where the result still passes every
check; anything else is overridden and the override is recorded. Public explanations come from fixed
templates, so neither party's limits nor any model-written text reach the other side.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ..models import CampaignSpec, InfluencerProfile, Offer, new_id, requirements_hash
from ..policy.engine import check_offer
from .models import Action, AdvertiserPolicy, CheckResult, DecisionRecord, InfluencerPolicy
from .pricing import CENT, calculate

HUNDRED = Decimal(100)


@dataclass
class Decision:
    record: DecisionRecord
    counter: Offer | None = None  # COUNTEROFFER: the offer to send; ACCEPT (advertiser): the acceptance offer


def _money(x: Decimal) -> str:
    return f"${x.quantize(CENT)}"


def _terms_text(offer: Offer) -> str:
    if offer.compensation_model == "fixed":
        return f"{_money(offer.fixed_fee)} fixed"
    if offer.compensation_model == "cpm":
        return f"{_money(offer.cpm)} CPM up to {_money(offer.cap)}"
    return f"{_money(offer.fixed_fee)} fixed + {_money(offer.cpm)} CPM, capped at {_money(offer.cap)}"


def shape(offer: Offer, fixed: Decimal, cpm: Decimal, cap: Decimal, **update: Any) -> Offer:
    """Applies the compensation model's invariants: fixed → cap = fee, no CPM; cpm → no fixed fee."""
    fixed, cpm = fixed.quantize(CENT), cpm.quantize(CENT)
    if offer.compensation_model == "fixed":
        cpm, cap = Decimal("0.00"), fixed
    elif offer.compensation_model == "cpm":
        fixed = Decimal("0.00")
    return offer.model_copy(update={"fixed_fee": fixed, "cpm": cpm, "cap": cap, **update})


def _total(model, fixed: Decimal, cpm: Decimal, views: int, cap: Decimal) -> Decimal:
    """Payable total for scoring. Offers from the other agent may be malformed (fixed above the cap, zero
    cap); scoring then treats them as paying the cap, and the hard checks decide."""
    if cap <= 0 or fixed < 0 or cpm < 0:
        return Decimal(0)
    return calculate(model, min(fixed, cap), cpm, views, cap).total


def _weighted(primary: str, weights: dict[str, float], features: dict[str, float]) -> float:
    w = {k: v for k, v in weights.items() if k in features}
    w[primary] = max(w.get(primary, 0.0), 1.0)  # the primary goal always counts most
    total = sum(w.values())
    return round(sum(features[k] * v for k, v in w.items()) / total, 4) if total else 0.0


# ---- influencer -----------------------------------------------------------------------------


@dataclass
class Floors:
    fixed: Decimal  # 0 when the model has no fixed fee
    cpm: Decimal  # 0 when the model has no CPM
    anchor_fixed: Decimal
    premiums: list[str]


def influencer_floors(policy: InfluencerPolicy, offer: Offer) -> Floors:
    """The creator's minimums for this offer: the base floor plus premiums for scope beyond one organic post."""
    h, p, pr = policy.hard, policy.preferences, policy.preferences.premiums
    pct, applied = Decimal(0), []
    if offer.scope.paid_amplification or offer.scope.usage_rights == "paid_ads":
        pct += pr.paid_amplification_pct
        applied.append(f"paid amplification +{pr.paid_amplification_pct}%")
    if offer.scope.exclusivity_days:
        add = pr.exclusivity_pct_per_30_days * Decimal(offer.scope.exclusivity_days) / 30
        pct += add
        applied.append(f"exclusivity {offer.scope.exclusivity_days}d +{add.quantize(CENT)}%")
    if offer.scope.license_days:
        add = pr.license_pct_per_30_days * Decimal(offer.scope.license_days) / 30
        pct += add
        applied.append(f"license {offer.scope.license_days}d +{add.quantize(CENT)}%")
    if offer.publish_within_days < pr.rush_days:
        pct += pr.rush_pct
        applied.append(f"rush +{pr.rush_pct}%")
    extra = pr.extra_revision_fee * max(0, offer.scope.revisions - 1)
    if extra:
        applied.append(f"extra revisions +{_money(extra)}")
    factor = 1 + pct / HUNDRED
    has_fixed = offer.compensation_model in ("fixed", "hybrid")
    has_cpm = offer.compensation_model in ("cpm", "hybrid")
    return Floors(
        fixed=(h.min_fixed_fee * factor + extra).quantize(CENT) if has_fixed else Decimal(0),
        cpm=h.min_cpm if has_cpm else Decimal(0),
        anchor_fixed=(p.preferred_fixed_fee * factor + extra).quantize(CENT) if has_fixed else Decimal(0),
        premiums=applied,
    )


def _influencer_hard_checks(policy: InfluencerPolicy, offer: Offer, requirements: str, disclosure_ok: bool) -> list[tuple[CheckResult, Action, str]]:
    """(check, action on failure, public explanation on failure)."""
    h = policy.hard
    s = offer.scope
    reject = Action.ESCALATE_TO_HUMAN if policy.preferences.escalate_instead_of_reject else Action.REJECT
    category = offer.category.strip().lower()
    forbidden = {c.strip().lower() for c in h.forbidden_categories}
    rows = [
        (Decimal(0) <= offer.fixed_fee <= offer.cap and offer.cap > 0 and offer.cpm >= 0, "offer_well_formed", Action.REJECT,
         "The offer is malformed: amounts must be non-negative and the fixed fee within the escrow cap.", f"fixed {offer.fixed_fee}, cap {offer.cap}"),
        (requirements_hash(requirements) == offer.requirements_hash, "brief_matches_signed_hash", Action.REJECT,
         "The content requirements received do not match the offer.", "hash of the received brief differs from the offer"),
        (disclosure_ok, "sponsorship_disclosure", Action.REJECT,
         "The offer does not require a sponsorship disclosure this creator's audience regions accept.", f"tags {offer.required_tags}"),
        (bool(requirements.strip()) or not h.require_brief, "brief_present", Action.REQUEST_CLARIFICATION,
         "Please send a written content brief.", "creator requires a written brief"),
        (offer.compensation_model in h.accepted_models, "compensation_model", reject,
         f"This creator does not take {offer.compensation_model} deals.", f"accepted: {h.accepted_models}"),
        (offer.publish_within_days >= h.min_turnaround_days, "turnaround", reject,
         "The publish window is too short for this creator.", f"{offer.publish_within_days}d < {h.min_turnaround_days}d"),
        (s.revisions <= h.max_revisions, "revisions", reject,
         "More revision rounds are requested than this creator offers.", f"{s.revisions} > {h.max_revisions}"),
        (h.allow_paid_amplification or not (s.paid_amplification or s.usage_rights == "paid_ads"), "paid_amplification", reject,
         "This creator does not allow paid amplification of their content.", "paid amplification not allowed"),
        (s.license_days <= h.max_license_days, "license_length", reject,
         "The requested content license is longer than this creator allows.", f"{s.license_days}d > {h.max_license_days}d"),
        (not s.exclusivity_days or (h.allow_exclusivity and s.exclusivity_days <= h.max_exclusivity_days), "exclusivity", reject,
         "The requested exclusivity is not available.", f"{s.exclusivity_days}d requested"),
        (not category or category not in forbidden, "brand_category", reject,
         "This creator does not work with this brand category.", f"category {category!r} is forbidden"),
    ]  # fmt: skip
    return [(CheckResult(name=n, passed=ok, detail="ok" if ok else d), act, msg) for ok, n, act, msg, d in rows]


def _influencer_scores(policy: InfluencerPolicy, offer: Offer, floors: Floors) -> tuple[dict[str, float], list[str]]:
    p = policy.preferences
    notes: list[str] = []
    views = p.expected_views
    if views is None:
        notes.append("expected views unknown: CPM valued at $0 for scoring")
    cpm_anchor = p.preferred_cpm if p.preferred_cpm is not None else policy.hard.min_cpm
    offered = _total(offer.compensation_model, offer.fixed_fee, offer.cpm, views or 0, offer.cap)
    anchor = _total(offer.compensation_model, min(floors.anchor_fixed, offer.cap), cpm_anchor, views or 0, offer.cap)
    if anchor > 0:
        earnings = min(1.0, float(offered / anchor))
    else:  # CPM-only deal with no view estimate: compare the rates
        earnings = min(1.0, float(offer.cpm / cpm_anchor)) if cpm_anchor > 0 else 1.0
    guaranteed = min(1.0, float(offer.fixed_fee / floors.anchor_fixed)) if floors.anchor_fixed > 0 else 0.0
    preferred = {c.strip().lower() for c in p.preferred_categories}
    if not preferred or not offer.category:
        fit = 0.5
        notes.append("brand fit neutral: no preferred categories or no brand category")
    else:
        fit = 1.0 if offer.category.strip().lower() in preferred else 0.0
    workload = 1.0 - offer.scope.revisions / 5
    features = {
        "maximize_earnings": round(earnings, 4),
        "guaranteed_income": round(guaranteed, 4),
        "brand_fit": fit,
        "long_term_partnership": 0.5,  # no repeat-deal history is tracked yet: neutral, never guessed
        "minimize_workload": round(workload, 4),
    }
    return features, notes


def decide_for_influencer(
    policy: InfluencerPolicy,
    offer: Offer,
    requirements: str,
    previous: list[Offer],
    *,
    actor_id: str,
    disclosure_ok: bool,
    llm: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Decision:
    """`previous` are the advertiser's earlier offers in this deal (oldest first)."""
    now = now or datetime.now(UTC)
    floors = influencer_floors(policy, offer)
    hard = _influencer_hard_checks(policy, offer, requirements, disclosure_ok)
    money = [
        CheckResult(name="fixed_fee_floor", passed=offer.fixed_fee >= floors.fixed, detail=f"{offer.fixed_fee} vs floor {floors.fixed}"),
        CheckResult(name="cpm_floor", passed=offer.cpm >= floors.cpm, detail=f"{offer.cpm} vs floor {floors.cpm}"),
        CheckResult(name="cap_covers_fee", passed=floors.fixed <= offer.cap, detail=f"cap {offer.cap} vs floor {floors.fixed}"),
    ]
    features, notes = _influencer_scores(policy, offer, floors)
    score = _weighted(policy.objective.primary, policy.objective.weights, features)
    applied = floors.premiums + notes
    last_round = offer.round >= offer.max_rounds
    repeated = bool(previous) and (previous[-1].fixed_fee, previous[-1].cpm) == (offer.fixed_fee, offer.cpm)

    counter: Offer | None = None
    failed = next(((c, a, m) for c, a, m in hard if not c.passed), None)
    if failed:
        action, public, private = failed[1], failed[2], f"hard constraint {failed[0].name}: {failed[0].detail}"
    elif not money[2].passed:
        action, public, private = Action.REJECT, "The escrow cap cannot cover this creator's fee.", money[2].detail
    elif all(c.passed for c in money) and score >= policy.preferences.min_acceptable_score:
        action, public, private = Action.ACCEPT, "Accepted: the offer meets this creator's requirements.", f"score {score}"
    elif last_round:
        action, public, private = Action.REJECT, "Declined: no agreement within the round limit.", "round limit reached"
    elif repeated and not all(c.passed for c in money):
        action, public, private = Action.REJECT, "Declined: the offer did not change since the last round.", "repeated offer below the floor"
    else:
        counter = _concede(policy, offer, floors, features["brand_fit"] == 1.0, applied)
        action, public, private = Action.COUNTEROFFER, f"Counter-offer: {_terms_text(counter)}.", f"score {score}; conceding toward the floor"

    decided_by, suggestion, overridden = "rules", None, []
    if llm is not None:
        suggestion = {k: str(v) for k, v in llm.items() if k in ("action", "fixed_fee", "cpm")}  # the note stays out of the record
        action, counter, public, overridden, used = _apply_influencer_llm(llm, action, counter, offer, floors, hard, last_round, public)
        decided_by = "llm" if used else "rules (LLM overridden)"

    record = DecisionRecord(
        id=new_id("DEC"),
        deal_id=offer.deal_id,
        campaign_id=offer.campaign_id,
        party="influencer",
        actor_id=actor_id,
        round=offer.round,
        objective=policy.objective.primary,
        hard_checks=[c for c, _, _ in hard] + money,
        preferences_applied=applied,
        scores=features,
        score=score,
        action=action,
        public_explanation=public,
        private_rationale=private,
        decided_by=decided_by,
        llm_suggestion=suggestion,
        llm_overridden=overridden,
        policy_version=policy.version,
        pricing_version=offer.pricing_version,
        offer_id=offer.key,
        response_offer_id=counter.key if counter else None,
        at=now,
    )
    return Decision(record, counter)


def _concede(policy: InfluencerPolicy, offer: Offer, floors: Floors, good_fit: bool, applied: list[str]) -> Offer:
    """From the anchor toward the floor as rounds pass; never below the floor or below what was offered."""
    p = policy.preferences
    anchor = floors.anchor_fixed
    if good_fit and p.brand_fit_discount_pct:
        anchor = max(floors.fixed, anchor * (1 - p.brand_fit_discount_pct / HUNDRED))
        applied.append(f"brand-fit discount {p.brand_fit_discount_pct}%")
    step = Decimal(offer.round - 1) / Decimal(max(1, offer.max_rounds - 1))
    fixed = max(anchor - (anchor - floors.fixed) * step, offer.fixed_fee, floors.fixed)
    if offer.compensation_model == "hybrid":
        fixed = min(fixed, offer.cap)
    cpm_anchor = p.preferred_cpm if p.preferred_cpm is not None else floors.cpm
    cpm = max(cpm_anchor - (cpm_anchor - floors.cpm) * step, offer.cpm, floors.cpm) if floors.cpm or cpm_anchor else offer.cpm
    return shape(offer, fixed, cpm, offer.cap, proposer="influencer", note="")


def _apply_influencer_llm(llm, action, counter, offer, floors, hard, last_round, public):
    """Returns (action, counter, public, overridden, used). The LLM may decline, or pick a counter at or above the floors."""
    hard_ok = all(c.passed for c, _, _ in hard)
    wanted = str(llm.get("action", ""))
    try:
        fixed, cpm = Decimal(str(llm.get("fixed_fee"))), Decimal(str(llm.get("cpm")))
    except ArithmeticError:
        return action, counter, public, ["LLM amounts were not decimals"], False
    if wanted == "reject":
        return Action.REJECT, None, "Declined.", [], True  # declining never spends anyone's money
    if not hard_ok:
        return action, counter, public, ["LLM proposed to continue despite a failed hard constraint"], False
    if wanted == "accept":
        if action == Action.ACCEPT:
            return action, counter, public, [], True
        return action, counter, public, ["LLM accepted below the floor"], False
    if wanted == "counter":
        if last_round:
            return action, counter, public, ["LLM countered after the round limit"], False
        raised = []
        if fixed < floors.fixed:
            raised.append(f"fixed fee raised to the floor (LLM proposed {fixed})")
        if cpm < floors.cpm:
            raised.append(f"CPM raised to the floor (LLM proposed {cpm})")
        fixed, cpm = max(fixed, floors.fixed), max(cpm, floors.cpm)
        if offer.compensation_model == "hybrid" and fixed > offer.cap:
            raised.append("fixed fee lowered to the cap")
            fixed = offer.cap
        c = shape(offer, fixed, cpm, offer.cap, proposer="influencer", note="")
        return Action.COUNTEROFFER, c, f"Counter-offer: {_terms_text(c)}.", raised, not raised
    return action, counter, public, [f"unknown LLM action {wanted!r}"], False


# ---- advertiser -----------------------------------------------------------------------------


def screen_creator(spec: CampaignSpec, policy: AdvertiserPolicy, creator: InfluencerProfile) -> list[CheckResult]:
    """Before the first offer: evidence the advertiser requires, from verified sources only."""
    h = policy.hard
    blocked = {n.strip().lower() for n in h.blocked_niches}
    return [
        CheckResult(name="verified_channel", passed=bool(creator.channel_id) or not h.require_verified_channel, detail="OAuth-verified channel"),
        CheckResult(name="min_verified_subscribers", passed=creator.subscribers >= h.min_verified_subscribers,
                    detail=f"{creator.subscribers} vs {h.min_verified_subscribers} (YouTube Data API)"),
        CheckResult(name="niche_allowed", passed=creator.niche.lower() not in blocked, detail=creator.niche),
    ]  # fmt: skip


def opening(spec: CampaignSpec, policy: AdvertiserPolicy, deal_id: str) -> Offer:
    p = policy.preferences
    base = Offer(
        deal_id=deal_id,
        campaign_id=spec.id,
        round=1,
        max_rounds=spec.max_rounds,
        compensation_model=spec.compensation_model,
        fixed_fee=Decimal(0),
        cpm=Decimal(0),
        cap=spec.cap_per_creator,
        deliverable=spec.deliverable,
        publish_within_days=spec.publish_within_days,
        required_tags=list(spec.required_tags),
        min_duration_seconds=spec.min_duration_seconds,
        max_duration_seconds=spec.max_duration_seconds,
        requirements_hash=requirements_hash(spec.requirements),
        scope=spec.scope,
        category=spec.category,
        disclosure_policies=list(spec.disclosure_policies),
        note=spec.brief[:200],
    )
    return shape(base, spec.max_fixed * p.opening_fixed_pct / HUNDRED, spec.max_cpm * p.opening_cpm_pct / HUNDRED, spec.cap_per_creator)


def _advertiser_scores(spec: CampaignSpec, creator: InfluencerProfile, candidate: Offer) -> dict[str, float]:
    cost_ceiling = _total(spec.compensation_model, min(spec.max_fixed, spec.cap_per_creator), spec.max_cpm, 0, spec.cap_per_creator)
    guaranteed = _total(candidate.compensation_model, candidate.fixed_fee, candidate.cpm, 0, candidate.cap)
    if candidate.fixed_fee > candidate.cap:  # out of bounds: the hard checks reject it; score it as worst value
        guaranteed = cost_ceiling
    cost = 1.0 - float(guaranteed / cost_ceiling) if cost_ceiling > 0 else 1.0
    in_band = spec.min_subscribers <= creator.subscribers <= spec.max_subscribers
    return {
        "minimize_cost": round(max(0.0, cost), 4),
        "audience_relevance": 1.0 if creator.niche.lower() == spec.niche.lower() else 0.0,
        "maximize_reach": 1.0 if in_band else 0.0,
        "maximize_roi": round(max(0.0, cost), 4),  # no verified view forecast exists: ROI falls back to cost
        "evidence_quality": 1.0 if creator.channel_id else 0.0,  # subscribers + channel come from the YouTube Data API
        "speed_to_close": round(1.0 - (candidate.round - 1) / max(1, spec.max_rounds), 4),
    }


def decide_for_advertiser(
    spec: CampaignSpec,
    policy: AdvertiserPolicy,
    creator: InfluencerProfile,
    last: Offer,
    counter: Offer,
    previous_counters: list[Offer],
    *,
    actor_id: str,
    now: datetime | None = None,
) -> Decision:
    now = now or datetime.now(UTC)
    p = policy.preferences
    accept = shape(last, counter.fixed_fee, counter.cpm, last.cap if spec.compensation_model != "fixed" else counter.fixed_fee,
                   round=last.round + 1, note="Accepted.")  # fmt: skip
    within_limits = check_offer(spec, accept).violations
    features = _advertiser_scores(spec, creator, accept)
    score = _weighted(policy.objective.primary, policy.objective.weights, features)
    checks = [
        CheckResult(name="within_campaign_limits", passed=not within_limits, detail="; ".join(within_limits) or "ok"),
        CheckResult(name="counter_terms_unchanged", passed=_same_terms(last, counter), detail="scope, content and disclosure as offered"),
    ]
    applied: list[str] = []
    nxt: Offer | None = None
    if previous_counters and (previous_counters[-1].fixed_fee, previous_counters[-1].cpm) == (counter.fixed_fee, counter.cpm):
        applied.append("creator repeated its previous counter-offer")
    if not checks[1].passed:
        action, public, private = Action.REJECT, "Declined: the counter-offer changed terms that are not negotiable.", "non-price terms changed"
    elif not within_limits and p.escalate_fixed_above is not None and counter.fixed_fee > p.escalate_fixed_above:
        action, public, private = (
            Action.ESCALATE_TO_HUMAN,
            "The advertiser will review this counter-offer personally.",
            (f"fixed {counter.fixed_fee} above the escalation threshold {p.escalate_fixed_above}"),
        )
        applied.append("escalation threshold")
    elif not within_limits and score >= p.min_acceptable_score:
        action, public, private, nxt = Action.ACCEPT, f"Accepted: {_terms_text(accept)}.", f"score {score}", accept
    elif last.round + 1 > spec.max_rounds:
        action, public, private = Action.REJECT, "Declined: no agreement within the round limit.", "round limit reached"
    else:
        share = p.concession_pct / HUNDRED
        if features["audience_relevance"] == 1.0 and p.fit_premium_pct:
            share = min(Decimal(1), share * (1 + p.fit_premium_pct / HUNDRED))
            applied.append(f"niche-fit premium {p.fit_premium_pct}%")
        fixed = min(last.fixed_fee + (counter.fixed_fee - last.fixed_fee) * share, spec.max_fixed)
        cpm = min(last.cpm + (counter.cpm - last.cpm) * share, spec.max_cpm)
        cand = shape(last, fixed, cpm, last.cap if spec.compensation_model != "fixed" else fixed, round=last.round + 1, note="Meeting you partway.")
        if (cand.fixed_fee, cand.cpm) == (last.fixed_fee, last.cpm):  # at our ceilings: repeating the offer cannot help
            action, public, private = Action.REJECT, "Declined: this is the advertiser's final offer and it was not accepted.", "no room left to move"
        else:
            action, public, private, nxt = (
                Action.COUNTEROFFER,
                f"Counter-offer: {_terms_text(cand)}.",
                f"score {score}; closing {share * 100}% of the gap",
                cand,
            )
    record = DecisionRecord(
        id=new_id("DEC"),
        deal_id=last.deal_id,
        campaign_id=spec.id,
        party="advertiser",
        actor_id=actor_id,
        round=counter.round,
        objective=policy.objective.primary,
        hard_checks=checks,
        preferences_applied=applied,
        scores=features,
        score=score,
        action=action,
        public_explanation=public,
        private_rationale=private,
        policy_version=policy.version,
        pricing_version=counter.pricing_version,
        offer_id=counter.key,
        response_offer_id=nxt.key if nxt else None,
        at=now,
    )
    return Decision(record, nxt)


def _same_terms(a: Offer, b: Offer) -> bool:
    keys = ("compensation_model", "deliverable", "publish_within_days", "required_tags", "min_duration_seconds",
            "max_duration_seconds", "requirements_hash", "scope", "disclosure_policies", "cap")  # fmt: skip
    if a.compensation_model == "fixed":
        keys = tuple(k for k in keys if k != "cap")
    return all(getattr(a, k) == getattr(b, k) for k in keys)
