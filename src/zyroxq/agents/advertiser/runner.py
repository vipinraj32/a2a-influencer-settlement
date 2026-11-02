from __future__ import annotations

import asyncio
import logging
from typing import Any

import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from ...models import Campaign, CampaignSpec
from ...services.deals import DealError, DealService
from .graph import build_advertiser_graph

log = logging.getLogger(__name__)
START_ATTEMPTS = 3  # a run that fails to reach the database before it starts is retried this many times
# Steps a restarted server may resume on its own: negotiation and the cart/policy gate. Approval waits for the
# advertiser, and payment steps are never re-entered automatically.
RESUMABLE = {"discover", "negotiate_next", "prepare_payments"}


class CampaignRunner:
    """Runs one advertiser graph per campaign (thread_id = campaign id) as background tasks. At most one run per
    campaign is active at a time; progress lives in the checkpointer and the negotiation sessions, so `recover`
    picks unfinished campaigns up again after a restart."""

    def __init__(self, deals: DealService, checkpointer: BaseCheckpointSaver) -> None:
        self.deals = deals
        self.graph = build_advertiser_graph(deals, checkpointer)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._active: dict[str, asyncio.Task[Any]] = {}

    async def start(self, spec: CampaignSpec) -> Campaign:
        campaign = Campaign(spec=spec, created_at=self.deals.ctx.clock.now())
        await self.deals.save_campaign(campaign)
        await self.deals.audit(spec.id, None, "human", "campaign_created", name=spec.name, budget=spec.budget, niche=spec.niche, max_creators=spec.max_creators)
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

    def running(self, campaign_id: str) -> bool:
        task = self._active.get(campaign_id)
        return task is not None and not task.done()

    async def recover(self) -> list[str]:
        """Resumes campaigns a stopped server left mid-negotiation. Each resumes from its last checkpoint: finished
        creators keep their outcomes, a creator cut off mid-negotiation is closed as FAILED, and the queue continues."""
        resumed: list[str] = []
        for campaign in await self.deals.ctx.store.list_campaigns():
            cid = campaign.spec.id
            if campaign.status not in ("planning", "negotiating") or self.running(cid):
                continue
            try:
                snapshot = await self.graph.aget_state(self._config(cid))
            except Exception:  # noqa: BLE001 - a checkpoint this graph cannot read (older version): leave it alone
                log.warning("campaign %s: checkpoint not resumable", cid, exc_info=True)
                continue
            if snapshot.created_at is None:
                if campaign.status == "planning":  # saved, but its run never started
                    self._spawn(cid, {"campaign_id": cid})
                    resumed.append(cid)
                continue
            if any(t.interrupts for t in snapshot.tasks) or not snapshot.next or not set(snapshot.next) <= RESUMABLE:
                continue
            await self.deals.audit(cid, None, "advertiser-agent", "campaign_resumed", step=list(snapshot.next))
            self._spawn(cid, None)  # None = continue from the checkpoint
            resumed.append(cid)
        if resumed:
            log.info("resumed %s campaign run(s) after restart: %s", len(resumed), ", ".join(resumed))
        return resumed

    async def shutdown(self, grace: float = 10) -> None:
        """Lets running campaigns finish for `grace` seconds, then stops them. They resume from their checkpoint on
        the next start (`recover`); a creator cut off mid-negotiation is closed as FAILED then, never re-run."""
        if self._tasks:
            _, pending = await asyncio.wait(list(self._tasks), timeout=grace)
            for task in pending:
                task.cancel()
            if pending:
                log.warning("stopped %s running campaign(s); they resume on the next start", len(pending))
        await self.wait_idle()

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _spawn(self, campaign_id: str, payload: Any) -> None:
        if self.running(campaign_id):  # a second run on the same thread would negotiate or pay twice
            raise DealError(f"campaign {campaign_id} is already running")
        task = asyncio.create_task(self._run(campaign_id, payload))
        self._tasks.add(task)
        self._active[campaign_id] = task
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(lambda t: self._active.pop(campaign_id, None) if self._active.get(campaign_id) is t else None)

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
