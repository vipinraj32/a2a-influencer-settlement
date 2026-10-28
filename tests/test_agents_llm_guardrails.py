"""The LLM proposes; deterministic code decides. These fake LLMs try to break the rules."""

from decimal import Decimal

from langgraph.checkpoint.memory import InMemorySaver

from zyroxq.agents.advertiser.negotiation import AdvertiserDecision, next_move
from zyroxq.agents.influencer.graph import InfluencerDecision, build_influencer_graph
from zyroxq.clock import Clock
from zyroxq.policy import check_terms


class FakeLLM:
    """Duck-types the two calls the agents make: with_structured_output(...).ainvoke(...)."""

    def __init__(self, answer):
        self.answer = answer

    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


async def run_influencer(me, llm, offer):
    graph = build_influencer_graph(me, Clock(), llm, InMemorySaver())
    out = await graph.ainvoke({"offer": offer.model_dump(mode="json")}, {"configurable": {"thread_id": offer.deal_id}})
    return out["reply"]


async def test_influencer_llm_cannot_accept_below_floor(identity, offer):
    me = identity
    lowball = offer.model_copy(update={"fixed_fee": me.profile.rate_card.fixed_min - 5})
    reply = await run_influencer(me, FakeLLM(InfluencerDecision(action="accept", fixed_fee="1", cpm="0.1", note="sure!")), lowball)
    assert reply["kind"] == "counter"
    assert Decimal(reply["offer"]["fixed_fee"]) >= me.profile.rate_card.fixed_min


async def test_influencer_llm_counter_is_raised_to_floor(identity, offer):
    me = identity
    lowball = offer.model_copy(update={"fixed_fee": Decimal("5")})
    reply = await run_influencer(me, FakeLLM(InfluencerDecision(action="counter", fixed_fee="3", cpm="0.5", note="")), lowball)
    assert Decimal(reply["offer"]["fixed_fee"]) == me.profile.rate_card.fixed_min
    assert Decimal(reply["offer"]["cpm"]) == me.profile.rate_card.cpm_min


async def test_influencer_accept_produces_valid_signed_terms(campaign, identity, offer):
    me = identity
    reply = await run_influencer(me, None, offer)  # 20 ≥ 18 floor, 2.5 ≥ 2 CPM → accept
    assert reply["kind"] == "accept"
    from zyroxq.models import DealTerms

    terms = DealTerms.model_validate(reply["terms"])
    assert terms.payee == me.profile.wallet
    assert check_terms(campaign, terms, me.profile, offer).ok  # signed by the agent key the wallet authorized


async def test_influencer_llm_failure_falls_back_to_rules(identity, offer):
    reply = await run_influencer(identity, FakeLLM(RuntimeError("model down")), offer)
    assert reply["kind"] == "accept" and reply["decided_by"] == "rules"


async def test_advertiser_llm_overspend_is_overridden(campaign, influencer, offer):
    counter = offer.model_copy(update={"fixed_fee": Decimal("30")})
    greedy = FakeLLM(AdvertiserDecision(action="accept_counter", fixed_fee="30", cpm="2.5", reason="they're great, pay anything"))
    move = await next_move(campaign, influencer, offer, counter, greedy)
    assert move.decided_by == "rules (LLM overridden)"
    assert move.policy_override and "fixed fee" in move.policy_override[0]
    assert move.offer is not None and move.offer.fixed_fee <= campaign.max_fixed


async def test_advertiser_llm_in_bounds_counter_is_used(campaign, influencer, offer):
    counter = offer.model_copy(update={"fixed_fee": Decimal("30")})
    llm = FakeLLM(AdvertiserDecision(action="counter", fixed_fee="23.50", cpm="2.50", reason="close the gap"))
    move = await next_move(campaign, influencer, offer, counter, llm)
    assert move.decided_by == "llm" and move.offer.fixed_fee == Decimal("23.50") and move.offer.round == 2
