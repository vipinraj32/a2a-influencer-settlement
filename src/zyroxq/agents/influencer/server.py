"""Influencer agents as A2A servers: one FastAPI sub-app (Agent Card + JSON-RPC) per onboarded creator."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from a2a.helpers import get_data_parts, new_data_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentProvider, AgentSkill
from fastapi import FastAPI
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import InMemorySaver

from ...clock import Clock
from .graph import DisclosureResolver, build_influencer_graph, builtin_resolver
from .identity import InfluencerIdentity

NEGOTIATE_SKILL = "negotiate-sponsorship"
DecisionSink = Callable[[dict[str, Any]], Awaitable[None]]


def agent_card(me: InfluencerIdentity, url: str) -> AgentCard:
    p = me.profile
    return AgentCard(
        name=f"{p.name} — sponsorship agent",
        description=f"Negotiates paid YouTube Shorts for {p.name} ({p.subscribers:,} subs, {p.niche}). "
        f"Signs deal terms with agent key {p.agent_signer}; payee {p.wallet}.",
        version="0.1.0",
        supported_interfaces=[AgentInterface(url=url, protocol_binding="JSONRPC", protocol_version="1.0")],
        provider=AgentProvider(organization="ZyroXQ", url="https://github.com/vipinraj32/a2a-influencer-settlement"),
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(
                id=NEGOTIATE_SKILL,
                name="Negotiate sponsorship",
                description="Accepts an Offer; replies counter / accept (with EIP-191 signed DealTerms) / reject / clarify / escalate.",
                tags=[p.niche, "youtube_short", f"channel:{p.channel_id}"],
            ),
            AgentSkill(id="settlement-updates", name="Settlement updates", description="Receives funded / payout notices.", tags=["hsp"]),
        ],
    )


class InfluencerExecutor(AgentExecutor):
    def __init__(
        self,
        me: InfluencerIdentity,
        clock: Clock,
        llm: BaseChatModel | None,
        inbox: list[dict[str, Any]] | None = None,
        record: DecisionSink | None = None,
        resolve_disclosure: DisclosureResolver = builtin_resolver,
    ) -> None:
        self.me = me
        self.graph = build_influencer_graph(me, clock, llm, InMemorySaver(), resolve_disclosure)
        self.inbox: list[dict[str, Any]] = inbox if inbox is not None else []  # settlement notices, shown in the influencer portal
        self.record = record  # stores the creator's private decision records; nothing of them goes back over A2A

    async def execute(self, context: RequestContext, event_queue) -> None:
        parts = get_data_parts(context.message.parts) if context.message else []
        data = parts[0] if parts else {}
        reply = await self.handle(data)
        await event_queue.enqueue_event(new_data_message(reply, context_id=context.context_id))

    async def handle(self, data: dict[str, Any]) -> dict[str, Any]:
        kind = data.get("type")
        if kind == "offer":
            offer = {**data["offer"], "advertiser": data.get("advertiser", "")}
            config = {"configurable": {"thread_id": offer["deal_id"]}}
            result = await self.graph.ainvoke({"offer": offer, "requirements": str(data.get("requirements", ""))}, config)
            if self.record is not None:
                await self.record(result["decision"])
            return {"type": "negotiation_reply", **result["reply"]}
        if kind in ("funded", "payout"):
            self.inbox.append(data)
            return {"type": "ack", "message": f"{self.me.profile.name} received {kind} for {data.get('deal_id')}"}
        return {"type": "error", "message": f"unsupported message type {kind!r}"}

    async def cancel(self, context: RequestContext, event_queue) -> None:
        return None


def build_influencer_app(
    me: InfluencerIdentity,
    url: str,
    clock: Clock,
    llm: BaseChatModel | None,
    inbox: list[dict[str, Any]] | None = None,
    record: DecisionSink | None = None,
    resolve_disclosure: DisclosureResolver = builtin_resolver,
) -> tuple[FastAPI, InfluencerExecutor]:
    card = agent_card(me, url)
    executor = InfluencerExecutor(me, clock, llm, inbox, record, resolve_disclosure)
    handler = DefaultRequestHandlerV2(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=card)
    app = FastAPI(title=card.name)
    add_a2a_routes_to_fastapi(app, agent_card_routes=create_agent_card_routes(card), jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"))
    return app, executor
