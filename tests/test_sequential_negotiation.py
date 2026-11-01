"""Creators are negotiated with one at a time, in selection order, each exactly once: every outcome (agreed,
rejected, failed, timed out, cancelled) is persisted and the queue moves on. Every transcript event reaches the
browser while its negotiation is still running, routed by negotiation_id, and a restarted server resumes the queue
without repeating a negotiation."""

import asyncio
import json
import socket
import time
from datetime import UTC, datetime

import httpx
import uvicorn

from zyroxq.agents.advertiser.runner import CampaignRunner
from zyroxq.api.app import create_app
from zyroxq.auth.tokens import issue_user_token
from zyroxq.config import Settings
from zyroxq.models import Campaign, CampaignSpec, DealState
from zyroxq.services.deals import campaign_progress

from .test_e2e import CAMPAIGN, MCP_TOKEN, client_for, onboard_advertiser, onboard_influencer, server  # noqa: F401 - pytest fixtures

TERMINAL = {"AGREED", "REJECTED", "TIMED_OUT", "FAILED", "CANCELLED"}


async def creators(app, *names: str) -> str:
    rates = {"ava": (42_000, "15", "2", "22"), "kai": (50_000, "20", "2", "30"), "leo": (260_000, "60", "4", "75")}
    for name in names:  # onboarding order is the discovery (selection) order
        await onboard_influencer(app, name, "fitness", *rates[name])
    return await onboard_advertiser(app)


def record(app) -> list[dict]:
    """Every live event, in the order the browsers receive them."""
    seen: list[dict] = []
    publish = app.state.zx.ctx.bus.publish

    def spy(event, audience):
        seen.append(json.loads(json.dumps(event, default=str)))
        publish(event, audience)

    app.state.zx.ctx.bus.publish = spy
    return seen


async def start(app, advertiser_id: str, wait: bool = True) -> str:
    res = await client_for(app, advertiser_id).post("/api/campaigns", json=CAMPAIGN)
    assert res.status_code == 201, res.text
    if wait:
        await app.state.zx.runner.wait_idle()
    return res.json()["spec"]["id"]


async def queue(app, advertiser_id: str, cid: str) -> dict:
    res = await client_for(app, advertiser_id).get(f"/api/campaigns/{cid}/negotiations")
    assert res.status_code == 200, res.text
    return res.json()


def by_creator(q: dict) -> dict[str, dict]:
    return {s["creator_id"].removeprefix("USR-"): s for s in q["sessions"]}


def patch_send(app, fn):
    a2a = app.state.zx.ctx.a2a
    real = a2a.send

    async def send(url, data, context_id=None):
        return await fn(real, url, data, context_id)

    a2a.send = send


def assert_sequential(seen: list[dict], order: list[str]) -> None:
    """Each negotiation's events form one contiguous block, blocks in queue order, ids 1..n, routed to their own deal."""
    events = [e["event"] for e in seen if e["type"] == "negotiation"]
    blocks: list[str] = []
    for e in events:
        if not blocks or blocks[-1] != e["negotiation_id"]:
            blocks.append(e["negotiation_id"])
    assert blocks == order, "a negotiation started before the previous one finished, or one ran twice"
    for deal_id in order:
        mine = [e for e in events if e["negotiation_id"] == deal_id]
        assert [e["event_id"] for e in mine] == list(range(1, len(mine) + 1))
        assert mine[-1]["event_type"] == "negotiation_completed"
        assert len({e["creator_id"] for e in mine}) == 1 and len({e["campaign_id"] for e in mine}) == 1


async def test_two_creators_run_one_after_the_other(server):
    app, _ = server
    advertiser_id = await creators(app, "ava", "kai")
    seen = record(app)
    cid = await start(app, advertiser_id)
    q = await queue(app, advertiser_id, cid)
    sessions = q["sessions"]
    assert [s["creator_id"] for s in sessions] == ["USR-ava", "USR-kai"] and [s["position"] for s in sessions] == [0, 1]
    assert all(s["status"] == "AGREED" and s["started_at"] and s["completed_at"] for s in sessions)
    assert sessions[0]["completed_at"] <= sessions[1]["started_at"]  # B starts only after A reached a terminal state
    assert_sequential(seen, [s["negotiation_id"] for s in sessions])
    p = q["progress"]
    assert (p["total_creators"], p["completed_count"], p["agreed_count"], p["workflow_status"]) == (2, 2, 2, "completed")
    assert p["current_negotiation_id"] is None
    # progress was pushed along the way: B was QUEUED while A ran
    pushed = [e["progress"] for e in seen if e["type"] == "negotiation_progress"]
    assert any(x["current_creator_id"] == "USR-ava" and x["queued_count"] == 1 for x in pushed)
    assert any(x["current_creator_id"] == "USR-kai" and x["completed_count"] == 1 for x in pushed)


