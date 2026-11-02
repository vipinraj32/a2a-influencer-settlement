"""What each agent says in the negotiation chat, in plain conversational sentences.

Deterministic templates over public facts only: names, the deliverable, the brief the advertiser chose to send, and
offers both sides already exchanged. Never a private limit, floor, score or rationale, so a message can be shown to the
other party and persisted in the transcript as is. The wording varies by deal and round (seeded, not random), so the
same negotiation always reads the same way.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from ..models import Offer

DELIVERABLE = {"youtube_short": "YouTube Short", "youtube_video": "YouTube video"}
BRIEF_CHARS = 160


def money(v: Decimal | str) -> str:
    return f"${Decimal(v):,.2f}"


def pay_words(o: Offer) -> str:
    """The offer in words: '$1.20 up front plus $0.70 per 1,000 views, up to $4.00 in total'."""
    fixed, cpm, cap = money(o.fixed_fee), money(o.cpm), money(o.cap)
    if o.compensation_model == "fixed":
        return f"{fixed} for the video"
    if o.compensation_model == "cpm":
        return f"{cpm} per 1,000 views, up to {cap} in total"
    return f"{fixed} up front plus {cpm} per 1,000 views, up to {cap} in total"


def _moved(new: Offer, old: Offer | None) -> str:
    """'up from $1.20 fixed and $0.70 per 1,000 views' when the amounts changed, else ''."""
    if old is None:
        return ""
    parts = []
    if new.compensation_model != "cpm" and Decimal(new.fixed_fee) != Decimal(old.fixed_fee):
        parts.append(f"{money(old.fixed_fee)} fixed")
    if new.compensation_model != "fixed" and Decimal(new.cpm) != Decimal(old.cpm):
        parts.append(f"{money(old.cpm)} per 1,000 views")
    return " and ".join(parts)


def _pick(seed: str, options: list[str]) -> str:
    return options[int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(options)]


def _deliverable(o: Offer) -> str:
    return DELIVERABLE.get(o.deliverable, o.deliverable.replace("_", " "))


def _brief(brief: str) -> str:
    text = " ".join(brief.split())
    return text if len(text) <= BRIEF_CHARS else text[: BRIEF_CHARS - 1].rstrip() + "…"


def _unprefixed(reason: str) -> str:
    """A policy explanation without its 'Declined:' label, as a sentence: the chat line already says no."""
    text = reason.strip()
    if text.lower().startswith("declined"):
        text = text[len("declined") :].lstrip(":. ")
    return text[:1].upper() + text[1:] if text else ""


# ---- advertiser agent ---------------------------------------------------------------------------------------------


def advertiser_opening(brand: str, creator: str, offer: Offer, brief: str) -> str:
    about = f" We're running a campaign: “{_brief(brief).rstrip('.')}”." if brief.strip() else ""
    ask = _pick(f"{offer.deal_id}:open", ["Would that work for you?", "Does that sound good?", "Are you interested?"])
    return f"Hi {creator}! I'm the agent for {brand}.{about} We'd love one {_deliverable(offer)} from you, and we're offering {pay_words(offer)}. {ask}"


def advertiser_counter(offer: Offer, previous: Offer | None, last_round: bool) -> str:
    moved = _moved(offer, previous)
    step = f"We've moved up from {moved}: we can do {pay_words(offer)}." if moved else f"We can do {pay_words(offer)}."
    if last_round:
        return f"Thanks for the counter. {step} That's our best and final offer."
    lead = _pick(f"{offer.deal_id}:{offer.round}:a", ["Thanks for the counter.", "Appreciate the quick reply.", "Thanks, that helps."])
    close = _pick(f"{offer.deal_id}:{offer.round}:b", ["If that works, we can close today.", "Can we meet there?", "Does that work for you?"])
    return f"{lead} {step} {close}"


def advertiser_accepts(offer: Offer) -> str:
    lead = _pick(f"{offer.deal_id}:{offer.round}:ok", ["Great, that works for us.", "Deal, we can do that.", "Sounds fair, let's do it."])
    return f"{lead} {pay_words(offer).capitalize()}. Please confirm and sign the terms."


def advertiser_walks_away(creator: str, reason: str) -> str:
    because = _unprefixed(reason)
    return f"We can't go any further on this one, so we'll have to pass.{' ' + because if because else ''} Thanks for your time, {creator}."


# ---- creator agent ------------------------------------------------------------------------------------------------


def creator_counter(brand: str, received: Offer, counter: Offer, last_round: bool) -> str:
    deliverable = _deliverable(received)
    if received.round == 1:
        lead = _pick(f"{received.deal_id}:c1", [f"Thanks for reaching out, {brand}!", f"Hi {brand}, thanks for the offer!", f"Thanks {brand}, happy to talk."])
        return f"{lead} For a {deliverable} I'd need {pay_words(counter)}."
    # only the new ask, never how far the creator could still go
    lead = _pick(f"{received.deal_id}:{received.round}:c", ["Thanks for moving up.", "We're getting closer.", "Appreciate that."])
    if last_round:
        return f"{lead} My best offer is {pay_words(counter)}."
    return f"{lead} I can come down to {pay_words(counter)}. Meet me there and we have a deal."


def creator_accepts(brand: str, offer: Offer) -> str:
    lead = _pick(f"{offer.deal_id}:{offer.round}:yes", ["Deal!", "Sounds good, it's a deal!", "Perfect, let's do it!"])
    return f"{lead} {pay_words(offer).capitalize()} works for me. I've signed the terms, and I'll post the {_deliverable(offer)} once {brand} funds the escrow."


def creator_declines(reason: str) -> str:
    return f"Thanks for the offer, but I'll have to pass. {_unprefixed(reason)}".strip()


def creator_asks(reason: str) -> str:
    return f"Before I can answer: {reason.strip()}"
