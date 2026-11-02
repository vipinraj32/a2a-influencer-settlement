"""Deal lifecycle after negotiation: Cart Mandate → policy gate → Payment Mandate (signed in the
advertiser's browser wallet) → HSP settlement → escrow funding → proof verification → payouts.
Every transition is written to the audit log and pushed to the participants' live streams."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from ..agent_policy import AdvertiserPolicy, DecisionRecord, calculate
from ..chain.escrow import EscrowError, Release
from ..chain.settlement import FundingEvidence, SettlementMismatch, SettlementPending, SettlementReverted
from ..compliance import proof_checks as disclosure_checks
from ..feedback import performance_row
from ..hsp.cart import build_cart_contents, cart_method, cart_total
from ..hsp.webhook import verify_webhook_signature
from ..models import (
    Approval,
    AuditEvent,
    Campaign,
    CampaignSpec,
    Deal,
    DealState,
    DealTerms,
    InfluencerProfile,
    Offer,
    PaymentAttempt,
    Payout,
    PayoutIntent,
    Proof,
    ProofCheck,
    new_id,
    transition_allowed,
)
from ..policy import PolicyResult, check_cart, check_terms, performance_payout
from ..verifier.content import analyze_content
from ..verifier.explain import explain_rejection, explain_sent_back
from ..verifier.verify import all_passed, check_video
from ..verifier.youtube import parse_video_id
from ..wallet.gateway import GatewayRejected, GatewayUnavailable
from ..wallet.signer import mandate_from_signature, payment_authorization, payment_typed_data, recover_payment_mandate_signer, to_token_units
from .context import AppContext

log = logging.getLogger(__name__)

# Payment submitted or in flight, no verified settlement yet
PAYMENT_PENDING = {DealState.AWAITING_CHECKOUT, DealState.SIGNED, DealState.FUNDING, DealState.PAYMENT_UNKNOWN}
# Entering these frees the deal's budget reservation: the authorization can no longer be (or never was) paid
RELEASES_BUDGET = {DealState.REJECTED, DealState.BLOCKED, DealState.PAYMENT_FAILED, DealState.PAYMENT_EXPIRED, DealState.EXPIRED_REFUNDED}
AUTH_EXPIRED = "authorization expired or expires too soon to submit"
POLICY_KIND = "agent_policy"
# Audit kinds only the advertiser sees: they name the advertiser's limits or internal reasoning
ADVERTISER_PRIVATE_EVENTS = {"negotiation_move", "negotiation_decision", "policy_gate", "approval_decision", "campaign_full",
                             "discovered", "agent_unreachable", "authorization_redrafted", "lessons_applied"}  # fmt: skip


# Negotiation sessions (one per creator in a campaign, run one after another)
SESSION_TERMINAL = frozenset({"AGREED", "REJECTED", "TIMED_OUT", "FAILED", "CANCELLED"})
OUTCOME_STATUS = {
    "agreed": "AGREED",
    "rejected": "REJECTED",
    "walked_away": "REJECTED",
    "screened_out": "REJECTED",
    "escalated": "REJECTED",
    "timeout": "TIMED_OUT",  # round limit reached
    "no_reply": "TIMED_OUT",  # an agent did not answer within negotiation_reply_timeout_seconds
    "error": "FAILED",
    "interrupted": "FAILED",  # the server stopped mid-negotiation; it is never re-run
    "cancelled": "CANCELLED",
}


def _cap(offer: dict[str, Any] | None) -> Decimal:
    return Decimal(str(offer["cap"])) if offer else Decimal("0")


def campaign_progress(campaign: Campaign, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Where the campaign's creator queue stands, computed only from the persisted sessions. Agreed spend counts the
    caps (most each deal can pay) of AGREED negotiations; proposed spend is the latest offer on the table for every
    creator, agreed or not. Neither is a payment: nothing moves until the advertiser's wallet signs."""

    def count(*statuses: str) -> int:
        return sum(1 for x in sessions if x["status"] in statuses)

    running = next((x for x in sessions if x["status"] == "RUNNING"), None)
    queued = count("QUEUED")
    if running or (queued and not campaign.negotiation_cancelled):
        workflow = "running"
    elif sessions:
        workflow = "cancelled" if campaign.negotiation_cancelled else "completed"
    else:
        workflow = "failed" if campaign.status == "failed" else "starting"
    return {
        "campaign_id": campaign.spec.id,
        "total_creators": len(sessions),
        "completed_count": count(*SESSION_TERMINAL),
        "agreed_count": count("AGREED"),
        "rejected_count": count("REJECTED"),
        "failed_count": count("FAILED", "TIMED_OUT"),
        "cancelled_count": count("CANCELLED"),
        "queued_count": queued,
        "current_creator_id": running["creator_id"] if running else None,
        "current_negotiation_id": running["negotiation_id"] if running else None,
        "workflow_status": workflow,
        "agreed_spend": str(sum((_cap(x["final_offer"]) for x in sessions if x["status"] == "AGREED"), Decimal("0"))),
        "proposed_spend": str(sum((_cap(x["latest_offer"]) for x in sessions), Decimal("0"))),
    }


class DealError(Exception):
    pass