async def test_three_creators_mixed_outcomes_each_exactly_once(server):
    app, _ = server
    advertiser_id = await creators(app, "leo", "ava", "kai")  # leo first: out of budget
    seen = record(app)
    cid = await start(app, advertiser_id)
    q = await queue(app, advertiser_id, cid)
    s = by_creator(q)
    assert [x["creator_id"] for x in q["sessions"]] == ["USR-leo", "USR-ava", "USR-kai"]
    assert s["leo"]["status"] == "REJECTED" and s["ava"]["status"] == "AGREED" and s["kai"]["status"] == "AGREED"
    assert_sequential(seen, [x["negotiation_id"] for x in q["sessions"]])
    deals = (await client_for(app, advertiser_id).get(f"/api/campaigns/{cid}/deals")).json()
    assert sorted(d["influencer_id"] for d in deals) == ["USR-ava", "USR-kai", "USR-leo"]  # one deal per creator
    p = q["progress"]
    assert (p["agreed_count"], p["rejected_count"], p["failed_count"], p["completed_count"]) == (2, 1, 0, 3)
    caps = sum(float(s[n]["final_offer"]["cap"]) for n in ("ava", "kai"))
    assert float(p["agreed_spend"]) == caps and float(p["proposed_spend"]) >= caps  # leo's last offer counts as proposed only
    assert s["leo"]["final_offer"] is None and s["leo"]["latest_offer"] is not None
    # the agreed deals went on to the signing step; the rejected one did not
    assert {d["influencer_id"]: d["state"] for d in deals}["USR-leo"] == DealState.REJECTED


async def test_first_creator_fails_second_still_agrees(server):
    app, _ = server
    advertiser_id = await creators(app, "ava", "kai")

    async def fail_ava(real, url, data, context_id):
        if "USR-ava" in url and data.get("type") == "offer":
            raise httpx.ConnectError("agent down")
        return await real(url, data, context_id)

    patch_send(app, fail_ava)
    seen = record(app)
    cid = await start(app, advertiser_id)
    q = await queue(app, advertiser_id, cid)
    s = by_creator(q)
    assert s["ava"]["status"] == "FAILED" and s["ava"]["outcome"] == "error" and "ConnectError" in s["ava"]["failure_reason"]
    assert s["kai"]["status"] == "AGREED"
    assert_sequential(seen, [x["negotiation_id"] for x in q["sessions"]])
    assert q["progress"]["failed_count"] == 1 and q["progress"]["workflow_status"] == "completed"


async def test_agent_timeout_ends_that_creator_only(server):
    app, _ = server
    advertiser_id = await creators(app, "ava", "kai")
    app.state.zx.ctx.settings.negotiation_reply_timeout_seconds = 0.3

    async def slow_ava(real, url, data, context_id):
        if "USR-ava" in url and data.get("type") == "offer":
            await asyncio.sleep(10)
        return await real(url, data, context_id)

    patch_send(app, slow_ava)
    started = time.monotonic()
    cid = await start(app, advertiser_id)
    assert time.monotonic() - started < 8  # never waits for the slow agent
    s = by_creator(await queue(app, advertiser_id, cid))
    assert s["ava"]["status"] == "TIMED_OUT" and s["ava"]["outcome"] == "no_reply"
    assert s["kai"]["status"] == "AGREED"
    events = (await client_for(app, advertiser_id).get(f"/api/negotiations/{s['ava']['negotiation_id']}/events")).json()
    assert [e["event_type"] for e in events][-2:] == ["negotiation_timeout", "negotiation_completed"]


async def test_duplicate_start_request_starts_one_campaign(server):
    app, _ = server
    advertiser_id = await creators(app, "ava")
    adv = client_for(app, advertiser_id)
    headers = {"Idempotency-Key": "form-submit-1"}
    first, second = await asyncio.gather(adv.post("/api/campaigns", json=CAMPAIGN, headers=headers),
                                         adv.post("/api/campaigns", json=CAMPAIGN, headers=headers))  # fmt: skip
    await app.state.zx.runner.wait_idle()
    assert first.status_code == second.status_code == 201
    assert first.json()["spec"]["id"] == second.json()["spec"]["id"]
    assert len((await adv.get("/api/campaigns")).json()) == 1
    assert len((await adv.get(f"/api/campaigns/{first.json()['spec']['id']}/deals")).json()) == 1


