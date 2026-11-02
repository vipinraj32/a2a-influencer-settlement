"""Campaign names: the advertiser's own label to tell campaigns apart. Required, tidied to one clean line, unique per
advertiser, never sent to creators, and older campaigns without one still read well."""

import json
from datetime import UTC, datetime

from zyroxq.models import Campaign, CampaignSpec, clean_campaign_name

from .test_e2e import CAMPAIGN, client_for, onboard_advertiser, onboard_influencer, server  # noqa: F401 - pytest fixtures


async def advertiser(app) -> str:
    return await onboard_advertiser(app)


async def test_name_is_required_and_bounded(server):
    app, _ = server
    adv = client_for(app, await advertiser(app))
    no_name = {k: v for k, v in CAMPAIGN.items() if k != "name"}
    for body in (no_name, {**CAMPAIGN, "name": ""}, {**CAMPAIGN, "name": "   \n\t "}, {**CAMPAIGN, "name": "x" * 81}):
        res = await adv.post("/api/campaigns", json=body)
        assert res.status_code == 422, res.text
    assert (await adv.get("/api/campaigns")).json() == []  # nothing was started


async def test_name_is_tidied_to_one_line(server):
    app, _ = server
    adv = client_for(app, await advertiser(app))
    res = await adv.post("/api/campaigns", json={**CAMPAIGN, "name": "  Diwali\n   push\t2026\x00 "})
    assert res.status_code == 201, res.text
    assert res.json()["spec"]["name"] == "Diwali push 2026"
    assert clean_campaign_name("A​B  C") == "AB C"  # invisible characters cannot make two names look alike


async def test_names_are_unique_per_advertiser(server):
    app, _ = server
    first_id = await advertiser(app)
    adv = client_for(app, first_id)
    assert (await adv.post("/api/campaigns", json={**CAMPAIGN, "name": "Summer drop"})).status_code == 201
    for clash in ("Summer drop", "summer DROP", "  Summer   drop "):  # case and spacing do not make a new name
        res = await adv.post("/api/campaigns", json={**CAMPAIGN, "name": clash})
        assert res.status_code == 409 and "already have a campaign named" in res.json()["detail"], res.text
    assert (await adv.post("/api/campaigns", json={**CAMPAIGN, "name": "Summer drop 2"})).status_code == 201
    await app.state.zx.runner.wait_idle()
    names = sorted(c["spec"]["name"] for c in (await adv.get("/api/campaigns")).json())
    assert names == ["Summer drop", "Summer drop 2"]


async def test_another_advertiser_may_use_the_same_name(server):
    app, _ = server
    a = client_for(app, await advertiser(app))
    assert (await a.post("/api/campaigns", json={**CAMPAIGN, "name": "Launch"})).status_code == 201
    # a second advertiser account
    store = app.state.zx.ctx.store
    other = (await store.get_user("USR-fitfuel")).model_copy(update={"id": "USR-other", "email": "other@example.com"})
    await store.save_user(other)
    b = client_for(app, "USR-other")
    assert (await b.post("/api/campaigns", json={**CAMPAIGN, "name": "Launch"})).status_code == 201
    await app.state.zx.runner.wait_idle()


async def test_retried_start_returns_the_first_campaign_although_its_name_is_taken(server):
    app, _ = server
    adv = client_for(app, await advertiser(app))
    headers = {"Idempotency-Key": "submit-42"}
    first = await adv.post("/api/campaigns", json={**CAMPAIGN, "name": "Retry me"}, headers=headers)
    again = await adv.post("/api/campaigns", json={**CAMPAIGN, "name": "Retry me"}, headers=headers)  # the response was lost
    await app.state.zx.runner.wait_idle()
    assert first.status_code == again.status_code == 201
    assert first.json()["spec"]["id"] == again.json()["spec"]["id"]
    assert len((await adv.get("/api/campaigns")).json()) == 1


async def test_name_never_reaches_creators(server):
    app, _ = server
    await onboard_influencer(app, "ava", "fitness", 42_000, "15", "2", "22")
    adv = client_for(app, await advertiser(app))
    secret = "Internal codename Falcon"
    sent: list[str] = []
    a2a = app.state.zx.ctx.a2a
    real = a2a.send

    async def spy(url, data, context_id=None):
        sent.append(json.dumps(data, default=str))
        return await real(url, data, context_id)

    a2a.send = spy
    cid = (await adv.post("/api/campaigns", json={**CAMPAIGN, "name": secret})).json()["spec"]["id"]
    await app.state.zx.runner.wait_idle()
    assert sent and all(secret not in m for m in sent)  # A2A messages to the creator's agent
    creator = client_for(app, "USR-ava")
    assert secret not in (await creator.get("/api/influencer/deals")).text
    deal_id = (await adv.get(f"/api/campaigns/{cid}/deals")).json()[0]["id"]
    assert secret not in (await creator.get(f"/api/negotiations/{deal_id}/events")).text
    # the advertiser sees it, and the audit trail records it
    assert (await adv.get(f"/api/campaigns/{cid}")).json()["spec"]["name"] == secret
    created = next(e for e in (await adv.get(f"/api/campaigns/{cid}/events")).json() if e["kind"] == "campaign_created")
    assert created["detail"]["name"] == secret


async def test_campaigns_from_before_names_still_read_well(server):
    app, _ = server
    owner = await advertiser(app)
    spec = CampaignSpec(**{k: v for k, v in CAMPAIGN.items() if k != "name"}, owner_id=owner)
    assert spec.name == "" and spec.display_name == "FitFuel · fitness"
    await app.state.zx.ctx.store.save_campaign(Campaign(spec=spec, created_at=datetime.now(UTC), status="done"))
    listed = (await client_for(app, owner).get("/api/campaigns")).json()
    assert [c["spec"]["name"] for c in listed] == [""]  # the UI shows brand · niche for these
    # an unnamed old campaign never blocks a new name
    assert (await client_for(app, owner).post("/api/campaigns", json=CAMPAIGN)).status_code == 201
    await app.state.zx.runner.wait_idle()
