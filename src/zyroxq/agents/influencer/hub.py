"""Serves every onboarded influencer's A2A agent under one mount: /a2a/influencers/{id}/...

Agents are built on first request from the stored profile and rebuilt when the creator edits it, so
new sign-ups are discoverable without restarting the server.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI
from langchain_core.language_models import BaseChatModel
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from ...clock import Clock
from .graph import DisclosureResolver, builtin_resolver
from .identity import InfluencerIdentity
from .server import DecisionSink, InfluencerExecutor, build_influencer_app

IdentityLookup = Callable[[str], Awaitable[InfluencerIdentity | None]]


class InfluencerHub:
    def __init__(
        self,
        lookup: IdentityLookup,
        agent_url: Callable[[str], str],
        clock: Clock,
        llm: BaseChatModel | None,
        record: DecisionSink | None = None,
        resolve_disclosure: DisclosureResolver = builtin_resolver,
    ) -> None:
        self.lookup = lookup
        self.agent_url = agent_url
        self.clock = clock
        self.llm = llm
        self.record = record
        self.resolve_disclosure = resolve_disclosure
        self._apps: dict[str, tuple[str, FastAPI, InfluencerExecutor]] = {}
        self._inboxes: dict[str, list[dict[str, Any]]] = {}

    def inbox(self, influencer_id: str) -> list[dict[str, Any]]:
        return self._inboxes.setdefault(influencer_id, [])

    async def agent(self, influencer_id: str) -> tuple[FastAPI, InfluencerExecutor] | None:
        me = await self.lookup(influencer_id)
        if me is None:
            self._apps.pop(influencer_id, None)
            return None
        policy = me.policy.model_dump(mode="json") if me.policy else None
        fingerprint = json.dumps([me.profile.model_dump(mode="json"), me.persona, policy, me.lessons], sort_keys=True)
        cached = self._apps.get(influencer_id)
        if cached is None or cached[0] != fingerprint:
            app, executor = build_influencer_app(me, self.agent_url(influencer_id), self.clock, self.llm, self.inbox(influencer_id),
                                                 self.record, self.resolve_disclosure)  # fmt: skip
            cached = self._apps[influencer_id] = (fingerprint, app, executor)
        return cached[1], cached[2]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        root = scope.get("root_path", "")
        rest = scope["path"][len(root) :] if scope["path"].startswith(root) else scope["path"]
        influencer_id, _, _ = rest.lstrip("/").partition("/")
        found = await self.agent(influencer_id) if influencer_id else None
        if found is None:
            await JSONResponse({"detail": "no active influencer agent with this id"}, status_code=404)(scope, receive, send)
            return
        await found[0]({**scope, "root_path": f"{root}/{influencer_id}"}, receive, send)