async def test_duplicate_creator_selection_queues_once(server):
    app, _ = server
    advertiser_id = await creators(app, "ava", "kai")
    spec = CampaignSpec(**CAMPAIGN, owner_id=advertiser_id)
    deals = app.state.zx.deals
    await deals.save_campaign(Campaign(spec=spec, created_at=datetime.now(UTC)))
    first = await deals.queue_negotiations(spec, ["USR-ava", "USR-ava", "USR-kai"])
    again = await deals.queue_negotiations(spec, ["USR-kai", "USR-ava"])  # a resumed run re-queues: nothing changes
    assert [s["creator_id"] for s in first] == ["USR-ava", "USR-kai"]
    assert [s["negotiation_id"] for s in again] == [s["negotiation_id"] for s in first]


async def test_cancel_stops_the_queue(server):
    app, _ = server
    advertiser_id = await creators(app, "ava", "kai", "leo")
    talking, release = asyncio.Event(), asyncio.Event()

    async def hold_ava(real, url, data, context_id):
        if "USR-ava" in url and data.get("type") == "offer":
            talking.set()
            await release.wait()
        return await real(url, data, context_id)

    patch_send(app, hold_ava)
    cid = await start(app, advertiser_id, wait=False)
    await asyncio.wait_for(talking.wait(), 10)
    adv = client_for(app, advertiser_id)
    res = await adv.post(f"/api/campaigns/{cid}/negotiations/cancel")
    assert res.status_code == 200, res.text
    release.set()
    await app.state.zx.runner.wait_idle()
    q = await queue(app, advertiser_id, cid)
    s = by_creator(q)
    assert s["ava"]["status"] in TERMINAL  # the running one ends at its next turn
    assert s["kai"]["status"] == s["leo"]["status"] == "CANCELLED"
    deals = (await adv.get(f"/api/campaigns/{cid}/deals")).json()
    assert [d["influencer_id"] for d in deals] == ["USR-ava"]  # nobody else was ever contacted
    assert q["progress"]["workflow_status"] == "cancelled"


async def test_restart_resumes_the_queue_without_rerunning(server):
    app, _ = server
    advertiser_id = await creators(app, "ava", "kai")
    talking = asyncio.Event()

    async def hang_ava(real, url, data, context_id):
        if "USR-ava" in url and data.get("type") == "offer":
            talking.set()
            await asyncio.Event().wait()  # the process dies while waiting for this reply
        return await real(url, data, context_id)

    patch_send(app, hang_ava)
    old = app.state.zx.runner
    cid = await start(app, advertiser_id, wait=False)
    await asyncio.wait_for(talking.wait(), 10)
    old._active[cid].cancel()  # the server stops: the run is gone, its checkpoint and sessions remain
    await old.wait_idle()
    s = by_creator(await queue(app, advertiser_id, cid))
    assert s["ava"]["status"] == "RUNNING" and s["kai"]["status"] == "QUEUED"

    app.state.zx.runner = fresh = CampaignRunner(app.state.zx.deals, old.graph.checkpointer)  # the next process
    assert await fresh.recover() == [cid]
    assert await fresh.recover() == []  # already running: never started twice
    await fresh.wait_idle()
    q = await queue(app, advertiser_id, cid)
    s = by_creator(q)
    assert s["ava"]["status"] == "FAILED" and s["ava"]["outcome"] == "interrupted"  # closed, not re-run
    assert s["kai"]["status"] == "AGREED"
    ava_events = (await client_for(app, advertiser_id).get(f"/api/negotiations/{s['ava']['negotiation_id']}/events")).json()
    assert [e["event_type"] for e in ava_events].count("negotiation_started") == 1
    assert ava_events[-1]["event_type"] == "negotiation_completed"
    assert q["progress"]["workflow_status"] == "completed"


async def test_concurrent_campaigns_keep_separate_queues(server):
    app, _ = server
    advertiser_id = await creators(app, "ava", "kai")
    seen = record(app)
    adv = client_for(app, advertiser_id)
    a, b = await asyncio.gather(adv.post("/api/campaigns", json=CAMPAIGN), adv.post("/api/campaigns", json=CAMPAIGN))
    await app.state.zx.runner.wait_idle()
    for res in (a, b):
        cid = res.json()["spec"]["id"]
        q = await queue(app, advertiser_id, cid)
        assert [x["status"] for x in q["sessions"]] == ["AGREED", "AGREED"]
        mine = [e for e in seen if e["type"] == "negotiation" and e["campaign_id"] == cid]
        assert {e["event"]["negotiation_id"] for e in mine} == {x["negotiation_id"] for x in q["sessions"]}
        assert_sequential([e for e in seen if e.get("campaign_id") == cid], [x["negotiation_id"] for x in q["sessions"]])


