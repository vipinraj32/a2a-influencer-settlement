from __future__ import annotations

import asyncio
import logging
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from ...models import Campaign, CampaignSpec
from ...services.deals import DealService
from .graph import build_advertiser_graph

log = logging.getLogger(__name__)


class CampaignRunner:
    """Runs one advertiser graph per campaign (thread_id = campaign id) as background tasks."""

    def __init__(self, deals: DealService, checkpointer: BaseCheckpointSaver) -> None:
        self.deals = deals
        self.graph = build_advertiser_graph(deals, checkpointer)
        self._tasks: set[asyncio.Task[Any]] = set()

    async def start(self, spec: CampaignSpec) -> Campaign:
        campaign = Campaign(spec=spec, created_at=self.deals.ctx.clock.now())
        await self.deals.ctx.store.save_campaign(campaign)
        await self.deals.audit(spec.id, None, "human", "campaign_created", budget=spec.budget, niche=spec.niche, max_creators=spec.max_creators)
        self._spawn(spec.id, {"campaign_id": spec.id})
        return campaign

    async def decide(self, campaign_id: str, approved: bool) -> None:
        self._spawn(campaign_id, Command(resume=approved))

    async def pending_approval(self, campaign_id: str) -> dict[str, Any] | None:
        snapshot = await self.graph.aget_state(self._config(campaign_id))
        for task in snapshot.tasks:
            for intr in task.interrupts:
                return intr.value
        return None

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _spawn(self, campaign_id: str, payload: Any) -> None:
        task = asyncio.create_task(self._run(campaign_id, payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, campaign_id: str, payload: Any) -> None:
        try:
            await self.graph.ainvoke(payload, self._config(campaign_id))
        except Exception as e:
            log.exception("campaign %s failed", campaign_id)
            campaign = await self.deals.ctx.store.get_campaign(campaign_id)
            if campaign:
                campaign.status = "failed"
                campaign.error = f"{type(e).__name__}: {e}"
                await self.deals.ctx.store.save_campaign(campaign)
            await self.deals.audit(campaign_id, None, "advertiser-agent", "campaign_error", error=str(e))

    @staticmethod
    def _config(campaign_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": campaign_id}, "recursion_limit": 100}
