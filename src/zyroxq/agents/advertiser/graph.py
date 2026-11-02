"""Advertiser (Payment) Agent graph.

    discover ──▶ negotiate_next ──▶ prepare_payments ──▶ approval ──▶ execute_payments ──▶ END
    (Agent Cards,   ↺ one creator     (Cart Mandate +       (interrupt:   (verify signatures,
     queue one       per step, in      policy gate)           wallet signs   submit Payment
     session per     selection order)                         in browser)    Mandates to HSP)
     creator)

Creators are negotiated with one at a time. Each `negotiate_next` step runs one creator's whole negotiation and is
checkpointed, so a restarted server resumes with the next creator; a negotiation that was cut off mid-way is closed
as FAILED (never re-run), and a rejected, failed or timed-out creator never blocks the ones after it.

The LLM only appears inside `negotiate` (proposals, bounded by the policy). The advertiser's own
wallet signs; the graph only verifies and submits.
"""

from __future__ import annotations

import asyncio
import logging
import operator
from decimal import Decimal
from typing import Annotated, Any, TypedDict

import httpx
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ...agent_policy import screen_creator
from ...feedback import lessons_for
from ...models import Deal, DealState, DealTerms, NegotiationReply, Offer, new_id
from ...services.deals import DealService
from .. import chat
from ..influencer.server import NEGOTIATE_SKILL
from .negotiation import next_move, opening_offer

log = logging.getLogger(__name__)


class AdvertiserState(TypedDict, total=False):
    campaign_id: str
    shortlist: list[str]
    results: Annotated[list[dict[str, Any]], operator.add]
    gated: list[str]
    signatures: dict[str, str]
    remaining: int  # creators still QUEUED after the last negotiate_next step


