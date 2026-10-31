from __future__ import annotations

import asyncio
import logging
from typing import Any

import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from ...models import Campaign, CampaignSpec
from ...services.deals import DealService
from .graph import build_advertiser_graph

log = logging.getLogger(__name__)
START_ATTEMPTS = 3  # a run that fails to reach the database before it starts is retried this many times


class CampaignRunner:
    """Runs one advertiser graph per campaign (thread_id = campaign id) as background tasks."""

    def __init__(self, deals: DealService, checkpointer: BaseCheckpointSaver) -> None:
        self.deals = deals
        self.graph = build_advertiser_graph(deals, checkpointer)
        self._tasks: set[asyncio.Task[Any]] = set()

    async def start(self, spec: CampaignSpec) -> Campaign:
        campaign = Campaign(spec=spec, created_at=self.deals.ctx.clock.now())
        await self.deals.save_campaign(campaign)
        await self.deals.audit(spec.id, None, "human", "campaign_created", budget=spec.budget, niche=spec.niche, max_creators=spec.max_creators)
        self._spawn(spec.id, {"campaign_id": spec.id})
        return campaign

    async def decide(self, campaign_id: str, approved: bool, signatures: dict[str, str] | None = None) -> None:
        self._spawn(campaign_id, Command(resume={"approved": approved, "signatures": signatures or {}}))

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
        for attempt in range(1, START_ATTEMPTS + 1):
            try:
                await self.graph.ainvoke(payload, self._config(campaign_id))
                return
            except psycopg.OperationalError as e:
                # Retrying is only safe while the run has not checkpointed anything: a later failure could repeat side effects
                if attempt < START_ATTEMPTS and not await self._started(campaign_id):
                    log.warning("campaign %s: database unavailable before the run started, retrying (%s)", campaign_id, attempt)
                    await asyncio.sleep(2 * attempt)
                    continue
                await self._fail(campaign_id, e)
                return
            except Exception as e:
                await self._fail(campaign_id, e)
                return

    async def _started(self, campaign_id: str) -> bool:
        try:
            return (await self.graph.aget_state(self._config(campaign_id))).created_at is not None
        except Exception:  # noqa: BLE001 - cannot tell: treat as started, so nothing runs twice
            return True

    async def _fail(self, campaign_id: str, e: Exception) -> None:
        log.error("campaign %s failed: %s: %s", campaign_id, type(e).__name__, e, exc_info=not isinstance(e, psycopg.OperationalError))
        try:
            campaign = await self.deals.ctx.store.get_campaign(campaign_id)
            if campaign:
                campaign.status = "failed"
                transient = isinstance(e, psycopg.OperationalError)
                campaign.error = "the database was unreachable; start the campaign again" if transient else f"{type(e).__name__}: {e}"
                await self.deals.save_campaign(campaign)
            await self.deals.audit(campaign_id, None, "advertiser-agent", "campaign_error", error=f"{type(e).__name__}: {e}")
        except Exception:  # the database is still down: the log line above is all we can record
            log.exception("campaign %s: could not record the failure", campaign_id)

    @staticmethod
    def _config(campaign_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": campaign_id}, "recursion_limit": 100}
