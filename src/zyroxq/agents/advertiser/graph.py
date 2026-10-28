"""Advertiser (Payment) Agent graph.

    discover ──Send×N──▶ negotiate ──▶ prepare_payments ──▶ approval? ──▶ execute_payments ──▶ END
    (Agent Cards)        (A2A, parallel)   (Cart Mandate +       (interrupt)   (sign Payment Mandate,
                                            policy gate)                         submit to HSP)

The LLM only appears inside `negotiate` (proposals, bounded by the policy). Signing is a fixed node.
"""

from __future__ import annotations

import logging
import operator
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

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


class NegotiateInput(TypedDict):
    campaign_id: str
    influencer_id: str


def build_advertiser_graph(deals: DealService, checkpointer: BaseCheckpointSaver):
    ctx = deals.ctx

    async def discover(state: AdvertiserState) -> AdvertiserState:
        campaign = await deals.campaign(state["campaign_id"])
        spec = campaign.spec
        shortlist: list[str] = []
        for inf_id, reg in ctx.registry.items():
            p = reg.profile
            if p.niche != spec.niche or not (spec.min_subscribers <= p.subscribers <= spec.max_subscribers):
                continue
            try:
                card = await ctx.a2a.fetch_card(reg.agent_url)
            except Exception as e:  # noqa: BLE001 - unreachable agents are skipped
                await deals.audit(spec.id, None, "advertiser-agent", "agent_unreachable", influencer=inf_id, error=str(e))
                continue
            if any(s.id == NEGOTIATE_SKILL for s in card.skills):
                shortlist.append(inf_id)
        await deals.audit(spec.id, None, "advertiser-agent", "discovered", shortlist=shortlist, registry_size=len(ctx.registry))
        campaign.status = "negotiating" if shortlist else "failed"
        campaign.error = "" if shortlist else "no matching influencer agents"
        await ctx.store.save_campaign(campaign)
        return {"shortlist": shortlist}

    def fan_out(state: AdvertiserState) -> list[Send] | str:
        if not state["shortlist"]:
            return END
        return [Send("negotiate", {"campaign_id": state["campaign_id"], "influencer_id": i}) for i in state["shortlist"]]

    async def negotiate(inp: NegotiateInput) -> AdvertiserState:
        spec = (await deals.campaign(inp["campaign_id"])).spec
        reg = ctx.registry[inp["influencer_id"]]
        deal = await deals.save(Deal(id=new_id("DEAL"), campaign_id=spec.id, influencer_id=reg.profile.id))

        offer = opening_offer(spec, deal.id)
        actor = f"influencer-agent:{reg.profile.id}"
        while True:
            raw = await ctx.a2a.send(reg.agent_url, {"type": "offer", "advertiser": spec.advertiser, "offer": offer.model_dump(mode="json")},
                                     context_id=deal.id)  # fmt: skip
            reply = NegotiationReply.model_validate(raw)
            await deals.record_round(deal, offer, {"kind": reply.kind, "reason": reply.reason, "decided_by": raw.get("decided_by"),
                                                   "counter": reply.offer.model_dump(mode="json") if reply.offer else None}, actor)  # fmt: skip
            if reply.kind == "accept" and reply.terms is not None:
                return {"results": [await _agree(deal.id, reply.terms, offer)]}
            if reply.kind == "reject" or reply.offer is None:
                await deals.save(deal, DealState.REJECTED)
                return {"results": [{"deal_id": deal.id, "outcome": "rejected"}]}
            move = await next_move(spec, reg.profile, offer, reply.offer, ctx.llm)
            await deals.audit(spec.id, deal.id, "advertiser-agent", "negotiation_move", action=move.action, reason=move.reason,
                              decided_by=move.decided_by, policy_override=move.policy_override)  # fmt: skip
            if move.action == "walk_away" or move.offer is None:
                await deals.save(deal, DealState.REJECTED)
                return {"results": [{"deal_id": deal.id, "outcome": "walked_away"}]}
            offer = move.offer

    async def _agree(deal_id: str, terms: DealTerms, offer: Offer) -> dict[str, Any]:
        deal = await deals.get(deal_id)
        deal.terms = terms
        deal.agreed_at = ctx.clock.now()
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
        gated = [await deals.get(i) for i in state["gated"]]
        if not deals.approval_needed(gated):
            return Command(goto="execute_payments")
        for d in gated:
            await deals.await_approval(d)
        await deals.refresh_campaign_status(state["campaign_id"])
        total = sum(d.terms.cap for d in gated if d.terms)
        decision = interrupt({"campaign_id": state["campaign_id"], "deals": state["gated"], "total": str(total),
                              "threshold": str(ctx.settings.approval_threshold_usd)})  # fmt: skip
        await deals.audit(state["campaign_id"], None, "human", "approval_decision", approved=bool(decision), total=total)
        if decision:
            return Command(goto="execute_payments")
        for d in gated:
            await deals.save(await deals.get(d.id), DealState.REJECTED)
        await deals.refresh_campaign_status(state["campaign_id"])
        return Command(goto=END)

    async def execute_payments(state: AdvertiserState) -> AdvertiserState:
        for deal_id in state["gated"]:
            await deals.sign_and_submit(await deals.get(deal_id))
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
