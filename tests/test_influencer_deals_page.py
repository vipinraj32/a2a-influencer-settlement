"""What the creator's My deals page reads and may do: only their own deals, newest first, only fields meant for creators,
and the one manual action (submitting the video) refused for someone else's deal or a deal that is not funded."""

from .test_e2e import client_for, named, onboard_advertiser, onboard_influencer, server  # noqa: F401 - pytest fixtures

# exactly what /api/influencer/deals shares about a campaign; its name, budget, limits and owner stay with the advertiser
CREATOR_CAMPAIGN_FIELDS = {
    "advertiser",
    "brand_handle",
    "niche",
    "deliverable",
    "brief",
    "required_tags",
    "requirements",
    "min_duration_seconds",
    "max_duration_seconds",
    "content_checks",
}


async def test_creators_see_only_their_own_deals(server):
    app, _ = server
    await onboard_influencer(app, "ava", "fitness", 42_000, "15", "2", "22")
    await onboard_influencer(app, "zed", "fitness", 38_000, "18", "2", "24")
    adv = client_for(app, await onboard_advertiser(app))
    for _ in range(2):
        assert (await adv.post("/api/campaigns", json=named())).status_code == 201
    await app.state.zx.runner.wait_idle()

    ava = (await client_for(app, "USR-ava").get("/api/influencer/deals")).json()
    zed = (await client_for(app, "USR-zed").get("/api/influencer/deals")).json()
    assert ava and zed
    assert {d["influencer_id"] for d in ava} == {"USR-ava"} and {d["influencer_id"] for d in zed} == {"USR-zed"}
    assert not {d["id"] for d in ava} & {d["id"] for d in zed}
    # newest first, so the page needs no sorting of its own
    stamps = [d["updated_at"] for d in ava]
    assert stamps == sorted(stamps, reverse=True)
    for d in ava + zed:
        assert set(d["campaign"]) == CREATOR_CAMPAIGN_FIELDS, d["campaign"].keys()
        assert {"state", "rounds", "terms", "payouts", "updated_at"} <= d.keys()


async def test_only_signed_in_creators_reach_the_page_data(server):
    app, _ = server
    adv = client_for(app, await onboard_advertiser(app))
    assert (await adv.get("/api/influencer/deals")).status_code == 403
    assert (await adv.get("/api/influencer/inbox")).status_code == 403
    assert (await client_for(app).get("/api/influencer/deals")).status_code == 401


async def test_proof_is_refused_for_another_creators_deal_or_an_unfunded_one(server):
    app, _ = server
    await onboard_influencer(app, "ava", "fitness", 42_000, "15", "2", "22")
    await onboard_influencer(app, "zed", "fitness", 38_000, "18", "2", "24")
    adv = client_for(app, await onboard_advertiser(app))
    assert (await adv.post("/api/campaigns", json=named())).status_code == 201
    await app.state.zx.runner.wait_idle()
    ava = client_for(app, "USR-ava")
    deal = (await ava.get("/api/influencer/deals")).json()[0]
    assert deal["state"] not in ("FUNDED", "PROOF_REJECTED")  # the advertiser has not paid yet
    url = {"video_url": "https://youtube.com/shorts/abcdefghijk"}

    res = await client_for(app, "USR-zed").post(f"/api/deals/{deal['id']}/proof", json=url)
    assert res.status_code == 404  # someone else's deal does not even exist for zed
    res = await ava.post(f"/api/deals/{deal['id']}/proof", json=url)
    assert res.status_code == 409 and "not awaiting proof" in res.json()["detail"]
    assert (await adv.post(f"/api/deals/{deal['id']}/proof", json=url)).status_code == 403  # advertisers never submit proof
    after = next(d for d in (await ava.get("/api/influencer/deals")).json() if d["id"] == deal["id"])
    assert after["state"] == deal["state"] and after["proof"] is None
