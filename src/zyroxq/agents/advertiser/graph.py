"""Advertiser (Payment) Agent graph.

    discover ──Send×N──▶ negotiate ──▶ prepare_payments ──▶ approval ──▶ execute_payments ──▶ END
    (Agent Cards)        (A2A, parallel)   (Cart Mandate +       (interrupt:   (verify signatures,
                                            policy gate)           wallet signs   submit Payment
                                                                   in browser)    Mandates to HSP)

The LLM only appears inside `negotiate` (proposals, bounded by the policy). The advertiser's own
wallet signs; the graph only verifies and submits.
"""

from __future__ import annotations

import logging
import operator
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from ...agent_policy import screen_creator
from ...models import Deal, DealState, DealTerms, NegotiationReply, Offer, new_id
from ...services.deals import DealService
from ..influencer.server import NEGOTIATE_SKILL
from .negotiation import next_move, opening_offer

log = logging.getLogger(__name__)


class AdvertiserState(TypedDict, total=False):
    campaign_id: str
    shortlist: list[str]
    results: Annotated[list[dict[str, Any]], operator.add]
    gated: list[str]
    signatures: dict[str, str]


class NegotiateInput(TypedDict):
    campaign_id: str
    influencer_id: str


def build_advertiser_graph(deals: DealService, checkpointer: BaseCheckpointSaver):
    ctx = deals.ctx

    async def discover(state: AdvertiserState) -> AdvertiserState:
        campaign = await deals.campaign(state["campaign_id"])
        spec = campaign.spec
        shortlist: list[str] = []
        registry = await ctx.registry.all()
        for reg in registry:
            p, inf_id = reg.profile, reg.profile.id
            if spec.niche.lower() not in p.all_niches or not (spec.min_subscribers <= p.subscribers <= spec.max_subscribers):
                continue
            try:
                card = await ctx.a2a.fetch_card(reg.agent_url)
            except Exception as e:  # noqa: BLE001 - unreachable agents are skipped
                await deals.audit(spec.id, None, "advertiser-agent", "agent_unreachable", influencer=inf_id, error=str(e))
                continue
            if any(s.id == NEGOTIATE_SKILL for s in card.skills):
                shortlist.append(inf_id)
        await deals.audit(spec.id, None, "advertiser-agent", "discovered", shortlist=shortlist, registry_size=len(registry))
        campaign.status = "negotiating" if shortlist else "failed"
        campaign.error = "" if shortlist else f"no active {spec.niche} influencer agents match this campaign yet"
        await deals.save_campaign(campaign)
        return {"shortlist": shortlist}

    def fan_out(state: AdvertiserState) -> list[Send] | str:
        if not state["shortlist"]:
            return END
        return [Send("negotiate", {"campaign_id": state["campaign_id"], "influencer_id": i}) for i in state["shortlist"]]

    async def negotiate(inp: NegotiateInput) -> AdvertiserState:
        """Bounded: at most `spec.max_rounds` offers; failure (decline, no movement, escalation) is a valid outcome."""
        spec = (await deals.campaign(inp["campaign_id"])).spec
        reg = await ctx.registry.get(inp["influencer_id"])
        if reg is None:
            return {"results": []}
        policy = await deals.advertiser_policy(spec)
        deal = await deals.save(Deal(id=new_id("DEAL"), campaign_id=spec.id, influencer_id=reg.profile.id))
        failed = [c for c in screen_creator(spec, policy, reg.profile) if not c.passed]
        if failed:
            await deals.audit(spec.id, deal.id, "advertiser-agent", "negotiation_move", action="screened_out",
                              failed=[f"{c.name}: {c.detail}" for c in failed])  # fmt: skip
            await deals.end_negotiation(deal, DealState.REJECTED, "creator does not meet the advertiser's evidence requirements")
            return {"results": [{"deal_id": deal.id, "outcome": "screened_out"}]}

        offer = opening_offer(spec, deal.id, policy)
        actor = f"influencer-agent:{reg.profile.id}"
        counters: list[Offer] = []
        while True:
            message = {"type": "offer", "advertiser": spec.advertiser, "requirements": spec.requirements_document(), "offer": offer.model_dump(mode="json")}
            raw = await ctx.a2a.send(reg.agent_url, message, context_id=deal.id)
            reply = NegotiationReply.model_validate(raw)
            await deals.record_round(deal, offer, {"kind": reply.kind, "reason": reply.reason, "decided_by": raw.get("decided_by"),
                                                   "counter": reply.offer.model_dump(mode="json") if reply.offer else None}, actor)  # fmt: skip
            if reply.kind == "accept" and reply.terms is not None:
                return {"results": [await _agree(deal.id, reply.terms, offer)]}
            if reply.kind in ("clarify", "escalate"):
                await deals.end_negotiation(await deals.get(deal.id), DealState.ESCALATED, f"creator's agent: {reply.reason}")
                return {"results": [{"deal_id": deal.id, "outcome": "escalated"}]}
            if reply.kind == "reject" or reply.offer is None:
                await deals.end_negotiation(await deals.get(deal.id), DealState.REJECTED, f"creator declined: {reply.reason}")
                return {"results": [{"deal_id": deal.id, "outcome": "rejected"}]}
            move = await next_move(spec, reg.profile, offer, reply.offer, ctx.llm, policy, counters, ctx.clock.now())
            counters.append(reply.offer)
            if move.record is not None:
                await deals.record_decision(move.record)
            await deals.audit(spec.id, deal.id, "advertiser-agent", "negotiation_move", action=move.action, reason=move.reason,
                              decided_by=move.decided_by, policy_override=move.policy_override)  # fmt: skip
            outcome = move.record.public_explanation if move.record else move.reason
            if move.action == "escalate":
                await deals.end_negotiation(await deals.get(deal.id), DealState.ESCALATED, f"advertiser review needed: {outcome}")
                return {"results": [{"deal_id": deal.id, "outcome": "escalated"}]}
            if move.action == "walk_away" or move.offer is None:
                await deals.end_negotiation(await deals.get(deal.id), DealState.REJECTED, outcome)
                return {"results": [{"deal_id": deal.id, "outcome": "walked_away"}]}
            offer = move.offer

    async def _agree(deal_id: str, terms: DealTerms, offer: Offer) -> dict[str, Any]:
        deal = await deals.get(deal_id)
        deal.terms = terms
        deal.agreed_at = ctx.clock.now()
        deal.negotiation_outcome = f"agreed in round {offer.round} of {offer.max_rounds}"
        await deals.save(deal, DealState.AGREED)
        await deals.audit(deal.campaign_id, deal.id, "advertiser-agent", "terms_agreed", fixed=terms.fixed_fee, cpm=terms.cpm,
                          cap=terms.cap, terms_hash=terms.terms_hash(), signature=terms.influencer_signature[:18] + "…")  # fmt: skip
        return {"deal_id": deal.id, "outcome": "agreed", "offer": offer.model_dump(mode="json")}

    async def prepare_payments(state: AdvertiserState) -> AdvertiserState:
        spec = (await deals.campaign(state["campaign_id"])).spec
        agreed = [r for r in state.get("results", []) if r["outcome"] == "agreed"]
        # Cheapest fixed fee first, up to max_creators
        agreed.sort(key=lambda r: (float(r["offer"]["fixed_fee"]), r["deal_id"]))
        gated: list[str] = []
        for r in agreed:
            deal = await deals.get(r["deal_id"])
            if len(gated) >= spec.max_creators:
                await deals.save(deal, DealState.REJECTED)
                await deals.audit(spec.id, deal.id, "advertiser-agent", "campaign_full", max_creators=spec.max_creators)
                continue
            deal = await deals.create_cart(deal)
            if (await deals.gate(deal, spec, Offer.model_validate(r["offer"]))).ok:
                gated.append(deal.id)
        await deals.refresh_campaign_status(spec.id)
        return {"gated": gated}

    def needs_approval(state: AdvertiserState) -> str:
        return "approval" if state["gated"] else END

    async def approval(state: AdvertiserState) -> Command:
        spec = (await deals.campaign(state["campaign_id"])).spec
        gated = [await deals.get(i) for i in state["gated"]]
        for d in gated:
            if d.state != "AWAITING_APPROVAL":  # node re-runs on resume; keep the draft that was signed
                await deals.await_signature(d, spec.payer)
        await deals.refresh_campaign_status(state["campaign_id"])
        total = sum(d.terms.cap for d in gated if d.terms)
        decision = interrupt({"campaign_id": state["campaign_id"], "deals": state["gated"], "total": str(total),
                              "payer": spec.payer, "signing": "browser" if ctx.gateway.agent_signing else "hosted_checkout"})  # fmt: skip
        approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
        await deals.audit(state["campaign_id"], None, "human", "approval_decision", approved=approved, total=total, payer=spec.payer)
        if approved:
            signatures = decision.get("signatures", {}) if isinstance(decision, dict) else {}
            return Command(goto="execute_payments", update={"signatures": signatures})
        for d in gated:
            await deals.save(await deals.get(d.id), DealState.REJECTED)
        await deals.refresh_campaign_status(state["campaign_id"])
        return Command(goto=END)

    async def execute_payments(state: AdvertiserState) -> AdvertiserState:
        signatures = state.get("signatures", {})
        for deal_id in state["gated"]:
            await deals.submit_payment(await deals.get(deal_id), signatures.get(deal_id))
        await deals.refresh_campaign_status(state["campaign_id"])
        return {}

    g = StateGraph(AdvertiserState)
    g.add_node("discover", discover)
    g.add_node("negotiate", negotiate)
    g.add_node("prepare_payments", prepare_payments)
    g.add_node("approval", approval, destinations=("execute_payments", END))
    g.add_node("execute_payments", execute_payments)
    g.add_edge(START, "discover")
    g.add_conditional_edges("discover", fan_out, ["negotiate", END])
    g.add_edge("negotiate", "prepare_payments")
    g.add_conditional_edges("prepare_payments", needs_approval, ["approval", END])
    g.add_edge("execute_payments", END)
    return g.compile(checkpointer=checkpointer)
