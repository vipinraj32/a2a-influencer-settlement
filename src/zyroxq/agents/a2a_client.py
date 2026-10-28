"""Thin A2A client: send one structured (data-part) message, get the agent's structured reply."""

from __future__ import annotations

from typing import Any

import httpx
from a2a.client import A2ACardResolver, Client, ClientConfig, ClientFactory
from a2a.helpers import get_data_parts, new_data_message
from a2a.types import AgentCard, Role, SendMessageRequest


class A2AConnector:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http
        self._factory = ClientFactory(ClientConfig(streaming=False, httpx_client=http))
        self._clients: dict[str, Client] = {}

    async def fetch_card(self, agent_url: str) -> AgentCard:
        return await A2ACardResolver(self.http, agent_url).get_agent_card()

    async def send(self, agent_url: str, data: dict[str, Any], context_id: str | None = None) -> dict[str, Any]:
        client = self._clients.get(agent_url)
        if client is None:
            client = self._clients[agent_url] = await self._factory.create_from_url(agent_url)
        message = new_data_message(data, role=Role.ROLE_USER, context_id=context_id)
        async for response in client.send_message(SendMessageRequest(message=message)):
            if response.HasField("message"):
                parts = get_data_parts(response.message.parts)
                if parts:
                    return parts[0]
        raise RuntimeError(f"A2A agent at {agent_url} returned no structured reply")
