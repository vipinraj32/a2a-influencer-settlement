"""Full lifecycle through the real app, in-process: A2A negotiation → policy → approval → Payment
Mandate → simulated HSP webhook → escrow → proofs → time travel → payouts."""

import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from zyroxq.api.app import create_app
from zyroxq.config import Settings

BASE = "http://localhost:8000"


@pytest.fixture
async def server():
    settings = Settings(_env_file=None, database_url="", anthropic_api_key="", youtube_api_key="", public_base_url=BASE)
    app = create_app(settings, http_factory=lambda a: httpx.AsyncClient(transport=httpx.ASGITransport(app=a), base_url=BASE),
                     scheduler_interval=None)  # fmt: skip
    # Run the lifespan in one task, like uvicorn does (anyio cancel scopes must enter and exit in the same task)
    started, stop = asyncio.Event(), asyncio.Event()

    async def lifespan():
        async with app.router.lifespan_context(app):
            started.set()
            await stop.wait()

    task = asyncio.create_task(lifespan())
    await started.wait()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
        yield app, client
    stop.set()
    await task


CAMPAIGN = {
    "advertiser": "FitFuel",
    "brand_handle": "@fitfuel",
    "niche": "fitness",
    "budget": "120.00",
    "max_creators": 3,
    "max_fixed": "25.00",
    "max_cpm": "3.00",
    "cap_per_creator": "40.00",
    "brief": "Protein snack launch, 30s Shorts",
}


async def by_influencer(client, campaign_id):
    deals = (await client.get(f"/api/campaigns/{campaign_id}/deals")).json()
    return {d["influencer_id"]: d for d in deals}