class AgentTimeout(Exception):
    pass


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
        shortlist = list(dict.fromkeys(shortlist))  # a creator listed twice is still negotiated with once
        await deals.audit(spec.id, None, "advertiser-agent", "discovered", shortlist=shortlist, registry_size=len(registry))
        campaign.status = "negotiating" if shortlist else "failed"
        campaign.error = "" if shortlist else f"no active {spec.niche} influencer agents match this campaign yet"
        await deals.save_campaign(campaign)
        if shortlist:
            await deals.queue_negotiations(spec, shortlist)
        return {"shortlist": shortlist}

    def after_discover(state: AdvertiserState) -> str:
        return "negotiate_next" if state["shortlist"] else END

    async def negotiate_next(state: AdvertiserState) -> AdvertiserState:
        """One creator per step: close anything a dead run left RUNNING, start the next QUEUED creator, run its whole
        negotiation, and report how many are still waiting. The graph loops here until the queue is empty."""
        campaign_id = state["campaign_id"]
        results: list[dict[str, Any]] = []
        for session in await deals.negotiation_sessions(campaign_id):
            if session["status"] == "RUNNING":  # this step was cut off last time (server restart): never run it twice
                await deals.abandon_session(session, "interrupted: the server restarted during this negotiation")
                results.append({"deal_id": session["negotiation_id"], "outcome": "interrupted"})
        if not (await deals.campaign(campaign_id)).negotiation_cancelled:
            queued = [x for x in await deals.negotiation_sessions(campaign_id) if x["status"] == "QUEUED"]
            if queued and (session := await deals.start_session(queued[0])) is not None:
                try:
                    results.append(await negotiate(session))
                except Exception as e:  # noqa: BLE001 - failed before or after the talks themselves: close it, keep the queue going
                    log.exception("negotiation %s could not run", session["negotiation_id"])
                    await deals.abandon_session(session, f"negotiation failed: {type(e).__name__}",
                                                "This negotiation could not run. Nothing was agreed or paid.")  # fmt: skip
                    results.append({"deal_id": session["negotiation_id"], "outcome": "error"})
        cancelled = (await deals.campaign(campaign_id)).negotiation_cancelled
        remaining = 0 if cancelled else sum(1 for x in await deals.negotiation_sessions(campaign_id) if x["status"] == "QUEUED")
        return {"results": results, "remaining": remaining}

    def after_negotiation(state: AdvertiserState) -> str:
        return "negotiate_next" if state.get("remaining") else "prepare_payments"

    async def ask(call: Any) -> Any:
        """An agent reply, bounded: a creator agent or LLM that does not answer in time ends this negotiation only."""
        try:
            return await asyncio.wait_for(call, ctx.settings.negotiation_reply_timeout_seconds)
        except (TimeoutError, httpx.TimeoutException) as e:
            raise AgentTimeout(str(e) or "no reply in time") from e

    async def negotiate(session: dict[str, Any]) -> dict[str, Any]:
        """One creator, start to finish. Bounded: at most `spec.max_rounds` offers; failure (decline, no movement,
        escalation, timeout, error) is a valid outcome. Every message is persisted and streamed the moment its agent
        produces it (DealService.negotiation_event), and every ending goes through `conclude`."""
        spec = (await deals.campaign(session["campaign_id"])).spec
        deal_id = session["negotiation_id"]
        reg = await ctx.registry.get(session["creator_id"])
        if reg is None:
            await deals.finish_session(session, "error", reason="the creator's agent is no longer active")
            return {"deal_id": deal_id, "outcome": "error"}
        policy = await deals.advertiser_policy(spec)
        deal = await deals.ctx.store.get_deal(deal_id) or await deals.save(Deal(id=deal_id, campaign_id=spec.id, influencer_id=reg.profile.id))
        us = ("advertiser", f"advertiser-agent:{spec.owner_id}", f"{spec.advertiser} agent")
        them = ("influencer", f"influencer-agent:{reg.profile.id}", f"{reg.profile.name}'s agent")
        system = ("system", "zyroxq", "ZyroXQ")

        async def emit(who: tuple[str, str, str], event_type: str, **kw: Any) -> dict[str, Any]:
            return await deals.negotiation_event(deal, event_type, *who, **kw)

        async def conclude(outcome: str, event_type: str, content: str, *, state: DealState | None = None, reason: str = "",
                           final_offer: Offer | None = None, **kw: Any) -> dict[str, Any]:  # fmt: skip
            """The single exit of a negotiation: deal state, ending message, session outcome, then negotiation_completed."""
            if state is not None:
                current = await deals.get(deal.id)
                if current.state == DealState.NEGOTIATING:
                    await deals.end_negotiation(current, state, reason)
            await emit(system, event_type, content=content, outcome=outcome, **kw)
            await deals.finish_session(session, outcome, final_offer=final_offer, reason=reason)
            await emit(system, "negotiation_completed", outcome=outcome)
            return {"deal_id": deal.id, "outcome": outcome}

        failed = [c for c in screen_creator(spec, policy, reg.profile) if not c.passed]
        if failed:
            await deals.audit(spec.id, deal.id, "advertiser-agent", "negotiation_move", action="screened_out",
                              failed=[f"{c.name}: {c.detail}" for c in failed])  # fmt: skip
            return await conclude("screened_out", "negotiation_rejected", f"{us[2]} did not open talks: this channel does not meet the campaign's "
                                  "evidence requirements.", state=DealState.REJECTED,
                                  reason="creator does not meet the advertiser's evidence requirements")  # fmt: skip

        deals.negotiation_started(deal.id)
        try:
            lessons = await lessons_for(ctx.store, spec.owner_id, "advertiser", niche=spec.niche, compensation_model=spec.compensation_model)
            if lessons:
                await deals.audit(spec.id, deal.id, "advertiser-agent", "lessons_applied", lessons=[x["id"] for x in lessons])
            await emit(system, "negotiation_started", content=f"{us[2]} opened talks with {them[2]}.", max_rounds=spec.max_rounds,
                       compensation_model=spec.compensation_model)  # fmt: skip
            offer = opening_offer(spec, deal.id, policy)
            offer = offer.model_copy(update={"note": chat.advertiser_opening(spec.advertiser, reg.profile.name, offer, spec.brief)})
            counters: list[Offer] = []
            intent = "offer"  # what our next message does: open, counter, or accept the creator's counter
            while True:
                if deals.cancel_requested(deal.id):
                    return await conclude("cancelled", "negotiation_rejected", "Cancelled by the advertiser.", state=DealState.REJECTED,
                                          reason="cancelled by the advertiser")  # fmt: skip
                # offer.note is this round's chat message: the creator's agent receives it with the offer
                sent = await emit(us, "message_completed", content=offer.note or chat.pay_words(offer), offer=offer, intent=intent,
                                  message_id=new_id("MSG"))  # fmt: skip
                await emit(us, "offer_created" if offer.round == 1 else "counter_offer_created", offer=offer, message_id=sent["message_id"])
                await deals.session_offer(session, offer)
                await emit(them, "agent_thinking", round=offer.round)
                message = {"type": "offer", "advertiser": spec.advertiser, "requirements": spec.requirements_document(), "offer": offer.model_dump(mode="json")}
                raw = await ask(ctx.a2a.send(reg.agent_url, message, context_id=deal.id))
                reply = NegotiationReply.model_validate(raw)
                await deals.record_round(deal, offer, {"kind": reply.kind, "reason": reply.reason, "decided_by": raw.get("decided_by"),
                                                       "counter": reply.offer.model_dump(mode="json") if reply.offer else None}, them[1])  # fmt: skip
                answered = await emit(them, "message_completed", content=reply.reason, round=offer.round, reply=reply.kind, message_id=new_id("MSG"),
                                      offer=reply.offer if reply.kind in ("counter", "accept") else None)  # fmt: skip
                if reply.kind == "accept" and reply.terms is not None:
                    await _agree(deal.id, reply.terms, offer)
                    t = reply.terms
                    agreed = {"fixed_fee": t.fixed_fee, "cpm": t.cpm, "cap": t.cap, "publish_by": t.publish_by, "deliverable": t.deliverable}
                    return await conclude("agreed", "negotiation_agreed", f"Agreed in round {offer.round}.", final_offer=offer,
                                          terms_hash=t.terms_hash(), terms=agreed)  # fmt: skip
                if reply.kind in ("clarify", "escalate"):
                    return await conclude("escalated", "negotiation_rejected", f"Paused for a human decision: {reply.reason}",
                                          state=DealState.ESCALATED, reason=f"creator's agent: {reply.reason}")  # fmt: skip
                if reply.kind == "reject" or reply.offer is None:
                    timeout = offer.round >= spec.max_rounds
                    return await conclude("timeout" if timeout else "rejected", "negotiation_timeout" if timeout else "negotiation_rejected",
                                          f"No deal: {them[2]} declined.", state=DealState.REJECTED,
                                          reason=f"creator declined: {reply.reason}")  # fmt: skip
                await emit(them, "counter_offer_created", offer=reply.offer, message_id=answered["message_id"])
                await deals.session_offer(session, reply.offer)
                await emit(us, "agent_thinking", round=offer.round + 1)
                move = await ask(next_move(spec, reg.profile, offer, reply.offer, ctx.llm, policy, counters, ctx.clock.now(),
                                           lessons=[x["guidance"] for x in lessons]))  # fmt: skip
                counters.append(reply.offer)
                if move.record is not None:
                    await deals.record_decision(move.record)
                await deals.audit(spec.id, deal.id, "advertiser-agent", "negotiation_move", action=move.action, reason=move.reason,
                                  decided_by=move.decided_by, policy_override=move.policy_override, round=offer.round + 1)  # fmt: skip
                outcome = move.record.public_explanation if move.record else move.reason
                if move.action == "escalate":
                    return await conclude("escalated", "negotiation_rejected", "Paused: the advertiser needs to review this one.",
                                          state=DealState.ESCALATED, reason=f"advertiser review needed: {outcome}")  # fmt: skip
                if move.action == "walk_away" or move.offer is None:
                    await emit(us, "message_completed", content=chat.advertiser_walks_away(reg.profile.name, outcome), round=offer.round + 1, reply="reject",
                               message_id=new_id("MSG"))  # fmt: skip
                    timeout = offer.round >= spec.max_rounds
                    return await conclude("timeout" if timeout else "walked_away", "negotiation_timeout" if timeout else "negotiation_rejected",
                                          "No agreement within the round limit." if timeout else f"{us[2]} walked away.",
                                          state=DealState.REJECTED, reason=outcome)  # fmt: skip
                said = (chat.advertiser_accepts(move.offer) if move.action == "accept_counter"
                        else chat.advertiser_counter(move.offer, offer, last_round=move.offer.round >= spec.max_rounds))  # fmt: skip
                offer = move.offer.model_copy(update={"note": said})
                intent = "accept" if move.action == "accept_counter" else "counter"
        except AgentTimeout:
            seconds = ctx.settings.negotiation_reply_timeout_seconds
            await deals.audit(spec.id, deal.id, "advertiser-agent", "negotiation_timeout", seconds=seconds)
            return await conclude("no_reply", "negotiation_timeout", f"An agent did not reply within {seconds:g} seconds. Nothing was agreed or paid.",
                                  state=DealState.REJECTED, reason=f"no reply within {seconds:g}s")  # fmt: skip
        except Exception as e:  # an unreachable or failing agent ends this negotiation only, with a visible error
            log.exception("negotiation %s failed", deal.id)
            await deals.audit(spec.id, deal.id, "advertiser-agent", "negotiation_error", error=f"{type(e).__name__}: {e}")
            return await conclude("error", "negotiation_error", "The negotiation stopped because an agent could not respond. Nothing was agreed or paid.",
                                  state=DealState.REJECTED, reason=f"negotiation failed: {type(e).__name__}", error=type(e).__name__)  # fmt: skip
        finally:
            deals.negotiation_stopped(deal.id)

    async def _agree(deal_id: str, terms: DealTerms, offer: Offer) -> None:
        deal = await deals.get(deal_id)
        deal.terms = terms
        deal.agreed_at = ctx.clock.now()
        deal.negotiation_outcome = f"agreed in round {offer.round} of {offer.max_rounds}"
        deal = await deals.save(deal, DealState.AGREED)
        await deals.record_performance(deal)
        await deals.audit(deal.campaign_id, deal.id, "advertiser-agent", "terms_agreed", fixed=terms.fixed_fee, cpm=terms.cpm,
                          cap=terms.cap, terms_hash=terms.terms_hash(), signature=terms.influencer_signature[:18] + "…")  # fmt: skip

    async def prepare_payments(state: AdvertiserState) -> AdvertiserState:
        spec = (await deals.campaign(state["campaign_id"])).spec
        # From the persisted outcomes, not the run's memory: a resumed run sees every agreement
        agreed = [x for x in await deals.negotiation_sessions(spec.id) if x["status"] == "AGREED" and x["final_offer"]]
        # Cheapest fixed fee first, up to max_creators
        agreed.sort(key=lambda x: (Decimal(x["final_offer"]["fixed_fee"]), x["negotiation_id"]))
        gated: list[str] = []
        for x in agreed:
            deal = await deals.get(x["negotiation_id"])
            if deal.state not in (DealState.AGREED, DealState.CART_CREATED):
                continue  # handled by an earlier attempt of this step
            if len(gated) >= spec.max_creators:
                await deals.save(deal, DealState.REJECTED)
                await deals.audit(spec.id, deal.id, "advertiser-agent", "campaign_full", max_creators=spec.max_creators)
                continue
            if deal.state == DealState.AGREED:
                deal = await deals.create_cart(deal)
            if (await deals.gate(deal, spec, Offer.model_validate(x["final_offer"]["offer"]))).ok:
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
    g.add_node("negotiate_next", negotiate_next)
    g.add_node("prepare_payments", prepare_payments)
    g.add_node("approval", approval, destinations=("execute_payments", END))
    g.add_node("execute_payments", execute_payments)
    g.add_edge(START, "discover")
    g.add_conditional_edges("discover", after_discover, ["negotiate_next", END])
    g.add_conditional_edges("negotiate_next", after_negotiation, ["negotiate_next", "prepare_payments"])
    g.add_conditional_edges("prepare_payments", needs_approval, ["approval", END])
    g.add_edge("execute_payments", END)
    return g.compile(checkpointer=checkpointer)