def test_progress_counts_only_persisted_outcomes():
    campaign = Campaign(spec=CampaignSpec(**CAMPAIGN), created_at=datetime.now(UTC))

    def session(status, latest=None, final=None, creator="c"):
        return {"negotiation_id": f"DEAL-{creator}", "creator_id": creator, "status": status, "latest_offer": latest, "final_offer": final}

    sessions = [
        session("AGREED", {"cap": "30"}, {"cap": "30"}, "a"),
        session("FAILED", {"cap": "20"}, None, "b"),
        session("RUNNING", {"cap": "25"}, None, "c"),
        session("QUEUED", None, None, "d"),
    ]
    p = campaign_progress(campaign, sessions)
    assert (p["total_creators"], p["completed_count"], p["agreed_count"], p["failed_count"], p["queued_count"]) == (4, 2, 1, 1, 1)
    assert (p["agreed_spend"], p["proposed_spend"], p["current_creator_id"], p["workflow_status"]) == ("30", "75", "c", "running")
    done = campaign_progress(campaign, [sessions[0], sessions[1], session("REJECTED", {"cap": "10"}, None, "e")])
    assert done["workflow_status"] == "completed" and done["rejected_count"] == 1
    campaign.negotiation_cancelled = True
    assert campaign_progress(campaign, [sessions[0], session("QUEUED", None, None, "f")])["workflow_status"] == "cancelled"


# ---- the real pipeline: uvicorn, a socket, and the browser's SSE connection --------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_messages_reach_the_browser_while_agents_are_still_talking():
    """Over a real HTTP connection: each message arrives the moment its agent produced it, creator A's whole
    conversation streams before creator B's starts, and B streams message by message too."""
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    settings = Settings(_env_file=None, database_url="", anthropic_api_key="", youtube_api_key="", public_base_url=base,
                        session_secret="test-session-secret", mcp_api_token=MCP_TOKEN)  # fmt: skip
    app = create_app(settings, http_factory=lambda a: httpx.AsyncClient(transport=httpx.ASGITransport(app=a), base_url=base),
                     scheduler_interval=None)  # fmt: skip
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on", loop="none"))
    serving = asyncio.create_task(srv.serve())
    while not srv.started:  # noqa: ASYNC110 - uvicorn exposes no event for this
        await asyncio.sleep(0.05)
    try:
        advertiser_id = await creators(app, "kai", "ava")  # kai counters first, so A has several rounds

        async def thinking_time(real, url, data, context_id):
            await asyncio.sleep(0.25)  # a creator agent takes a moment to answer, like a real LLM call
            return await real(url, data, context_id)

        patch_send(app, thinking_time)
        auth = {"Authorization": f"Bearer {issue_user_token('test-session-secret', advertiser_id)}"}
        arrivals: list[tuple[float, dict]] = []

        async def browser():
            async with httpx.AsyncClient(base_url=base, headers=auth, timeout=None) as http, http.stream("GET", "/api/stream") as res:
                assert res.status_code == 200
                async for line in res.aiter_lines():
                    if line.startswith("data: "):
                        arrivals.append((time.monotonic(), json.loads(line[6:])))

        reader = asyncio.create_task(browser())
        await asyncio.sleep(0.3)  # the page is open before the campaign starts
        async with httpx.AsyncClient(base_url=base, headers=auth) as http:
            res = await http.post("/api/campaigns", json=CAMPAIGN)
            assert res.status_code == 201, res.text  # returns at once: the agents run in the background
            cid = res.json()["spec"]["id"]
            await app.state.zx.runner.wait_idle()
            await asyncio.sleep(0.3)
            q = (await http.get(f"/api/campaigns/{cid}/negotiations")).json()
        reader.cancel()

        a, b = (x["negotiation_id"] for x in q["sessions"])
        live = [(t, e["event"]) for t, e in arrivals if e.get("type") == "negotiation"]
        a_events = [(t, e) for t, e in live if e["negotiation_id"] == a]
        b_events = [(t, e) for t, e in live if e["negotiation_id"] == b]
        a_done = next(t for t, e in a_events if e["event_type"] == "negotiation_completed")
        b_start = next(t for t, e in b_events if e["event_type"] == "negotiation_started")
        b_done = next(t for t, e in b_events if e["event_type"] == "negotiation_completed")
        for events, done in ((a_events, a_done), (b_events, b_done)):
            messages = [(t, e) for t, e in events if e["event_type"] == "message_completed"]
            assert len(messages) >= 2
            # the opening offer and the creator's reply reached the browser well before that negotiation ended
            assert messages[0][1]["agent_role"] == "advertiser" and messages[1][1]["agent_role"] == "influencer"
            assert done - messages[0][0] >= 0.2 and messages[1][0] - messages[0][0] >= 0.2
        assert a_done <= b_start  # B starts after A finished
        assert all(e["creator_id"] == q["sessions"][0]["creator_id"] for _, e in a_events)
        assert all(e["creator_id"] == q["sessions"][1]["creator_id"] for _, e in b_events)
        # nothing delivered twice
        assert len({(e["negotiation_id"], e["event_id"]) for _, e in live}) == len(live)
    finally:
        srv.should_exit = True
        await serving


