"""A dropped database connection is a clear, retryable error: requests get 503, a campaign run that never started
is retried, and a run that already started is never repeated."""

import psycopg
from langgraph.checkpoint.memory import InMemorySaver

from zyroxq.agents.advertiser.runner import CampaignRunner

from .test_e2e import client_for, onboard_advertiser, server  # noqa: F401 - pytest fixtures

DROPPED = psycopg.OperationalError("consuming input failed: server closed the connection unexpectedly")


async def test_requests_get_503_when_the_database_drops(server):
    app, _ = server
    advertiser_id = await onboard_advertiser(app)
    store = app.state.zx.ctx.store

    async def down():
        raise DROPPED

    store.list_campaigns = down
    res = await client_for(app, advertiser_id).get("/api/campaigns")
    assert res.status_code == 503 and res.headers["retry-after"] == "5" and "try again" in res.json()["detail"]


class FlakyGraph:
    def __init__(self, failures: int, started: bool):
        self.failures, self.started, self.calls = failures, started, 0

    async def ainvoke(self, payload, config):
        self.calls += 1
        if self.calls <= self.failures:
            raise DROPPED

    async def aget_state(self, config):
        return type("Snapshot", (), {"created_at": "2026-11-01T00:00:00Z" if self.started else None})()


async def test_run_is_retried_only_before_it_started(server, monkeypatch):
    app, _ = server
    monkeypatch.setattr("zyroxq.agents.advertiser.runner.asyncio.sleep", lambda s: _noop())
    runner = CampaignRunner(app.state.zx.deals, InMemorySaver())

    runner.graph = FlakyGraph(failures=1, started=False)  # failed while loading the first checkpoint
    await runner._run("CMP-none", {"campaign_id": "CMP-none"})
    assert runner.graph.calls == 2

    runner.graph = FlakyGraph(failures=1, started=True)  # failed mid-run: retrying could repeat side effects
    await runner._run("CMP-none", {"campaign_id": "CMP-none"})
    assert runner.graph.calls == 1


async def _noop():
    return None