async def test_full_campaign_lifecycle(server):
    app, client = server
    runner = app.state.zx.runner
    escrow = app.state.zx.ctx.escrow

    res = await client.post("/api/campaigns", json=CAMPAIGN)
    assert res.status_code == 201, res.text
    cid = res.json()["spec"]["id"]
    await runner.wait_idle()

    # Negotiation over A2A: three fitness creators agree, the premium one is out of budget, food creator never contacted
    deals = await by_influencer(client, cid)
    assert set(deals) == {"ava-fit", "raj-lifts", "mia-moves", "leo-gains"}
    assert deals["leo-gains"]["state"] == "REJECTED"
    agreed = {k: d for k, d in deals.items() if k != "leo-gains"}
    assert all(d["state"] == "AWAITING_APPROVAL" for d in agreed.values()), {k: d["state"] for k, d in deals.items()}
    for d in agreed.values():
        assert Decimal(d["terms"]["fixed_fee"]) <= Decimal("25") and d["terms"]["influencer_signature"].startswith("0x")
        assert d["cart_contents"]["payment_request"]["method_data"][0]["data"]["pay_to"] == escrow.address

    # 3 × $40 = $120 > $50 threshold → human approval interrupt
    campaign = (await client.get(f"/api/campaigns/{cid}")).json()
    assert campaign["status"] == "awaiting_approval" and campaign["pending_approval"]["total"] == "120.00"
    assert (await client.post(f"/api/campaigns/{cid}/decision", json={"approved": True})).status_code == 200
    await runner.wait_idle()

    deals = await by_influencer(client, cid)
    for k in agreed:
        assert deals[k]["state"] == "FUNDED", deals[k]
        assert deals[k]["payment_status"] == "payment-finalized"
    assert escrow.balance == 120_000_000

    # Influencer agents were told over A2A that the money is locked
    inbox = (await client.get("/api/influencers/ava-fit/inbox")).json()
    assert inbox and inbox[0]["type"] == "funded"

    # Proofs: valid, someone else's video, prompt injection
    ava = (await client.post(f"/api/demo/deals/{deals['ava-fit']['id']}/post", json={"variant": "valid"})).json()["deal"]
    assert ava["state"] == "VERIFIED", ava["proof"]
    raj = (await client.post(f"/api/demo/deals/{deals['raj-lifts']['id']}/post", json={"variant": "copied"})).json()["deal"]
    assert raj["state"] == "PROOF_REJECTED"
    assert not next(c for c in raj["proof"]["checks"] if c["name"] == "owned_by_deal_channel")["passed"]
    mia = (await client.post(f"/api/demo/deals/{deals['mia-moves']['id']}/post", json={"variant": "injection"})).json()["deal"]
    assert mia["state"] == "PROOF_REJECTED"
    assert not next(c for c in mia["proof"]["checks"] if c["name"] == "no_ai_directed_instructions")["passed"]

    # +49h: review window over → fixed fee released with a verifier attestation
    await client.post("/api/demo/advance", json={"hours": 49})
    ava = (await client.get(f"/api/deals/{ava['id']}")).json()
    assert ava["state"] == "FIXED_RELEASED"
    assert ava["payouts"][0]["kind"] == "fixed" and Decimal(ava["payouts"][0]["amount"]) == Decimal(ava["terms"]["fixed_fee"])

    # +7 days: performance fee from views, remainder refunded; the two failed proofs expire and refund in full
    await client.post("/api/demo/advance", json={"hours": 24 * 7})
    deals = await by_influencer(client, cid)
    ava = deals["ava-fit"]
    assert ava["state"] == "COMPLETED"
    kinds = {p["kind"]: Decimal(p["amount"]) for p in ava["payouts"]}
    terms = ava["terms"]
    expected_perf = min(Decimal(terms["cap"]) - Decimal(terms["fixed_fee"]), Decimal(9_400) / 1000 * Decimal(terms["cpm"])).quantize(Decimal("0.01"))
    assert kinds["performance"] == expected_perf
    assert kinds["fixed"] + kinds["performance"] + kinds["refund"] == Decimal("40")
    assert deals["raj-lifts"]["state"] == deals["mia-moves"]["state"] == "EXPIRED_REFUNDED"
    assert escrow.balance == 0 and escrow.allocated == 0
    assert (await client.get(f"/api/campaigns/{cid}")).json()["status"] == "done"

    # Audit trail covers every actor
    actors = {e["actor"] for e in (await client.get(f"/api/campaigns/{cid}/events")).json()}
    assert {"advertiser-agent", "policy", "wallet", "hsp", "escrow", "verifier", "human"} <= actors

    # MCP tools read the same data
    result = await app.state.mcp.call_tool("campaign_spend", {"campaign_id": cid})
    spend = json.loads(result[0][0].text) if isinstance(result, tuple) else json.loads(result[0].text)
    assert spend["locked_total"] == "120.00" and spend["in_escrow"] == "0.00"


async def test_small_campaign_skips_approval(server):
    app, client = server
    body = {**CAMPAIGN, "budget": "40.00", "max_creators": 1, "cap_per_creator": "40.00", "max_fixed": "20.00"}
    cid = (await client.post("/api/campaigns", json=body)).json()["spec"]["id"]
    await app.state.zx.runner.wait_idle()
    states = sorted(d["state"] for d in (await by_influencer(client, cid)).values())
    assert "FUNDED" in states and "AWAITING_APPROVAL" not in states  # $40 ≤ $50 threshold


async def test_declined_approval_signs_nothing(server):
    app, client = server
    cid = (await client.post("/api/campaigns", json=CAMPAIGN)).json()["spec"]["id"]
    await app.state.zx.runner.wait_idle()
    await client.post(f"/api/campaigns/{cid}/decision", json={"approved": False})
    await app.state.zx.runner.wait_idle()
    assert all(d["state"] == "REJECTED" for d in (await by_influencer(client, cid)).values())
    assert app.state.zx.ctx.escrow.balance == 0


async def test_webhook_rejects_bad_signature(server):
    _, client = server
    res = await client.post("/webhooks/hsp", content=b'{"x":1}', headers={"X-Signature": "t=1,v1=00"})
    assert res.status_code == 401


async def test_invalid_campaign_bounds(server):
    _, client = server
    res = await client.post("/api/campaigns", json={**CAMPAIGN, "max_fixed": "50.00"})
    assert res.status_code == 422
