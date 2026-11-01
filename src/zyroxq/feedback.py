"""Human feedback on finished negotiations, and the controlled feedback memory built from it.

Each party rates its own agent. A feedback submission becomes a *pending* lesson for that owner and role;
only lessons the owner approved (optionally after editing the text) are retrieved for later negotiations,
and only as strategy guidance in the LLM prompt. Lessons never reach the deterministic policy engine or
`check_offer`, so no feedback can move a hard limit: budget, floors, deliverables, rights or payment rules.
Lessons stay private to their owner; nothing is shared across accounts.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from .models import CampaignSpec, Deal, DealState, new_id
from .store import Store

Role = Literal["advertiser", "influencer"]
LESSONS_PER_RUN = 3
LESSON_CHARS = 600
NOTE_CHARS = 300

# key → (label, negative, guidance for the advertiser's agent, guidance for the creator's agent)
CATEGORIES: dict[str, tuple[str, bool, str, str]] = {
    "accepted_too_quickly": (
        "Agent accepted too quickly",
        True,
        "When the creator's ask is above your target, explore at least one reasonable counteroffer before accepting.",
        "When an offer is below your preferred rate, counter toward your anchor before accepting.",
    ),
    "rejected_reasonable_offer": (
        "Agent rejected a reasonable offer",
        True,
        "When a counter is inside the campaign limits and close to your last offer, prefer closing the deal over walking away.",
        "When an offer meets your minimums and is close to your ask, prefer accepting or a small counter over declining.",
    ),
    "poor_negotiation": (
        "Agent did not negotiate properly",
        True,
        "Move in steady steps each round and answer the creator's last counter instead of repeating your previous offer.",
        "Move in steady steps each round and answer the advertiser's last offer instead of repeating your previous ask.",
    ),
    "ignored_requirements": (
        "Agent ignored campaign requirements",
        True,
        "Weigh the deliverable, content requirements and usage rights before deciding, not only the price.",
        "Weigh the deliverable, content requirements and usage rights before deciding, not only the price.",
    ),
    "exceeded_budget": (
        "Agent exceeded the permitted budget",
        True,
        "Keep every offer well inside the campaign limits; the server refuses anything above them.",
        "Keep every ask at or above your minimums; the server refuses anything below them.",
    ),
    "unrealistic_counteroffer": (
        "Agent made an unrealistic counteroffer",
        True,
        "Keep counteroffers near the creator's last ask; large jumps stall the negotiation.",
        "Keep counteroffers near the advertiser's last offer; large jumps stall the negotiation.",
    ),
    "misunderstood_other_party": (
        "Agent failed to understand the other party",
        True,
        "Read the creator agent's explanation and adjust to the point it raised.",
        "Read the advertiser agent's note and adjust to the point it raised.",
    ),
    "good_agreement": (
        "Agent reached a good agreement",
        False,
        "This pace and style closed a good deal: keep it for similar campaigns.",
        "This pace and style closed a good deal: keep it for similar offers.",
    ),
    "other": ("Other", True, "", ""),
}
HARD_LIMITS_NOTE = "Guidance only: it never changes the hard limits, and offers outside them are refused by the server."


class SuggestedOffer(BaseModel):
    fixed_fee: Decimal = Field(ge=0, le=100_000)
    cpm: Decimal = Field(ge=0, le=1_000)


class Ratings(BaseModel):
    effectiveness: int | None = Field(default=None, ge=1, le=5)
    offer_quality: int | None = Field(default=None, ge=1, le=5)
    communication: int | None = Field(default=None, ge=1, le=5)
    respected_requirements: int | None = Field(default=None, ge=1, le=5)
    outcome: int | None = Field(default=None, ge=1, le=5)


class FeedbackIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    category: Literal[tuple(CATEGORIES)]  # type: ignore[valid-type]
    ratings: Ratings = Field(default_factory=Ratings)
    what_went_wrong: str = Field(default="", max_length=2000)
    do_differently: str = Field(default="", max_length=2000)
    suggested_response: str = Field(default="", max_length=2000)
    suggested_offer: SuggestedOffer | None = None
    comments: str = Field(default="", max_length=4000)
    message_id: str | None = Field(default=None, max_length=64)  # the message the feedback is about, if the user picked one


class LessonUpdate(BaseModel):
    status: Literal["approved", "dismissed"]
    guidance: str | None = Field(default=None, min_length=5, max_length=LESSON_CHARS)


def negotiation_outcome(deal: Deal, spec: CampaignSpec) -> str:
    """agreed | rejected | timeout | escalated | cancelled | error, from what the deal stored."""
    if deal.terms is not None:
        return "agreed"
    text = deal.negotiation_outcome.lower()
    if text.startswith("negotiation failed"):
        return "error"
    if text.startswith("cancelled"):
        return "cancelled"
    if deal.state == DealState.ESCALATED:
        return "escalated"
    if deal.rounds and deal.rounds[-1]["offer"]["round"] >= spec.max_rounds:
        return "timeout"
    return "rejected"


def _target(transcript: list[dict[str, Any]], role: Role, message_id: str | None) -> dict[str, Any] | None:
    """The message the feedback points at: the one the user picked (if it is their agent's), else their agent's last message."""
    mine = [e for e in transcript if e["event_type"] == "message_completed" and e["agent_role"] == role]
    picked = next((e for e in mine if message_id and e["message_id"] == message_id), None) or (mine[-1] if mine else None)
    if picked is None:
        return None
    return {k: picked.get(k) for k in ("message_id", "event_id", "round", "content", "offer", "reply")}


def _offer_within_limits(offer: SuggestedOffer, role: Role, spec: CampaignSpec, floors: tuple[Decimal, Decimal] | None) -> bool:
    if role == "advertiser":
        return offer.fixed_fee <= spec.max_fixed and offer.cpm <= spec.max_cpm and offer.fixed_fee <= spec.cap_per_creator
    return floors is None or (offer.fixed_fee >= floors[0] and offer.cpm >= floors[1])


def build_feedback(
    body: FeedbackIn,
    *,
    deal: Deal,
    spec: CampaignSpec,
    role: Role,
    user_id: str,
    transcript: list[dict[str, Any]],
    floors: tuple[Decimal, Decimal] | None,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The feedback record (linked to the deal, round and message) and the pending lesson extracted from it."""
    label, negative, adv_text, inf_text = CATEGORIES[body.category]
    outcome = negotiation_outcome(deal, spec)
    target = _target(transcript, role, body.message_id)
    suggested_ok = body.suggested_offer is None or _offer_within_limits(body.suggested_offer, role, spec, floors)
    signal = {
        "category": body.category,
        "negative": negative,
        "role": role,
        "outcome": outcome,
        "rounds_used": len(deal.rounds),
        "max_rounds": spec.max_rounds,
        "round": target["round"] if target else None,
        "target_reply": target.get("reply") if target else None,
        # A suggested offer outside the hard limits is kept as a record of what the user asked for, never as an example
        "suggested_offer_within_limits": suggested_ok,
    }
    record = {
        "id": new_id("FBK"),
        "deal_id": deal.id,
        "negotiation_id": deal.id,
        "campaign_id": deal.campaign_id,
        "creator_id": deal.influencer_id,
        "party": role,
        "agent_role": role,
        "agent_id": f"advertiser-agent:{spec.owner_id}" if role == "advertiser" else f"influencer-agent:{deal.influencer_id}",
        "user_id": user_id,
        "rating": body.rating,
        "category": body.category,
        "category_label": label,
        "ratings": body.ratings.model_dump(),
        "what_went_wrong": body.what_went_wrong.strip(),
        "do_differently": body.do_differently.strip(),
        "suggested_response": body.suggested_response.strip(),
        "suggested_offer": body.suggested_offer.model_dump(mode="json") if body.suggested_offer else None,
        "comments": body.comments.strip(),
        "target": target,
        "signal": signal,
        "transcript": transcript,
        "created_at": now.isoformat(),
        "lesson_id": None,
    }
    base = adv_text if role == "advertiser" else inf_text
    note = body.do_differently.strip()[:NOTE_CHARS]
    guidance = " ".join(p for p in (base, f"Owner's note: {note}" if note else "") if p)
    if not guidance:
        return record, None
    lesson = {
        "id": new_id("LSN"),
        "owner_id": user_id,
        "role": role,
        "status": "pending",  # used only after the owner approves it
        "category": body.category,
        "negative": negative,
        "guidance": guidance[:LESSON_CHARS],
        "feedback_id": record["id"],
        "deal_id": deal.id,
        "context": {"niche": spec.niche.lower(), "compensation_model": spec.compensation_model},
        "created_at": now.isoformat(),
        "reviewed_at": None,
    }
    record["lesson_id"] = lesson["id"]
    return record, lesson


async def lessons_for(store: Store, owner_id: str, role: Role, *, niche: str = "", compensation_model: str = "") -> list[dict[str, Any]]:
    """Approved lessons of this owner and role, most relevant first: same niche and model, then the newest."""
    if not owner_id:
        return []
    approved = await store.list_lessons(owner_id, role, status="approved")

    def score(lesson: dict[str, Any]) -> tuple[int, str]:
        ctx = lesson.get("context", {})
        match = (ctx.get("niche") == niche.lower()) + (ctx.get("compensation_model") == compensation_model)
        return match, lesson.get("reviewed_at") or lesson["created_at"]

    return sorted(approved, key=score, reverse=True)[:LESSONS_PER_RUN]


def lessons_prompt(lessons: list[dict[str, Any]]) -> str:
    if not lessons:
        return ""
    lines = "\n".join(f"- {lesson['guidance']}" for lesson in lessons)
    return f"\nLessons your owner approved from earlier negotiations. {HARD_LIMITS_NOTE}\n{lines}"


# ---- performance ------------------------------------------------------------------


def _price(offer: dict[str, Any] | None, model: str) -> Decimal | None:
    if not offer:
        return None
    return Decimal(str(offer["cpm" if model == "cpm" else "fixed_fee"]))


def performance_row(deal: Deal, spec: CampaignSpec) -> dict[str, Any] | None:
    """One finished negotiation, as stored in agent_performance. None while it runs or when no offer was made."""
    if deal.state == DealState.NEGOTIATING or not deal.rounds:
        return None
    model = spec.compensation_model
    first_ask = next((r["reply"].get("counter") for r in deal.rounds if r["reply"].get("kind") == "counter"), None)
    final = deal.terms.model_dump(mode="json") if deal.terms else None
    price = lambda o: str(p) if (p := _price(o, model)) is not None else None  # noqa: E731
    return {
        "deal_id": deal.id,
        "campaign_id": deal.campaign_id,
        "advertiser_id": spec.owner_id,
        "influencer_id": deal.influencer_id,
        "outcome": negotiation_outcome(deal, spec),
        "rounds": len(deal.rounds),
        "compensation_model": model,
        "niche": spec.niche.lower(),
        "opening_price": price(deal.rounds[0]["offer"]),
        "first_ask_price": price(first_ask),
        "final_price": price(final),
        "ended_at": deal.updated_at.isoformat() if deal.updated_at else None,
    }


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def performance_summary(rows: list[dict[str, Any]], feedback: list[dict[str, Any]], role: Role) -> dict[str, Any]:
    """Metrics from stored negotiations and feedback only; None where there is no data to compute from."""
    outcomes = Counter(r["outcome"] for r in rows)
    total = len(rows)
    moves: list[Decimal] = []
    for r in rows:
        if r["outcome"] != "agreed" or r["final_price"] is None:
            continue
        final = Decimal(r["final_price"])
        if role == "advertiser" and r["first_ask_price"] and Decimal(r["first_ask_price"]) > 0:
            moves.append((Decimal(r["first_ask_price"]) - final) / Decimal(r["first_ask_price"]))  # saved vs the creator's first ask
        elif role == "influencer" and r["opening_price"] and Decimal(r["opening_price"]) > 0:
            moves.append((final - Decimal(r["opening_price"])) / Decimal(r["opening_price"]))  # gained vs the advertiser's opening
    ratings = [f["rating"] for f in feedback]
    weeks: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["ended_at"]:
            y, w, _ = datetime.fromisoformat(r["ended_at"]).isocalendar()
            b = weeks.setdefault(f"{y}-W{w:02d}", {"week": f"{y}-W{w:02d}", "negotiations": 0, "agreed": 0, "ratings": []})
            b["negotiations"] += 1
            b["agreed"] += r["outcome"] == "agreed"
    for f in feedback:
        y, w, _ = datetime.fromisoformat(f["created_at"]).isocalendar()
        weeks.setdefault(f"{y}-W{w:02d}", {"week": f"{y}-W{w:02d}", "negotiations": 0, "agreed": 0, "ratings": []})["ratings"].append(f["rating"])
    trend = [
        {"week": b["week"], "negotiations": b["negotiations"], "agreement_rate": _rate(b["agreed"], b["negotiations"]),
         "avg_rating": round(sum(b["ratings"]) / len(b["ratings"]), 2) if b["ratings"] else None, "feedback": len(b["ratings"])}
        for b in sorted(weeks.values(), key=lambda b: b["week"])
    ]  # fmt: skip
    mistakes = Counter(f["category"] for f in feedback if CATEGORIES[f["category"]][1] and f["category"] != "other")
    return {
        "role": role,
        "total": total,
        "outcomes": dict(outcomes),
        "agreement_rate": _rate(outcomes["agreed"], total),
        "rejection_rate": _rate(outcomes["rejected"] + outcomes["timeout"], total),
        "avg_rounds": round(sum(r["rounds"] for r in rows) / total, 2) if total else None,
        "avg_price_move": float(round(sum(moves) / len(moves), 4)) if moves else None,
        "price_move_basis": "saved vs the creator's first ask" if role == "advertiser" else "gained vs the advertiser's opening offer",
        "price_move_samples": len(moves),
        "satisfaction": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "feedback_count": len(ratings),
        "trend": trend,
        "common_mistakes": [{"category": c, "label": CATEGORIES[c][0], "count": n} for c, n in mistakes.most_common(5)],
    }