async def test_restart_with_postgres_resumes_in_a_new_process():
    """The durable path: store and LangGraph checkpoints in PostgreSQL. One app instance dies mid-negotiation; the
    next one, started on the same database, resumes the campaign by itself on startup."""
    import os

    import pytest

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    base = "http://localhost:8000"

    def make_app():
        settings = Settings(_env_file=None, database_url=url, anthropic_api_key="", youtube_api_key="", public_base_url=base,
                            session_secret="test-session-secret", mcp_api_token=MCP_TOKEN)  # fmt: skip
        return create_app(settings, http_factory=lambda a: httpx.AsyncClient(transport=httpx.ASGITransport(app=a), base_url=base),
                          scheduler_interval=None)  # fmt: skip

    async def running(app, body):
        started, stop = asyncio.Event(), asyncio.Event()

        async def lifespan():
            async with app.router.lifespan_context(app):
                started.set()
                await stop.wait()

        task = asyncio.create_task(lifespan())
        await started.wait()
        try:
            return await body(app)
        finally:
            stop.set()
            await task

    async def first_life(app):
        advertiser_id = await creators(app, "ava", "kai")
        talking = asyncio.Event()

        async def hang_ava(real, url_, data, context_id):
            if "USR-ava" in url_ and data.get("type") == "offer":
                talking.set()
                await asyncio.Event().wait()
            return await real(url_, data, context_id)

        patch_send(app, hang_ava)
        cid = await start(app, advertiser_id, wait=False)
        await asyncio.wait_for(talking.wait(), 10)
        app.state.zx.runner._active[cid].cancel()  # the process dies here
        await app.state.zx.runner.wait_idle()
        return advertiser_id, cid

    advertiser_id, cid = await running(make_app(), first_life)

    async def second_life(app):  # startup already called runner.recover()
        await app.state.zx.runner.wait_idle()
        return await queue(app, advertiser_id, cid)

    q = await running(make_app(), second_life)
    s = by_creator(q)
    assert s["ava"]["status"] == "FAILED" and s["ava"]["outcome"] == "interrupted"
    assert s["kai"]["status"] == "AGREED"
    assert q["progress"]["workflow_status"] == "completed"


async def test_outcomes_are_pushed_live_and_payment_waits_for_the_whole_queue(server):
    """A rejection or agreement reaches the advertiser's page the moment it happens; the signing step comes once,
    for every agreed creator together, only after the last creator finished."""
    app, _ = server
    advertiser_id = await creators(app, "leo", "ava", "kai")  # leo is rejected first, then two agreements
    seen = record(app)
    cid = await start(app, advertiser_id)
    q = await queue(app, advertiser_id, cid)
    leo, ava, kai = (x["negotiation_id"] for x in q["sessions"])

    def index(pred):
        return next(i for i, e in enumerate(seen) if pred(e))

    leo_rejected = index(lambda e: e["type"] == "deal" and e["deal_id"] == leo and e["state"] == "REJECTED")
    ava_agreed = index(lambda e: e["type"] == "deal" and e["deal_id"] == ava and e["state"] == "AGREED")
    kai_started = index(lambda e: e["type"] == "negotiation" and e["deal_id"] == kai and e["event"]["event_type"] == "negotiation_started")
    kai_done = index(lambda e: e["type"] == "negotiation" and e["deal_id"] == kai and e["event"]["event_type"] == "negotiation_completed")
    awaiting = index(lambda e: e["type"] == "campaign" and e["status"] == "awaiting_approval")
    assert leo_rejected < ava_agreed < kai_started  # each outcome is pushed before the next creator even starts
    assert awaiting > kai_done  # the payment step waits for the whole queue
    assert sum(1 for e in seen if e["type"] == "campaign" and e["status"] == "awaiting_approval") == 1
    detail = (await client_for(app, advertiser_id).get(f"/api/campaigns/{cid}")).json()
    assert sorted(detail["pending_approval"]["deals"]) == sorted([ava, kai])  # one authorization step for both agreed deals