class DealService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._lock = asyncio.Lock()  # serializes escrow/ledger mutations
        self._owners: dict[str, str] = {}  # campaign → advertiser user, for live-event routing
        self._deal_influencers: dict[str, str] = {}
        self._deal_locks: dict[str, asyncio.Lock] = {}
        self._negotiating: set[str] = set()  # deals whose negotiation loop is running in this process
        self._cancel: set[str] = set()

    # ---- helpers -----------------------------------------------------------

    async def audit(self, campaign_id: str, deal_id: str | None, actor: str, kind: str, /, **detail: Any) -> None:
        event = AuditEvent(at=self.ctx.clock.now(), campaign_id=campaign_id, deal_id=deal_id, actor=actor, kind=kind,
                           detail=json.loads(json.dumps(detail, default=str)))  # fmt: skip
        await self.ctx.store.add_event(event)
        await self._publish({"type": "audit", "campaign_id": campaign_id, "deal_id": deal_id, "actor": actor, "kind": kind}, campaign_id, deal_id)

    async def save(self, deal: Deal, state: DealState | None = None) -> Deal:
        if state is not None:
            stored = await self.ctx.store.get_deal(deal.id)
            if stored is not None and not transition_allowed(stored.state, state):
                raise DealError(f"deal {deal.id}: {stored.state} → {state} is not allowed")
            deal.state = state
        deal.updated_at = self.ctx.clock.now()
        await self.ctx.store.save_deal(deal)
        if deal.state in RELEASES_BUDGET:
            await self.ctx.store.release_budget(deal.id)
        self._deal_influencers[deal.id] = deal.influencer_id
        await self._publish({"type": "deal", "campaign_id": deal.campaign_id, "deal_id": deal.id, "state": deal.state}, deal.campaign_id, deal.id)
        return deal

    async def save_campaign(self, campaign: Campaign) -> None:
        await self.ctx.store.save_campaign(campaign)
        self._owners[campaign.spec.id] = campaign.spec.owner_id
        await self._publish({"type": "campaign", "campaign_id": campaign.spec.id, "status": campaign.status}, campaign.spec.id, None)

    async def _publish(self, event: dict[str, Any], campaign_id: str, deal_id: str | None) -> None:
        if campaign_id not in self._owners and (c := await self.ctx.store.get_campaign(campaign_id)):
            self._owners[campaign_id] = c.spec.owner_id
        audience = {self._owners.get(campaign_id, "")}
        if deal_id:
            if deal_id not in self._deal_influencers and (d := await self.ctx.store.get_deal(deal_id)):
                self._deal_influencers[deal_id] = d.influencer_id
            audience.add(self._deal_influencers.get(deal_id, ""))
        audience.discard("")
        self.ctx.bus.publish(event, audience)

    async def get(self, deal_id: str) -> Deal:
        deal = await self.ctx.store.get_deal(deal_id)
        if deal is None:
            raise DealError(f"unknown deal {deal_id}")
        return deal

    async def campaign(self, campaign_id: str) -> Campaign:
        c = await self.ctx.store.get_campaign(campaign_id)
        if c is None:
            raise DealError(f"unknown campaign {campaign_id}")
        return c

    async def committed_spend(self, campaign_id: str, exclude: str | None = None) -> Decimal:
        """Caps reserved from the campaign budget by deals that are being, or were, paid."""
        return await self.ctx.store.reserved_budget(campaign_id, exclude=exclude)

    async def profile(self, influencer_id: str) -> InfluencerProfile:
        reg = await self.ctx.registry.get(influencer_id)
        if reg is None:
            raise DealError(f"influencer {influencer_id} is no longer active")
        return reg.profile

    async def notify_influencer(self, deal: Deal, message_type: str, /, **data: Any) -> None:
        """Tell the influencer's agent over A2A. Best effort: a down agent never blocks settlement."""
        reg = await self.ctx.registry.get(deal.influencer_id)
        if reg is None:
            return
        try:
            await self.ctx.a2a.send(reg.agent_url, {"type": message_type, "deal_id": deal.id, **json.loads(json.dumps(data, default=str))})
            await self.audit(deal.campaign_id, deal.id, "advertiser-agent", f"a2a_{message_type}_sent", **data)
        except Exception as e:  # noqa: BLE001
            log.warning("A2A notify %s failed: %s", message_type, e)

    async def refresh_campaign_status(self, campaign_id: str) -> None:
        c = await self.campaign(campaign_id)
        deals = await self.ctx.store.list_deals(campaign_id)
        states = {d.state for d in deals}
        if c.status in ("planning", "negotiating", "failed") and not (states - {DealState.NEGOTIATING}):
            return
        if not c.negotiation_cancelled and any(x["status"] in ("QUEUED", "RUNNING") for x in await self.negotiation_sessions(campaign_id)):
            return  # creators are still waiting their turn: the campaign is negotiating
        terminal = {DealState.REJECTED, DealState.BLOCKED, DealState.PAYMENT_FAILED, DealState.PAYMENT_EXPIRED, DealState.COMPLETED,
                    DealState.EXPIRED_REFUNDED}  # fmt: skip
        if DealState.AWAITING_APPROVAL in states:
            status = "awaiting_approval"
        elif states & (PAYMENT_PENDING | {DealState.CART_CREATED}):
            status = "funding"
        elif states and states <= terminal:
            status = "done"
        else:
            status = "active"
        if status != c.status:
            c.status = status  # type: ignore[assignment]
            await self.save_campaign(c)

    # ---- negotiation bookkeeping ------------------------------------------

    async def advertiser_policy(self, spec: CampaignSpec) -> AdvertiserPolicy:
        """The advertiser policy version the campaign snapshotted at creation (defaults when it had none)."""
        if spec.agent_policy_version and spec.owner_id:
            doc = await self.ctx.store.get_version(POLICY_KIND, spec.owner_id, spec.agent_policy_version)
            if doc and doc.get("role") == "advertiser":
                return AdvertiserPolicy.model_validate(doc)
        return AdvertiserPolicy()

    async def record_decision(self, record: DecisionRecord | dict[str, Any]) -> None:
        """Stores a decision record for its owner. Only the public explanation ever reaches the other party."""
        rec = record if isinstance(record, DecisionRecord) else DecisionRecord.model_validate(record)
        await self.ctx.store.add_decision(rec.model_dump(mode="json"))
        actor = "advertiser-agent" if rec.party == "advertiser" else f"influencer-agent:{rec.actor_id}"
        kind = "negotiation_decision" if rec.party == "advertiser" else "influencer_decision"
        await self.audit(rec.campaign_id, rec.deal_id, actor, kind, decision_id=rec.id, action=rec.action, round=rec.round,
                         decided_by=rec.decided_by, policy_version=rec.policy_version)  # fmt: skip

    async def end_negotiation(self, deal: Deal, state: DealState, outcome: str) -> Deal:
        deal.negotiation_outcome = outcome
        deal = await self.save(deal, state)
        await self.record_performance(deal)
        return deal

    async def record_performance(self, deal: Deal) -> None:
        if (row := performance_row(deal, (await self.campaign(deal.campaign_id)).spec)) is not None:
            await self.ctx.store.save_performance(row)

    # ---- live negotiation transcript ------------------------------------------

    async def negotiation_event(self, deal: Deal, event_type: str, agent_role: str, agent_id: str, agent_name: str, /, *,
                                content: str = "", offer: Offer | None = None, round: int | None = None,
                                message_id: str | None = None, **extra: Any) -> dict[str, Any]:  # fmt: skip
        """Persists one transcript event, then pushes it to the browsers that may see the deal. Only what the two
        agents exchanged goes in here (public notes and offers): never private limits or decision rationale."""
        event = {
            "negotiation_id": deal.id,
            "campaign_id": deal.campaign_id,
            "creator_id": deal.influencer_id,
            "event_type": event_type,
            "agent_role": agent_role,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "message_id": message_id,
            "round": round if round is not None else (offer.round if offer else None),
            "timestamp": self.ctx.clock.now().isoformat(),
            "content": content,
            "offer": {k: str(getattr(offer, k)) for k in ("fixed_fee", "cpm", "cap")} | {"round": offer.round} if offer else None,
            **json.loads(json.dumps(extra, default=str)),
        }
        saved = await self.ctx.store.add_negotiation_event(event)
        await self._publish({"type": "negotiation", "campaign_id": deal.campaign_id, "deal_id": deal.id, "event": saved}, deal.campaign_id, deal.id)
        return saved

    def negotiation_started(self, deal_id: str) -> None:
        self._negotiating.add(deal_id)

    def negotiation_stopped(self, deal_id: str) -> None:
        self._negotiating.discard(deal_id)
        self._cancel.discard(deal_id)

    def cancel_requested(self, deal_id: str) -> bool:
        return deal_id in self._cancel

    async def cancel_negotiation(self, deal: Deal, by: str) -> bool:
        """Asks the running loop to stop before its next message. A negotiation with no live loop (the server
        restarted mid-way) is closed here directly. False when the negotiation already ended."""
        if deal.state != DealState.NEGOTIATING:
            return False
        await self.audit(deal.campaign_id, deal.id, "human", "negotiation_cancel_requested", by=by)
        if deal.id in self._negotiating:
            self._cancel.add(deal.id)
            return True
        await self.end_negotiation(deal, DealState.REJECTED, "cancelled by the advertiser")
        system = ("system", "zyroxq", "ZyroXQ")
        await self.negotiation_event(deal, "negotiation_rejected", *system, content="Cancelled by the advertiser.", outcome="cancelled")
        session = next((x for x in await self.negotiation_sessions(deal.campaign_id) if x["negotiation_id"] == deal.id), None)
        if session is not None and session["status"] not in SESSION_TERMINAL:
            await self.finish_session(session, "cancelled", reason="cancelled by the advertiser")
        await self.negotiation_event(deal, "negotiation_completed", *system, outcome="cancelled")
        return True

    # ---- negotiation sessions: the campaign's creator queue ----------------------------
    # The campaign graph runs one session at a time (see agents/advertiser/graph.py). Every status change is a
    # compare-and-set on the stored status, so a cancel and a start can never both win, and nothing runs twice.

    async def queue_negotiations(self, spec: CampaignSpec, creator_ids: list[str]) -> list[dict[str, Any]]:
        """One QUEUED session per creator, in selection order. Re-queuing (a resumed run) keeps the sessions already stored."""
        unique = list(dict.fromkeys(creator_ids))  # the same creator is never negotiated with twice in one campaign
        rows = [
            {
                "negotiation_id": new_id("DEAL"),
                "campaign_id": spec.id,
                "creator_id": creator_id,
                "position": position,
                "advertiser_agent_id": f"advertiser-agent:{spec.owner_id}",
                "influencer_agent_id": f"influencer-agent:{creator_id}",
                "status": "QUEUED",
                "outcome": None,
                "current_round": 0,
                "maximum_rounds": spec.max_rounds,
                "initial_offer": None,
                "latest_offer": None,
                "final_offer": None,
                "queued_at": self.ctx.clock.now().isoformat(),
                "started_at": None,
                "completed_at": None,
                "failure_reason": "",
            }
            for position, creator_id in enumerate(unique)
        ]
        sessions = await self.ctx.store.queue_negotiations(rows)
        await self.publish_progress(spec.id)
        return sessions

    async def negotiation_sessions(self, campaign_id: str) -> list[dict[str, Any]]:
        return await self.ctx.store.list_negotiation_sessions(campaign_id)

    async def start_session(self, session: dict[str, Any]) -> dict[str, Any] | None:
        """QUEUED → RUNNING. None when someone else moved it first (cancelled, or another run started it)."""
        row = {**session, "status": "RUNNING", "started_at": self.ctx.clock.now().isoformat()}
        if not await self.ctx.store.save_negotiation_session(row, expect="QUEUED"):
            return None
        await self.publish_progress(session["campaign_id"])
        return row

    async def session_offer(self, session: dict[str, Any], offer: Offer) -> None:
        """Records the offer now on the table (either side) and the round it belongs to."""
        amounts = {"fixed_fee": str(offer.fixed_fee), "cpm": str(offer.cpm), "cap": str(offer.cap), "round": offer.round, "proposer": offer.proposer}
        session["latest_offer"] = amounts
        session["initial_offer"] = session["initial_offer"] or amounts
        session["current_round"] = max(session["current_round"], offer.round)
        await self.ctx.store.save_negotiation_session(session, expect="RUNNING")
        await self.publish_progress(session["campaign_id"])

    async def finish_session(self, session: dict[str, Any], outcome: str, *, final_offer: Offer | None = None, reason: str = "") -> dict[str, Any]:
        """The one place a creator's negotiation becomes terminal. The campaign graph then starts the next QUEUED creator."""
        status = OUTCOME_STATUS.get(outcome, "FAILED")
        session.update(status=status, outcome=outcome, completed_at=self.ctx.clock.now().isoformat(),
                       failure_reason=reason if status in ("FAILED", "TIMED_OUT", "CANCELLED") else "")  # fmt: skip
        if final_offer is not None:
            session["final_offer"] = {"fixed_fee": str(final_offer.fixed_fee), "cpm": str(final_offer.cpm), "cap": str(final_offer.cap),
                                      "round": final_offer.round, "offer": final_offer.model_dump(mode="json")}  # fmt: skip
        if not await self.ctx.store.save_negotiation_session(session, expect="RUNNING"):
            await self.ctx.store.save_negotiation_session(session)  # not RUNNING (an interrupted run being closed): still record it
        await self.audit(session["campaign_id"], session["negotiation_id"], "advertiser-agent", "negotiation_finished",
                         creator=session["creator_id"], status=status, outcome=outcome)  # fmt: skip
        await self.publish_progress(session["campaign_id"])
        return session

    async def abandon_session(self, session: dict[str, Any], reason: str,
                              message: str = "This negotiation stopped when the server restarted. Nothing was agreed or paid.") -> dict[str, Any]:  # fmt: skip
        """A session left RUNNING by a run that died (server restart). Re-running it could repeat what the agents already
        said and signed, so it is closed as FAILED with a visible message, and the queue moves on."""
        deal = await self.ctx.store.get_deal(session["negotiation_id"])
        if deal is None or deal.state != DealState.NEGOTIATING:
            return await self.finish_session(session, "interrupted", reason=reason)
        await self.end_negotiation(deal, DealState.REJECTED, reason)
        system = ("system", "zyroxq", "ZyroXQ")
        await self.negotiation_event(deal, "negotiation_error", *system, content=message, outcome="error", error="interrupted")
        await self.finish_session(session, "interrupted", reason=reason)
        await self.negotiation_event(deal, "negotiation_completed", *system, outcome="error")
        return session

    async def cancel_campaign_negotiations(self, campaign: Campaign, by: str) -> dict[str, Any]:
        """Stops the creator queue: QUEUED sessions become CANCELLED and the running one is asked to stop before its
        next message. Agreements already reached stay; they still need the advertiser's signature to be paid."""
        campaign.negotiation_cancelled = True
        await self.save_campaign(campaign)
        await self.audit(campaign.spec.id, None, "human", "negotiations_cancelled", by=by)
        for x in await self.negotiation_sessions(campaign.spec.id):
            if x["status"] == "QUEUED":
                row = {**x, "status": "CANCELLED", "outcome": "cancelled", "completed_at": self.ctx.clock.now().isoformat(),
                       "failure_reason": "cancelled by the advertiser before it started"}  # fmt: skip
                await self.ctx.store.save_negotiation_session(row, expect="QUEUED")
            elif x["status"] == "RUNNING" and (deal := await self.ctx.store.get_deal(x["negotiation_id"])) is not None:
                await self.cancel_negotiation(deal, by)
        await self.publish_progress(campaign.spec.id)
        return await self.progress(campaign.spec.id)

    async def progress(self, campaign_id: str) -> dict[str, Any]:
        return campaign_progress(await self.campaign(campaign_id), await self.negotiation_sessions(campaign_id))

    async def publish_progress(self, campaign_id: str) -> None:
        event = {"type": "negotiation_progress", "campaign_id": campaign_id, "progress": await self.progress(campaign_id)}
        await self._publish(event, campaign_id, None)  # the advertiser only: other creators' terms are not the creator's business

    async def record_approval(self, deal_id: str, kind: str, by: str, approved: bool, note: str = "") -> Deal:
        deal = await self.get(deal_id)
        terms = self._terms(deal)
        deal.approvals.append(Approval(kind=kind, by=by, approved=approved, terms_hash=terms.terms_hash(), note=note, at=self.ctx.clock.now()))  # type: ignore[arg-type]
        return await self.save(deal)

    def negotiation_view(self, deal: Deal, spec: CampaignSpec) -> dict[str, Any]:
        """The Negotiation Room: structured offers, what moved between rounds, the payable breakdown and the outcome.
        Built from what both parties already exchanged; no private limits or decision records."""

        def amounts(o: dict[str, Any] | None) -> dict[str, str] | None:
            return {k: o[k] for k in ("fixed_fee", "cpm", "cap")} if o else None

        def delta(a: dict[str, str] | None, b: dict[str, str] | None) -> dict[str, str] | None:
            if not a or not b:
                return None
            return {k: str(Decimal(b[k]) - Decimal(a[k])) for k in ("fixed_fee", "cpm")}

        rounds, prev_offer, prev_counter = [], None, None
        for r in deal.rounds:
            offer, reply = amounts(r["offer"]), r["reply"]
            counter = amounts(reply.get("counter")) if reply.get("kind") == "counter" else None
            rounds.append({
                "round": r["offer"]["round"],
                "advertiser_offer": offer,
                "advertiser_moved": delta(prev_offer, offer),
                "reply": reply.get("kind"),
                "explanation": reply.get("reason", ""),
                "counter": counter,
                "creator_moved": delta(prev_counter, counter),
            })  # fmt: skip
            prev_offer, prev_counter = offer, counter or prev_counter
        latest = deal.terms or (deal.rounds[-1]["offer"] if deal.rounds else None)
        breakdown = None
        if latest is not None:
            t = latest if isinstance(latest, dict) else latest.model_dump(mode="json")
            fixed, cpm, cap = Decimal(t["fixed_fee"]), Decimal(t["cpm"]), Decimal(t["cap"])
            model = t.get("compensation_model", spec.compensation_model)
            views_to_cap = int(((cap - fixed) / cpm * 1000).to_integral_value(rounding="ROUND_CEILING")) if cpm > 0 and cap > fixed else None
            samples = sorted({0, 10_000, *([views_to_cap] if views_to_cap else [])})
            breakdown = {"model": model, "views_to_cap": views_to_cap,
                         "examples": [calculate(model, fixed, cpm, v, cap).as_dict() for v in samples]}  # fmt: skip
        status = {DealState.NEGOTIATING: "open", DealState.REJECTED: "failed", DealState.ESCALATED: "escalated"}.get(deal.state, "agreed")
        if deal.state == DealState.REJECTED and deal.terms is not None:
            status = "agreed_then_cancelled"
        return {
            "deal_id": deal.id,
            "status": status,
            "outcome": deal.negotiation_outcome,
            "max_rounds": spec.max_rounds,
            "rounds_used": len(deal.rounds),
            "rounds_remaining": max(0, spec.max_rounds - len(deal.rounds)) if deal.state == DealState.NEGOTIATING else 0,
            "compensation_model": spec.compensation_model,
            "scope": spec.scope.model_dump(),
            "rounds": rounds,
            "payable": breakdown,
            "agreement": {
                "terms": deal.terms.model_dump(mode="json"),
                "terms_hash": deal.terms.terms_hash(),
                "approved_terms_hash": deal.approved_terms_hash,
                "approvals": [a.model_dump(mode="json") for a in deal.approvals],
            }
            if deal.terms
            else None,  # fmt: skip
        }

    async def compliance_view(self, spec: CampaignSpec) -> dict[str, Any]:
        """Campaign Compliance & Audit: which disclosure policy versions apply, and each deal's gates."""
        from ..compliance import DISCLAIMER

        deals = []
        for d in await self.ctx.store.list_deals(spec.id):
            if d.terms is None:
                continue
            checks = [c.model_dump() for c in (d.proof.checks if d.proof else []) if c.name.startswith("disclosure")]
            h = d.terms.terms_hash()
            review = next((a for a in reversed(d.approvals) if a.kind == "compliance_review" and a.terms_hash == h), None)
            payment = next((a for a in reversed(d.approvals) if a.kind == "payment" and a.terms_hash == h), None)
            gates = []
            if d.proof is None:
                gates.append("waiting for the post")
            elif not all(c["passed"] for c in checks):
                gates.append("disclosure checks failed")
            if self.review_pending(d, spec) and d.state == DealState.VERIFIED:
                gates.append("advertiser review pending" + (" (content agent could not settle every check)" if not spec.review_required else ""))
            deals.append({
                "deal_id": d.id,
                "influencer_id": d.influencer_id,
                "state": d.state,
                "disclosure_policies": d.terms.disclosure_policies,
                "checks": checks,
                "attestations": d.proof.attestations if d.proof else {},
                "review": "not_required" if not self.review_required(d, spec) else (("approved" if review.approved else "rejected") if review else "pending"),
                "payment_approval": payment.model_dump(mode="json") if payment else None,
                "terms_hash": h,
                "terms_hash_matches_approval": d.approved_terms_hash in (None, h),
                "gates": gates,
                "funding_simulated": d.funding_simulated,
            })  # fmt: skip
        return {
            "campaign_id": spec.id,
            "jurisdictions": spec.jurisdictions,
            "required_tags": spec.required_tags,
            "review_required": spec.review_required,
            "policies": [p.model_dump(mode="json") for p in self.ctx.disclosure.resolve_all(spec.disclosure_policies)],
            "disclaimer": DISCLAIMER,
            "settlement_simulated": self.ctx.gateway.simulated or self.ctx.escrow.simulated,
            "deals": deals,
        }

    async def record_round(self, deal: Deal, offer: Offer, reply: dict[str, Any], actor: str) -> None:
        deal.rounds.append({"offer": offer.model_dump(mode="json"), "reply": reply})
        await self.save(deal)
        await self.ctx.store.add_negotiation_round({"deal_id": deal.id, "campaign_id": deal.campaign_id, "round": offer.round,
                                                    "at": self.ctx.clock.now().isoformat(), "offer": deal.rounds[-1]["offer"], "reply": reply})  # fmt: skip
        await self.audit(deal.campaign_id, deal.id, actor, "negotiation_round", round=offer.round,
                         fixed=offer.fixed_fee, cpm=offer.cpm, reply=reply.get("kind"), reason=reply.get("reason", ""))  # fmt: skip

    # ---- cart mandate + policy gate + payment mandate ------------------------

    async def create_cart(self, deal: Deal) -> Deal:
        terms = self._terms(deal)
        contents = build_cart_contents(
            cart_mandate_id=deal.id,
            payment_request_id=f"PAY-{deal.id}",
            merchant_name=self.ctx.settings.hsp_merchant_name,
            token=self.ctx.token,
            pay_to=self.ctx.escrow.address,
            # A fixed deal has no performance line, a CPM deal no fixed line: HSP rejects zero amounts
            items=[(n, a) for n, a in (("Fixed fee", terms.fixed_fee), ("Performance fee (max)", terms.cap - terms.fixed_fee)) if a > 0],
            expires_at=self.ctx.clock.now() + timedelta(hours=2),
        )
        order = await self.ctx.gateway.create_order(contents)
        deal.cart_contents = contents
        deal.cart_mandate_id = contents["id"]
        deal.payment_request_id = order.payment_request_id
        deal.payment_url = order.payment_url
        await self.save(deal, DealState.CART_CREATED)
        await self.audit(deal.campaign_id, deal.id, "zyroxq-merchant", "cart_mandate_created",
                         total=terms.cap, pay_to=self.ctx.escrow.address, gateway=self.ctx.gateway.name,
                         payment_request_id=order.payment_request_id)  # fmt: skip
        return deal

    async def gate(self, deal: Deal, spec: CampaignSpec, agreed: Offer) -> PolicyResult:
        """Everything that must hold before the wallet may sign. Failure → BLOCKED, nothing signed.
        Passing reserves the deal's cap from the campaign budget atomically (store.reserve_budget)."""
        terms = self._terms(deal)
        assert deal.cart_contents is not None
        result = check_terms(spec, terms, await self.profile(deal.influencer_id), agreed)
        cart = check_cart(spec, terms, deal.cart_contents, self.ctx.token, self.ctx.escrow.address,
                          await self.committed_spend(deal.campaign_id, exclude=deal.id), self.ctx.clock.now())  # fmt: skip
        result.violations += cart.violations
        if result.ok and not await self.ctx.store.reserve_budget(spec.id, deal.id, terms.cap, spec.budget):
            result.violations.append(f"budget exceeded: another deal reserved the remaining budget of {spec.budget}")
        await self.audit(deal.campaign_id, deal.id, "policy", "policy_gate", ok=result.ok, violations=result.violations)
        if not result.ok:
            deal.policy_violations = result.violations
            await self.save(deal, DealState.BLOCKED)
        return result

    async def await_signature(self, deal: Deal, payer: str) -> Deal:
        """Drafts the EIP-3009 authorization the advertiser's wallet signs (simulated gateway; hosted mode
        signs in HSP checkout). The draft is fixed server-side, so the browser can only sign it, not edit it."""
        assert deal.cart_contents is not None
        deal.payer = payer
        deal.approved_terms_hash = self._terms(deal).terms_hash()  # the authorization pays for exactly these terms
        if self.ctx.gateway.agent_signing:
            now = int(self.ctx.clock.now().timestamp())
            deal.mandate_draft = payment_authorization(payer, deal.cart_contents, self.ctx.token, now - 60, now + self.ctx.settings.authorization_ttl_seconds)
        return await self.save(deal, DealState.AWAITING_APPROVAL)

    async def redraft(self, deal: Deal) -> Deal:
        """Replaces an unsigned authorization that expired (or is about to) with a fresh one: new window, new nonce."""
        if deal.state != DealState.AWAITING_APPROVAL or not self.ctx.gateway.agent_signing or deal.payer is None:
            raise DealError(f"deal is {deal.state}, nothing to redraft")
        await self.audit(deal.campaign_id, deal.id, "advertiser-agent", "authorization_redrafted")
        return await self.await_signature(deal, deal.payer)

    def typed_data(self, deal: Deal) -> dict[str, Any] | None:
        return payment_typed_data(deal.mandate_draft, self.ctx.token) if deal.mandate_draft else None

    def signature_valid(self, deal: Deal, signature: str) -> bool:
        if not (deal.mandate_draft and deal.cart_contents and deal.payment_request_id and deal.payer):
            return False
        mandate = mandate_from_signature(deal.mandate_draft, signature, deal.cart_contents, deal.payment_request_id, self.ctx.token)
        return recover_payment_mandate_signer(mandate, self.ctx.token).lower() == deal.payer.lower()

    async def authorization_problems(self, deal: Deal) -> list[str]:
        """Server-side checks on the drafted authorization before it may be signed or submitted. Nothing here
        trusts the browser: amount, recipient, token, chain, payer and budget come from server state."""
        problems: list[str] = []
        if deal.state != DealState.AWAITING_APPROVAL:
            return [f"deal is {deal.state}, not awaiting a signature"]
        terms, auth, contents = deal.terms, deal.mandate_draft, deal.cart_contents
        if terms is None or auth is None or contents is None or deal.payer is None:
            return ["no authorization drafted for this deal"]
        spec = (await self.campaign(deal.campaign_id)).spec
        method = cart_method(contents)
        cap_units = to_token_units(terms.cap, self.ctx.token.decimals)
        now = int(self.ctx.clock.now().timestamp())
        checks = [
            (contents["id"] == deal.id, "cart mandate belongs to another deal"),
            (method["chain_id"] == self.ctx.token.chain_id == self.ctx.escrow.chain_id, "wrong chain"),
            (method["contract_address"].lower() == self.ctx.token.contract_address.lower(), "wrong token contract"),
            (auth["to"].lower() == method["pay_to"].lower() == self.ctx.escrow.address.lower(), "recipient is not the escrow"),
            (int(auth["value"]) == cap_units == to_token_units(cart_total(contents), self.ctx.token.decimals), "amount differs from the agreed cap"),
            (auth["from"].lower() == deal.payer.lower() == spec.payer.lower(), "payer is not the campaign's wallet"),
            (int(auth["validAfter"]) <= now, "authorization is not valid yet"),
            (int(auth["validBefore"]) - now >= self.ctx.settings.authorization_min_remaining_seconds, AUTH_EXPIRED),
            (deal.approved_terms_hash == terms.terms_hash(), "terms changed after the authorization was drafted"),
        ]
        problems += [msg for ok, msg in checks if not ok]
        if not await self.ctx.store.reserve_budget(spec.id, deal.id, terms.cap, spec.budget):
            problems.append("budget exceeded")
        return problems

    async def funding_summary(self, campaign_id: str) -> dict[str, str]:
        """Budget is a spending limit, not money held: reserved = caps of deals that may still be paid or were paid."""
        spec = (await self.campaign(campaign_id)).spec
        reserved = await self.ctx.store.reserved_budget(campaign_id)
        funded = sum((d.terms.cap for d in await self.ctx.store.list_deals(campaign_id) if d.funding_tx and d.terms), Decimal("0"))
        return {"budget": str(spec.budget), "reserved": str(reserved), "funded": str(funded), "remaining": str(spec.budget - reserved)}

    async def submit_payment(self, deal: Deal, signature: str | None) -> Deal:
        """Fixed code path after the policy gate and the advertiser's wallet signature."""
        assert deal.cart_contents is not None and deal.payment_request_id is not None
        if not self.ctx.gateway.agent_signing:
            await self.save(deal, DealState.AWAITING_CHECKOUT)
            await self.audit(deal.campaign_id, deal.id, "advertiser-agent", "awaiting_hosted_checkout", payment_url=deal.payment_url)
            return deal
        if deal.state != DealState.AWAITING_APPROVAL:
            raise DealError(f"deal {deal.id} is {deal.state}: its payment was already submitted or closed")
        problems = await self.authorization_problems(deal)
        if problems:
            await self.save(deal, DealState.PAYMENT_EXPIRED if problems == [AUTH_EXPIRED] else DealState.PAYMENT_FAILED)
            await self.audit(deal.campaign_id, deal.id, "policy", "authorization_refused", problems=problems)
            return deal
        if signature is None or not self.signature_valid(deal, signature):
            await self.save(deal, DealState.PAYMENT_FAILED)
            await self.audit(deal.campaign_id, deal.id, "policy", "payment_signature_invalid", payer=deal.payer)
            return deal
        assert deal.mandate_draft is not None
        mandate = mandate_from_signature(deal.mandate_draft, signature, deal.cart_contents, deal.payment_request_id, self.ctx.token)
        auth = mandate.authorization
        if await self.ctx.store.claim("auth_nonce", auth["nonce"], deal.id) != deal.id:
            await self.save(deal, DealState.PAYMENT_FAILED)
            await self.audit(deal.campaign_id, deal.id, "policy", "authorization_replayed", nonce=auth["nonce"][:10] + "…")
            return deal

        # Persist the attempt before submitting, so a crash or timeout leaves a record to reconcile
        now = self.ctx.clock.now()
        deal.payment_attempts.append(PaymentAttempt(idempotency_key=auth["nonce"], payer=mandate.payer, to=auth["to"], value_units=int(auth["value"]),
                                                    valid_before=int(auth["validBefore"]), gateway=self.ctx.gateway.name,
                                                    simulated=self.ctx.gateway.simulated, created_at=now, updated_at=now))  # fmt: skip
        await self.save(deal, DealState.SIGNED)
        await self.audit(deal.campaign_id, deal.id, "wallet", "payment_mandate_signed", payer=mandate.payer, value=auth["value"], to=auth["to"],
                         scheme="x402 exact / EIP-3009", simulated=self.ctx.gateway.simulated)  # fmt: skip
        try:
            tx = await asyncio.wait_for(self.ctx.gateway.submit_payment_mandate(mandate, deal.cart_contents), self.ctx.settings.hsp_submit_timeout_seconds)
        except GatewayRejected as e:
            deal = await self._attempt(deal.id, auth["nonce"], "failed", error=str(e))
            await self.save(deal, DealState.PAYMENT_FAILED)
            await self.audit(deal.campaign_id, deal.id, "hsp", "payment_rejected", reason=str(e))
        except (TimeoutError, GatewayUnavailable, httpx.HTTPError) as e:
            # The transfer may still settle: never fail it or re-sign, reconcile with HSP and the chain instead
            deal = await self._attempt(deal.id, auth["nonce"], "unknown", tx_hash=getattr(e, "tx_hash", None), error=f"{type(e).__name__}: {e}"[:300])
            if deal.state in (DealState.SIGNED, DealState.FUNDING):
                await self.save(deal, DealState.PAYMENT_UNKNOWN)
            await self.audit(deal.campaign_id, deal.id, "hsp", "payment_outcome_unknown", reason=type(e).__name__)
        else:
            await self._attempt(deal.id, auth["nonce"], "pending", tx_hash=tx if self.ctx.gateway.direct_onchain else None)
            if self.ctx.gateway.direct_onchain:  # no webhook will come: check the transaction now, the scheduler keeps checking
                await self.reconcile(await self.get(deal.id))
        return await self.get(deal.id)

    async def _attempt(self, deal_id: str, key: str, status: str, *, tx_hash: str | None = None, error: str = "") -> Deal:
        """Updates a payment attempt. `pending` never overwrites an outcome a webhook already recorded."""
        deal = await self.get(deal_id)
        for a in deal.payment_attempts:
            if a.idempotency_key == key and not (status == "pending" and a.status != "submitted"):
                a.status = status  # type: ignore[assignment]
                a.tx_hash = tx_hash or a.tx_hash
                a.error = error or a.error
                a.updated_at = self.ctx.clock.now()
        return await self.save(deal)

    # ---- HSP notifications + settlement verification ------------------------------

    async def handle_webhook(self, raw: bytes, signature_header: str | None) -> bool:
        now = int(self.ctx.clock.now().timestamp())
        if not verify_webhook_signature(signature_header, raw, self.ctx.gateway.webhook_secret, now=now):
            return False
        payload = json.loads(raw)
        await self.on_payment(payload)
        # Recorded after processing, so a crash mid-way lets HSP's retry through; replays are harmless
        # because every transition below is idempotent on the deal's state and funding transaction.
        await self.ctx.store.claim_webhook(str(payload.get("request_id", "")), str(payload.get("status", "")))
        return True

    async def on_payment(self, p: dict[str, Any]) -> None:
        """An HSP payment notification. It is a claim, not evidence: FUNDED needs a verified settlement."""
        deal = await self.ctx.store.get_deal(str(p.get("cart_mandate_id", "")))
        if deal is None or deal.terms is None:
            log.warning("payment notification for unknown deal %s", p.get("cart_mandate_id"))
            return
        claimed = (Decimal(str(p.get("order_amount", "0"))), str(p.get("to_pay_address", "")).lower(), p.get("token"))
        if claimed != (deal.terms.cap, self.ctx.escrow.address.lower(), deal.terms.asset):
            await self.audit(deal.campaign_id, deal.id, "policy", "notification_mismatch", amount=p.get("order_amount"), to=p.get("to_pay_address"),
                             token=p.get("token"))  # fmt: skip
            if deal.state in PAYMENT_PENDING:
                await self.save(deal, DealState.PAYMENT_UNKNOWN)
            await self.refresh_campaign_status(deal.campaign_id)
            return
        await self._apply_status(deal, str(p.get("status", "")), p.get("tx_signature"), p.get("payer_address"), source="webhook")

    async def reconcile(self, deal: Deal) -> None:
        """Asks HSP again and re-checks the chain for a deal whose payment is pending or ambiguous."""
        if deal.payment_request_id is None:
            return
        attempt = deal.payment_attempts[-1] if deal.payment_attempts else None
        if self.ctx.gateway.direct_onchain:
            # No gateway to ask: the submitted transaction is the only claim, and its receipt decides (after restarts too)
            if attempt is not None and attempt.tx_hash and attempt.status != "failed":
                await self._apply_status(deal, "payment-finalized", attempt.tx_hash, attempt.payer, source="chain")
                return
            await self._authorization_expired(deal, attempt)
            return
        try:
            lookup = await self.ctx.gateway.lookup_payment(deal.payment_request_id)
        except GatewayUnavailable as e:
            await self.audit(deal.campaign_id, deal.id, "hsp", "reconcile_unavailable", reason=str(e)[:200])
            return
        if lookup is not None:
            await self._apply_status(deal, lookup.status, lookup.tx_hash, lookup.payer, source="reconcile")
            return
        # HSP has no record. Only the token contract can say the authorization will never settle.
        await self._authorization_expired(deal, attempt)

    async def _authorization_expired(self, deal: Deal, attempt: PaymentAttempt | None) -> None:
        """PAYMENT_FAILED once the authorization has expired and the token says it was never used."""
        if attempt is None or int(self.ctx.clock.now().timestamp()) < attempt.valid_before:
            return
        try:
            used = await asyncio.to_thread(self.ctx.settlement.authorization_used, attempt.payer, attempt.idempotency_key)
        except SettlementPending as e:
            await self.audit(deal.campaign_id, deal.id, "escrow", "reconcile_inconclusive", reason=str(e))
            return
        if not used:
            await self._attempt(deal.id, attempt.idempotency_key, "failed", error="authorization expired unused")
            await self.save(await self.get(deal.id), DealState.PAYMENT_FAILED)
            await self.audit(deal.campaign_id, deal.id, "escrow", "authorization_expired_unused")

    async def _apply_status(self, deal: Deal, status: str, tx_hash: Any, payer: Any, *, source: str) -> None:
        async with self._deal_lock(deal.id):
            deal = await self.get(deal.id)
            await self.audit(deal.campaign_id, deal.id, "hsp", "payment_status", status=status, tx=tx_hash, source=source)
            if deal.state not in PAYMENT_PENDING:
                return  # already settled or closed: duplicate or late notification
            deal.payment_status = status
            if status == "payment-failed":
                await self.save(deal, DealState.PAYMENT_FAILED)
            elif status in ("payment-included", "payment-safe"):
                await self.save(deal, DealState.FUNDING)
            elif status == "payment-finalized":
                await self._settle(deal, tx_hash if isinstance(tx_hash, str) else None, payer if isinstance(payer, str) else None)
            else:
                await self.save(deal)
        await self.refresh_campaign_status(deal.campaign_id)

    async def _settle(self, deal: Deal, tx_hash: str | None, payer: str | None) -> None:
        """HSP says finalized. Verify the transaction, bind it to this deal once, then lock it in escrow."""
        terms = self._terms(deal)
        expected_payer = deal.payer or payer
        if tx_hash is None or expected_payer is None or (payer and payer.lower() != expected_payer.lower()):
            await self.save(deal, DealState.PAYMENT_UNKNOWN)
            await self.audit(deal.campaign_id, deal.id, "escrow", "settlement_unverified", reason="no transaction hash or unexpected payer")
            return
        cap_units = to_token_units(terms.cap, self.ctx.token.decimals)
        try:
            evidence = await asyncio.to_thread(self.ctx.settlement.verify_funding, tx_hash, expected_payer, self.ctx.escrow.address, cap_units)
        except SettlementPending as e:
            await self.save(deal, DealState.FUNDING)
            await self.audit(deal.campaign_id, deal.id, "escrow", "settlement_pending", tx=tx_hash, reason=str(e))
            return
        except SettlementReverted as e:
            # Nothing moved. The signed authorization stays valid until it expires, so the deal is not failed yet:
            # reconcile marks it failed once the token confirms the authorization was never used.
            if deal.payment_attempts:
                deal = await self._attempt(deal.id, deal.payment_attempts[-1].idempotency_key, "failed", tx_hash=tx_hash, error=str(e))
            await self.save(deal, DealState.PAYMENT_UNKNOWN)
            await self.audit(deal.campaign_id, deal.id, "escrow", "settlement_reverted", tx=tx_hash)
            return
        except (SettlementMismatch, EscrowError) as e:
            await self.save(deal, DealState.PAYMENT_UNKNOWN)
            await self.audit(deal.campaign_id, deal.id, "escrow", "settlement_unverified", tx=tx_hash, reason=str(e))
            return
        except Exception as e:  # noqa: BLE001 - RPC unreachable: keep pending, reconcile retries
            await self.save(deal, DealState.FUNDING)
            await self.audit(deal.campaign_id, deal.id, "escrow", "settlement_check_failed", tx=tx_hash, reason=type(e).__name__)
            return
        if await self.ctx.store.claim("funding_tx", tx_hash, deal.id) != deal.id:
            await self.save(deal, DealState.PAYMENT_UNKNOWN)
            await self.audit(deal.campaign_id, deal.id, "escrow", "funding_tx_reused", tx=tx_hash)
            return
        await self._fund_escrow(deal, evidence)

    async def _fund_escrow(self, deal: Deal, evidence: FundingEvidence) -> None:
        terms = self._terms(deal)
        cap_units = to_token_units(terms.cap, self.ctx.token.decimals)
        async with self._lock:
            try:
                on_chain = await asyncio.to_thread(self.ctx.escrow.get_deal, deal.id)
                if on_chain.get("state", "None") == "None":
                    await asyncio.to_thread(self.ctx.escrow.register_deal, deal.id, evidence.payer, terms.payee, cap_units,
                                            int(terms.publish_by.timestamp()))  # fmt: skip
                    on_chain = {"state": "Registered"}
                if on_chain["state"] == "Registered":
                    await asyncio.to_thread(self.ctx.escrow.mark_funded, deal.id, evidence.tx_hash)
                elif on_chain["state"] != "Funded" or on_chain.get("funding_tx", "").lower() != evidence.tx_hash.lower():
                    raise EscrowError(f"escrow deal is {on_chain['state']}")
            except EscrowError as e:
                # The money is verifiably in the escrow but could not be bound: operator attention, never a re-payment
                await self.save(deal, DealState.PAYMENT_UNKNOWN)
                await self.audit(deal.campaign_id, deal.id, "escrow", "funding_failed", reason=str(e))
                return
        deal.funding_tx = evidence.tx_hash
        deal.funding_block = evidence.block_number
        deal.funding_simulated = evidence.simulated
        await self.save(deal, DealState.FUNDED)
        if deal.payment_attempts:
            await self._attempt(deal.id, deal.payment_attempts[-1].idempotency_key, "succeeded", tx_hash=evidence.tx_hash)
        await self.audit(deal.campaign_id, deal.id, "escrow", "escrow_funded", cap=terms.cap, escrow=self.ctx.escrow.address, tx=evidence.tx_hash,
                         confirmations=evidence.confirmations, simulated=evidence.simulated)  # fmt: skip
        await self.notify_influencer(deal, "funded", amount_locked=terms.cap, escrow=self.ctx.escrow.address, tx=evidence.tx_hash,
                                     simulated=evidence.simulated)  # fmt: skip

    def _deal_lock(self, deal_id: str) -> asyncio.Lock:
        return self._deal_locks.setdefault(deal_id, asyncio.Lock())

    # ---- proof + payouts -----------------------------------------------------

    async def submit_proof(self, deal_id: str, video_ref: str, attestations: dict[str, bool] | None = None) -> Deal:
        deal = await self.get(deal_id)
        if deal.state not in (DealState.FUNDED, DealState.PROOF_REJECTED):
            raise DealError(f"deal is {deal.state}, not awaiting proof")
        video_id = parse_video_id(video_ref)
        if video_id is None:
            raise DealError("not a YouTube URL or video id")
        if self.ctx.videos is None:
            raise DealError("the verifier is offline: set YOUTUBE_API_KEY")
        if self.ctx.clock.now() > self._terms(deal).publish_by:
            raise DealError("the publish deadline has passed: the escrow refunds this deal")
        spec = (await self.campaign(deal.campaign_id)).spec
        video = await self.ctx.videos.get_video(video_id)
        assert deal.agreed_at is not None
        terms = self._terms(deal)
        checks = check_video(terms, video, deal.agreed_at, spec.brand_handle)
        if video is not None:
            checks += self._disclosure_checks(terms, video, attestations or {})
        now = self.ctx.clock.now()
        content = None
        if video is not None and spec.content_checks:
            content = await analyze_content(spec.content_checks, video_id, self.ctx.captions, self.ctx.frames, self.ctx.llm, now)
            # Only deterministic results (caption counts) decide; vision and "unverified" go to the advertiser's review
            checks += [ProofCheck(name=f"content:{r.check_id}", passed=r.status == "pass", detail=r.detail) for r in content.results if r.decisive]
        if deal.proof is not None:
            deal.proof_history.append(deal.proof)
        deal.proof = Proof(video_id=video_id, submitted_at=now, checks=checks, attestations=attestations or {}, attempt=len(deal.proof_history) + 1,
                           published_at=video.published_at if video else None, views=video.views if video else None, content=content)  # fmt: skip
        passed = all_passed(checks)
        if content is not None:
            await self.audit(deal.campaign_id, deal.id, "content-agent", "content_checked", video_id=video_id, transcript=content.transcript,
                             results={r.check_id: r.status for r in content.results}, needs_review=content.needs_human)  # fmt: skip
        await self.audit(deal.campaign_id, deal.id, "verifier", "proof_checked", video_id=video_id, passed=passed, attempt=deal.proof.attempt,
                         failed=[f"{c.name}: {c.detail}" for c in checks if not c.passed])  # fmt: skip
        if not passed:
            review = await explain_rejection(checks, terms, spec.brand_handle, deal.agreed_at, now, deal.proof.published_at, self.ctx.llm)
            deal.proof.review = review
            await self.audit(deal.campaign_id, deal.id, "verifier", "proof_rejected", summary=review.summary, can_resubmit=review.can_resubmit,
                             resubmit_by=review.resubmit_by, issues=[i.check for i in review.issues])  # fmt: skip
            return await self.save(deal, DealState.PROOF_REJECTED)
        terms = self._terms(deal)
        # The fixed fee is due as soon as the proof is verified; the performance fee still waits for the day-7 views,
        # and that re-check is also what catches a video taken down after the fixed payout
        deal.fixed_release_due = now
        assert video is not None
        deal.performance_due = max(video.published_at + timedelta(days=terms.views_measured_after_days), deal.fixed_release_due)
        await self.save(deal, DealState.VERIFIED)
        deal = await self.release_due(deal.id)
        await self.refresh_campaign_status(deal.campaign_id)
        return deal

    def _disclosure_checks(self, terms: DealTerms, video: Any, attestations: dict[str, bool]) -> list[ProofCheck]:
        policies = self.ctx.disclosure.resolve_all(terms.disclosure_policies)
        unknown = [i for i in terms.disclosure_policies if self.ctx.disclosure.resolve(i) is None]
        checks = disclosure_checks(policies, video, attestations)
        if unknown:
            checks.append(ProofCheck(name="disclosure_policy_known", passed=False, detail=f"unknown disclosure policy {unknown}"))
        return checks

    async def compliance_review(self, deal_id: str, reviewer: str, approved: bool, note: str) -> Deal:
        """The advertiser's disclosure review of a verified post. Approval unblocks the fixed fee; rejection sends
        the proof back, and the creator can resubmit until the publish deadline."""
        deal = await self.get(deal_id)
        if deal.state != DealState.VERIFIED:
            raise DealError(f"deal is {deal.state}: only verified posts can be reviewed")
        deal = await self.record_approval(deal_id, "compliance_review", reviewer, approved, note)
        await self.audit(deal.campaign_id, deal.id, "human", "compliance_reviewed", approved=approved, note=note)
        if not approved:
            assert deal.proof is not None
            deal.proof.review = explain_sent_back(note, self._terms(deal), self.ctx.clock.now())
            await self.audit(deal.campaign_id, deal.id, "human", "proof_rejected", summary=deal.proof.review.summary,
                             can_resubmit=deal.proof.review.can_resubmit, resubmit_by=deal.proof.review.resubmit_by)  # fmt: skip
            deal = await self.save(deal, DealState.PROOF_REJECTED)
        else:
            deal = await self.release_due(deal.id)  # the review was the only thing holding the fixed fee
            await self.refresh_campaign_status(deal.campaign_id)
        return deal

    @staticmethod
    def review_required(deal: Deal, spec: CampaignSpec) -> bool:
        """The advertiser reviews when the campaign asks for it or the content agent could not settle a check."""
        return spec.review_required or bool(deal.proof and deal.proof.content and deal.proof.content.needs_human)

    def review_pending(self, deal: Deal, spec: CampaignSpec) -> bool:
        if not self.review_required(deal, spec) or deal.terms is None:
            return False
        h = deal.terms.terms_hash()
        return not any(a.kind == "compliance_review" and a.approved and a.terms_hash == h for a in deal.approvals)

    async def dispute(self, deal_id: str, reason: str) -> Deal:
        deal = await self.get(deal_id)
        if deal.state != DealState.VERIFIED:
            raise DealError("disputes are only possible while the fixed fee is still held (awaiting your review)")
        await self.audit(deal.campaign_id, deal.id, "human", "disputed", reason=reason)
        return await self.save(deal, DealState.DISPUTED)

    async def tick(self) -> int:
        """Scheduler: refunds, fixed-fee release, performance payout. Returns the number of actions taken."""
        actions = 0
        now = self.ctx.clock.now()
        for deal in await self.ctx.store.list_deals():
            if deal.state in PAYMENT_PENDING:
                await self.reconcile(deal)
                continue
            if not await self._payout_due(deal, now):
                continue
            before = deal.state
            deal = await self.release_due(deal.id)
            if deal.state != before:
                actions += 1
            await self.refresh_campaign_status(deal.campaign_id)
        return actions

    async def _payout_due(self, deal: Deal, now: datetime) -> bool:
        if deal.state in (DealState.FUNDED, DealState.PROOF_REJECTED):
            return now > self._terms(deal).publish_by
        if deal.state == DealState.VERIFIED:
            # the advertiser's disclosure review, when the campaign asks for one, gates the fixed fee
            return bool(deal.fixed_release_due and now >= deal.fixed_release_due) and not self.review_pending(
                deal, (await self.campaign(deal.campaign_id)).spec
            )
        if deal.state == DealState.FIXED_RELEASED:
            return bool(deal.performance_due and now >= deal.performance_due)
        return False

    async def release_due(self, deal_id: str) -> Deal:
        """Makes whatever payout is due for one deal now. Callers race (the scheduler, a verified proof, an approved
        review): the deal lock and a fresh read make sure only one of them decides and attests each payout. Escrow
        failures are recorded and left to the next tick, which resumes the same payout intent."""
        async with self._deal_lock(deal_id):
            deal = await self.get(deal_id)
            if not await self._payout_due(deal, self.ctx.clock.now()):
                return deal
            try:
                if deal.state == DealState.VERIFIED:
                    await self._release_fixed(deal)
                elif deal.state == DealState.FIXED_RELEASED:
                    await self._release_performance(deal)
                else:
                    await self._refund_expired(deal)
            except (EscrowError, DealError) as e:
                await self.audit(deal.campaign_id, deal.id, "escrow", "payout_error", reason=str(e))
            return await self.get(deal_id)

    async def _recheck(self, deal: Deal) -> tuple[bool, int]:
        assert deal.proof is not None and deal.agreed_at is not None
        spec = (await self.campaign(deal.campaign_id)).spec
        if self.ctx.videos is None:
            raise DealError("the verifier is offline: set YOUTUBE_API_KEY")
        video = await self.ctx.videos.get_video(deal.proof.video_id)
        checks = check_video(self._terms(deal), video, deal.agreed_at, spec.brand_handle)
        return all_passed(checks), video.views if video else 0

    def _paid(self, deal: Deal, kind: str) -> bool:
        return any(p.kind == kind for p in deal.payouts)

    async def _release(self, deal: Deal, amount: Decimal, kind: str, views: int | None) -> Deal:
        """Pays one attested amount out of the escrow, at most once, whatever fails or restarts on the way.

        1. The decision (amount + verifier attestation) is persisted as a PayoutIntent before anything is sent.
        2. If the escrow already used that attestation (its Released event exists), the payout is recorded from the
           event: the earlier send went through and only its record was lost.
        3. Otherwise the same attestation is sent; the escrow accepts it once, so a resend cannot pay twice.
        The Payout is recorded only from a successful receipt or the Released event."""
        assert deal.proof is not None
        terms = self._terms(deal)
        intent = deal.payout_intent
        if intent is None or intent.kind != kind:
            att = self.ctx.attestor.attest(deal.id, deal.proof.video_id, terms.payee, amount, kind,  # type: ignore[arg-type]
                                           deal.proof.checks, views=views)  # fmt: skip
            block = await asyncio.to_thread(self.ctx.escrow.block_number)
            intent = PayoutIntent(kind=kind, amount=amount, nonce=att.nonce, signature=att.signature, evidence_hash=att.evidence_hash,  # type: ignore[arg-type]
                                  views=views, from_block=block, created_at=self.ctx.clock.now())  # fmt: skip
            deal.payout_intent = intent
            deal.attestations.append(att)
            deal = await self.save(deal)
            await self.audit(deal.campaign_id, deal.id, "escrow", "payout_authorized", kind=kind, amount=amount, to=terms.payee,
                             attestation=att.evidence_hash, views=views)  # fmt: skip
        units = to_token_units(intent.amount, self.ctx.token.decimals)
        async with self._lock:
            found = await asyncio.to_thread(self.ctx.escrow.find_release, deal.id, intent.nonce, intent.from_block)
            if found is None:
                try:
                    tx = await asyncio.to_thread(self.ctx.escrow.release, deal.id, units, intent.nonce, intent.signature)
                except EscrowError as e:
                    # A lost response may still have paid: only the escrow's event can say, so look once more
                    found = await asyncio.to_thread(self.ctx.escrow.find_release, deal.id, intent.nonce, intent.from_block)
                    if found is None:
                        intent.attempts += 1
                        intent.error = str(e)[:300]
                        await self.save(deal)
                        raise
                else:
                    found = Release(tx, await asyncio.to_thread(self.ctx.escrow.tx_block, tx), units, terms.payee)
        return await self._record_payout(deal, intent, found)

    async def _record_payout(self, deal: Deal, intent: PayoutIntent, done: Release) -> Deal:
        escrow = self.ctx.escrow
        to = deal.payer or "" if intent.kind == "refund" else self._terms(deal).payee
        amount = Decimal(done.amount_units) / (Decimal(10) ** self.ctx.token.decimals) if intent.kind == "refund" else intent.amount
        payout = Payout(kind=intent.kind, to=to, amount=amount, tx_hash=done.tx_hash, at=self.ctx.clock.now(), simulated=escrow.simulated,
                        block_number=done.block_number, chain_id=escrow.chain_id, token=self.ctx.token.contract_address,
                        views=intent.views)  # fmt: skip
        deal.payouts.append(payout)
        deal.payout_intent = None
        deal = await self.save(deal)
        kind = f"{intent.kind}_released" if intent.kind != "refund" else "refunded"
        await self.audit(deal.campaign_id, deal.id, "escrow", kind, amount=amount, to=to, tx=done.tx_hash, block=done.block_number,
                         attestation=intent.evidence_hash or None, views=intent.views, simulated=escrow.simulated)  # fmt: skip
        if intent.kind != "refund":
            await self.notify_influencer(deal, "payout", kind=intent.kind, amount=amount, tx=done.tx_hash)
        return deal

    async def _close(self, deal: Deal) -> Deal:
        """Returns the escrow's remainder to the advertiser, once (a closed escrow deal is recorded from its event)."""
        if self._paid(deal, "refund"):
            return deal
        intent = deal.payout_intent
        if intent is None or intent.kind != "refund":
            block = await asyncio.to_thread(self.ctx.escrow.block_number)
            intent = PayoutIntent(kind="refund", amount=Decimal(0), from_block=block, created_at=self.ctx.clock.now())
            deal.payout_intent = intent
            deal = await self.save(deal)
        expired = deal.state in (DealState.FUNDED, DealState.PROOF_REJECTED)
        async with self._lock:
            found = await asyncio.to_thread(self.ctx.escrow.find_refund, deal.id, intent.from_block)
            if found is None:
                try:
                    if expired:
                        tx, units = await asyncio.to_thread(self.ctx.escrow.refund_expired, deal.id, int(self.ctx.clock.now().timestamp()))
                    else:
                        tx, units = await asyncio.to_thread(self.ctx.escrow.close, deal.id)
                except EscrowError as e:
                    found = await asyncio.to_thread(self.ctx.escrow.find_refund, deal.id, intent.from_block)
                    if found is None:
                        intent.attempts += 1
                        intent.error = str(e)[:300]
                        await self.save(deal)
                        raise
                else:
                    found = Release(tx, await asyncio.to_thread(self.ctx.escrow.tx_block, tx), units, deal.payer or "")
        if found.amount_units == 0:  # nothing left to return: no transfer happened, so no payout row
            deal.payout_intent = None
            return await self.save(deal)
        return await self._record_payout(deal, intent, found)

    async def _release_fixed(self, deal: Deal) -> None:
        # A payout already decided is finished, not re-decided: its attestation was signed for the checks that passed then
        pending = deal.payout_intent is not None and deal.payout_intent.kind == "fixed"
        if not pending and not self._paid(deal, "fixed"):
            still_ok, _ = await self._recheck(deal)
            if not still_ok:
                await self.audit(deal.campaign_id, deal.id, "verifier", "post_removed_before_release")
                await self.save(deal, DealState.PROOF_REJECTED)
                return
        if (pending or self._terms(deal).fixed_fee > 0) and not self._paid(deal, "fixed"):  # CPM-only deals have no fixed fee
            deal = await self._release(deal, self._terms(deal).fixed_fee, "fixed", None)
        await self.save(deal, DealState.FIXED_RELEASED)

    async def _release_performance(self, deal: Deal) -> None:
        intent = deal.payout_intent
        views: int | None = None
        if intent is not None and intent.kind == "performance":
            deal = await self._release(deal, intent.amount, "performance", intent.views)
        elif not self._paid(deal, "performance") and not (intent is not None and intent.kind == "refund"):
            still_ok, views = await self._recheck(deal)
            amount = performance_payout(self._terms(deal), views) if still_ok else Decimal("0")
            if amount > 0:
                deal = await self._release(deal, amount, "performance", views)
        deal = await self._close(deal)
        performance = sum((p.amount for p in deal.payouts if p.kind == "performance"), Decimal(0))
        refund = sum((p.amount for p in deal.payouts if p.kind == "refund"), Decimal(0))
        await self.audit(deal.campaign_id, deal.id, "escrow", "deal_closed", views=views, performance=performance, refunded=refund)
        await self.save(deal, DealState.COMPLETED)

    async def _refund_expired(self, deal: Deal) -> None:
        deal = await self._close(deal)
        refund = sum((p.amount for p in deal.payouts if p.kind == "refund"), Decimal(0))
        await self.audit(deal.campaign_id, deal.id, "escrow", "expired_refunded", refunded=refund)
        await self.save(deal, DealState.EXPIRED_REFUNDED)

    @staticmethod
    def _terms(deal: Deal) -> DealTerms:
        if deal.terms is None:
            raise DealError(f"deal {deal.id} has no agreed terms")
        return deal.terms
