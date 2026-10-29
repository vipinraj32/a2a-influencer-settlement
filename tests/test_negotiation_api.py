"""Configurable agents through the real app: policy versions, privacy between parties, escalation,
compensation models, disclosure gates, compliance review and terms-hash binding of approvals."""

import json
from decimal import Decimal

from zyroxq.agent_policy import InfluencerPolicy
from zyroxq.models import DealState

from .test_e2e import (  # noqa: F401 - pytest fixtures
    ATTESTED,
    CAMPAIGN,
    by_influencer,
    client_for,
    onboard_advertiser,
    onboard_influencer,
    onboarded,
    post_video,
    server,
    sign_requests,
    wallet,
)


async def creator_policy(client) -> dict:
    res = (await client.get("/api/me/agent-policy")).json()
    assert res["policy"] is not None
    return res["policy"]


async def test_policy_versions_need_confirmation_to_loosen(onboarded):
    app, _, _ = onboarded
    ava = client_for(app, "USR-ava")
    first = await creator_policy(ava)
    assert (await ava.get("/api/me/agent-policy")).json()["is_default"]  # derived from the onboarding rate card

    tighter = {**first, "hard": {**first["hard"], "min_fixed_fee": "16", "forbidden_categories": ["alcohol"]}}
    saved = (await ava.put("/api/me/agent-policy", json={"policy": tighter})).json()
    assert saved["policy"]["version"] == 1 and saved["material_changes"] == []

    looser = {**tighter, "hard": {**tighter["hard"], "min_fixed_fee": "9", "allow_paid_amplification": True, "forbidden_categories": []}}
    res = await ava.put("/api/me/agent-policy", json={"policy": looser})
    assert res.status_code == 409
    changes = res.json()["detail"]["material_changes"]
    assert any("minimum fixed fee lowered" in c for c in changes) and any("alcohol" in c for c in changes)
    assert (await ava.post("/api/me/agent-policy/validate", json={"policy": looser})).json()["material_changes"] == changes
    res = await ava.put("/api/me/agent-policy", json={"policy": looser, "confirm_material_change": True})
    assert res.status_code == 200 and res.json()["policy"]["version"] == 2
    assert [v["version"] for v in (await ava.get("/api/me/agent-policy/versions")).json()] == [2, 1]
    assert (await ava.get("/api/me")).json()["user"]["rate_card"]["fixed_min"] == "9"  # the profile follows the policy

    bad = {**looser, "preferences": {**looser["preferences"], "preferred_fixed_fee": "5"}}
    assert (await ava.put("/api/me/agent-policy", json={"policy": bad, "confirm_material_change": True})).status_code == 422
    await ava.aclose()


async def test_private_limits_never_reach_the_other_party(server):
    app, _ = server
    # The anchor is the creator's opening ask and a counter may sit on the CPM floor: both are offers the creator makes.
    # The fixed floor (below every counter here), the CPM floor (below the opening CPM) and policy fields must stay private.
    creator = await onboard_influencer(app, "ivy", "fitness", 30_000, "18.37", "1.93", "24.91")
    advertiser_id = await onboard_advertiser(app)
    adv, ivy = client_for(app, advertiser_id), client_for(app, creator)
    ad_policy = (await adv.get("/api/me/agent-policy")).json()["policy"]
    ad_policy["preferences"]["concession_pct"] = "37"  # a distinctive private value
    assert (await adv.put("/api/me/agent-policy", json={"policy": ad_policy})).status_code == 200

    cid = (await adv.post("/api/campaigns", json={**CAMPAIGN, "max_fixed": "23.17", "budget": "40.00"})).json()["spec"]["id"]
    await app.state.zx.runner.wait_idle()
    deal = (await by_influencer(adv, cid))["ivy"]
    assert len(deal["rounds"]) >= 2  # the creator countered at least once

    seen_by_advertiser = json.dumps([
        (await adv.get(f"/api/campaigns/{cid}")).json(),
        (await adv.get(f"/api/campaigns/{cid}/deals")).json(),
        (await adv.get(f"/api/campaigns/{cid}/events")).json(),
        (await adv.get(f"/api/deals/{deal['id']}/events")).json(),
        (await adv.get(f"/api/deals/{deal['id']}/negotiation")).json(),
        (await adv.get(f"/api/deals/{deal['id']}/decisions")).json(),
        (await adv.get("/api/influencers")).json(),
        (await adv.get(f"/api/influencers/{creator}/evidence")).json(),
    ])  # fmt: skip
    for secret in ("18.37", "1.93", "min_fixed_fee", "min_cpm", "preferred_fixed", 'rate_card": {'):
        assert secret not in seen_by_advertiser, secret

    seen_by_creator = json.dumps([
        (await ivy.get("/api/influencer/deals")).json(),
        (await ivy.get(f"/api/deals/{deal['id']}/events")).json(),
        (await ivy.get(f"/api/deals/{deal['id']}/decisions")).json(),
        (await ivy.get(f"/api/deals/{deal['id']}/negotiation")).json(),
    ])  # fmt: skip
    for secret in ("23.17", "concession_pct", "37%", "negotiation_move", "policy_gate"):
        assert secret not in seen_by_creator, secret

    # Each party reads only its own decision records
    mine = (await ivy.get(f"/api/deals/{deal['id']}/decisions")).json()
    theirs = (await adv.get(f"/api/deals/{deal['id']}/decisions")).json()
    assert mine and {d["party"] for d in mine} == {"influencer"} and any(d["private_rationale"] for d in mine)
    assert theirs and {d["party"] for d in theirs} == {"advertiser"}
    assert all(d["policy_version"] == 1 for d in theirs)  # the campaign snapshotted the saved advertiser policy
    for c in (adv, ivy):
        await c.aclose()


async def test_negotiation_room_and_round_limit(onboarded):
    app, _, advertiser_id = onboarded
    adv = client_for(app, advertiser_id)
    cid = (await adv.post("/api/campaigns", json={**CAMPAIGN, "max_rounds": 2})).json()["spec"]["id"]
    await app.state.zx.runner.wait_idle()
    deals = await by_influencer(adv, cid)
    leo = (await adv.get(f"/api/deals/{deals['leo']['id']}/negotiation")).json()
    assert leo["status"] == "failed" and leo["max_rounds"] == 2 and leo["rounds_used"] <= 2 and leo["outcome"]
    raj = (await adv.get(f"/api/deals/{deals['raj']['id']}/negotiation")).json()
    assert raj["rounds"][0]["advertiser_offer"]["fixed_fee"] == "15.00"
    assert all(r["round"] <= 2 for r in raj["rounds"])
    ava = (await adv.get(f"/api/deals/{deals['ava']['id']}/negotiation")).json()
    assert ava["status"] == "agreed" and ava["agreement"]["terms_hash"] == ava["agreement"]["approved_terms_hash"]
    examples = ava["payable"]["examples"]
    assert examples[0]["eligible_views"] == 0 and Decimal(examples[-1]["total"]) == Decimal(ava["agreement"]["terms"]["cap"])
    await adv.aclose()


async def test_advertiser_escalation_threshold_hands_counters_to_a_human(onboarded):
    app, _, advertiser_id = onboarded
    adv = client_for(app, advertiser_id)
    policy = (await adv.get("/api/me/agent-policy")).json()["policy"]
    policy["preferences"]["escalate_fixed_above"] = "16"
    await adv.put("/api/me/agent-policy", json={"policy": policy})
    cid = (await adv.post("/api/campaigns", json=CAMPAIGN)).json()["spec"]["id"]
    await app.state.zx.runner.wait_idle()
    deals = await by_influencer(adv, cid)
    assert deals["raj"]["state"] == "ESCALATED" and "advertiser review" in deals["raj"]["negotiation_outcome"]
    assert deals["ava"]["state"] == "AWAITING_APPROVAL"  # accepted the opening offer: nothing to escalate
    assert (await adv.get(f"/api/campaigns/{cid}")).json()["funding"]["reserved"] == "80.00"  # escalations reserve nothing
    await adv.aclose()


async def test_disclosure_gate_at_campaign_creation(onboarded):
    app, _, advertiser_id = onboarded
    adv = client_for(app, advertiser_id)
    for change, needle in (({"required_tags": ["#sp"]}, "no disclosure label approved"), ({"jurisdictions": ["FR"]}, "FR"),
                           ({"jurisdictions": []}, "at least one jurisdiction")):  # fmt: skip
        res = await adv.post("/api/campaigns", json={**CAMPAIGN, **change})
        assert res.status_code == 400 and needle in res.json()["detail"], res.text
    spec = (await adv.post("/api/campaigns", json={**CAMPAIGN, "jurisdictions": ["us"]})).json()["spec"]
    assert spec["disclosure_policies"] == ["US@1"]
    policies = (await adv.get("/api/disclosure-policies")).json()
    assert {p["jurisdiction"] for p in policies["policies"]} == {"IN", "US"} and "Not legal advice" in policies["disclaimer"]
    # Only the operator publishes new versions; existing campaigns keep theirs
    us = next(p for p in policies["policies"] if p["jurisdiction"] == "US")
    assert (await adv.put("/api/disclosure-policies/US", json=us)).status_code == 403
    op = client_for(app)
    op.headers["Authorization"] = "Bearer test-operator-token"
    assert (await op.put("/api/disclosure-policies/US", json={**us, "placement_chars": 80})).json()["id"] == "US@2"
    again = (await adv.post("/api/campaigns", json={**CAMPAIGN, "jurisdictions": ["US"]})).json()["spec"]
    assert again["disclosure_policies"] == ["US@2"] and (await adv.get(f"/api/campaigns/{spec['id']}")).json()["spec"]["disclosure_policies"] == ["US@1"]
    for c in (adv, op):
        await c.aclose()


async def fund(app, adv, cid):
    campaign = (await adv.get(f"/api/campaigns/{cid}")).json()
    res = await adv.post(f"/api/campaigns/{cid}/decision", json={"approved": True, "signatures": sign_requests(campaign, wallet("advertiser"))})
    assert res.status_code == 200, res.text
    await app.state.zx.runner.wait_idle()


async def test_compliance_review_gates_the_fixed_fee(onboarded):
    app, videos, advertiser_id = onboarded
    adv, ava = client_for(app, advertiser_id), client_for(app, "USR-ava")
    cid = (await adv.post("/api/campaigns", json={**CAMPAIGN, "review_required": True})).json()["spec"]["id"]
    await app.state.zx.runner.wait_idle()
    await fund(app, adv, cid)
    deal_id = (await by_influencer(adv, cid))["ava"]["id"]
    url = post_video(app, videos, "vidAvaRev", "UCavachannel", "#ad FitFuel bites @fitfuel", 5_000)
    assert (await ava.post(f"/api/deals/{deal_id}/proof", json={"video_url": url, "attestations": ATTESTED})).json()["state"] == "VERIFIED"

    await adv.post("/api/dev/advance", json={"hours": 49})
    assert (await adv.get(f"/api/deals/{deal_id}")).json()["state"] == "VERIFIED"  # held for the advertiser's review
    compliance = (await adv.get(f"/api/campaigns/{cid}/compliance")).json()
    row = next(d for d in compliance["deals"] if d["deal_id"] == deal_id)
    assert row["review"] == "pending" and "advertiser disclosure review pending" in row["gates"]
    assert row["payment_approval"]["approved"] and row["terms_hash_matches_approval"] and compliance["settlement_simulated"]
    assert (await ava.post(f"/api/deals/{deal_id}/compliance/review", json={"approved": True})).status_code == 403  # advertisers only

    assert (await adv.post(f"/api/deals/{deal_id}/compliance/review", json={"approved": True, "note": "label + on-screen ok"})).status_code == 200
    await adv.post("/api/dev/advance", json={"hours": 1})
    assert (await adv.get(f"/api/deals/{deal_id}")).json()["state"] == "FIXED_RELEASED"
    for c in (adv, ava):
        await c.aclose()


async def test_cpm_only_campaign_pays_performance_only(onboarded):
    app, videos, advertiser_id = onboarded
    adv, ava = client_for(app, advertiser_id), client_for(app, "USR-ava")
    body = {**CAMPAIGN, "compensation_model": "cpm", "max_fixed": "0", "max_cpm": "3.00"}
    cid = (await adv.post("/api/campaigns", json=body)).json()["spec"]["id"]
    await app.state.zx.runner.wait_idle()
    deals = await by_influencer(adv, cid)
    d = deals["ava"]
    assert d["state"] == "AWAITING_APPROVAL" and d["terms"]["compensation_model"] == "cpm" and d["terms"]["fixed_fee"] == "0.00"
    items = d["cart_contents"]["payment_request"]["details"]["display_items"]
    assert [i["label"] for i in items] == ["Performance fee (max)"]  # no zero-amount line for HSP
    await fund(app, adv, cid)
    url = post_video(app, videos, "vidAvaCpm", "UCavachannel", "#ad FitFuel @fitfuel", 6_000)
    await ava.post(f"/api/deals/{d['id']}/proof", json={"video_url": url, "attestations": ATTESTED})
    await adv.post("/api/dev/advance", json={"hours": 49})
    await adv.post("/api/dev/advance", json={"hours": 24 * 7})
    done = (await adv.get(f"/api/deals/{d['id']}")).json()
    assert done["state"] == "COMPLETED"
    kinds = {p["kind"]: Decimal(p["amount"]) for p in done["payouts"]}
    assert "fixed" not in kinds and kinds["performance"] == (Decimal(6_000) / 1000 * Decimal(d["terms"]["cpm"])).quantize(Decimal("0.01"))
    for c in (adv, ava):
        await c.aclose()


async def test_payment_is_bound_to_the_approved_terms_hash(onboarded):
    app, _, advertiser_id = onboarded
    adv = client_for(app, advertiser_id)
    cid = (await adv.post("/api/campaigns", json=CAMPAIGN)).json()["spec"]["id"]
    await app.state.zx.runner.wait_idle()
    campaign = (await adv.get(f"/api/campaigns/{cid}")).json()
    signatures = sign_requests(campaign, wallet("advertiser"))
    store = app.state.zx.ctx.store
    target = await store.get_deal(campaign["signing_requests"][0]["deal_id"])
    assert target.state == DealState.AWAITING_APPROVAL
    target.terms = target.terms.model_copy(update={"publish_by": target.terms.publish_by.replace(year=2030)})  # tampered after drafting
    await store.save_deal(target)
    res = await adv.post(f"/api/campaigns/{cid}/decision", json={"approved": True, "signatures": signatures})
    assert res.status_code == 400 and "terms changed" in res.json()["detail"]
    await adv.aclose()


async def test_creator_policy_constraints_apply_to_live_negotiations(onboarded):
    app, _, advertiser_id = onboarded
    adv, mia = client_for(app, advertiser_id), client_for(app, "USR-mia")
    policy = InfluencerPolicy.model_validate(await creator_policy(mia))
    policy.hard.forbidden_categories = ["snacks"]
    assert (await mia.put("/api/me/agent-policy", json={"policy": policy.model_dump(mode="json")})).status_code == 200
    cid = (await adv.post("/api/campaigns", json={**CAMPAIGN, "category": "Snacks"})).json()["spec"]["id"]
    await app.state.zx.runner.wait_idle()
    deals = await by_influencer(adv, cid)
    assert deals["mia"]["state"] == "REJECTED" and "brand category" in deals["mia"]["negotiation_outcome"]
    decision = (await mia.get(f"/api/deals/{deals['mia']['id']}/decisions")).json()[0]
    assert decision["action"] == "REJECT" and decision["policy_version"] == 1
    assert not next(c for c in decision["hard_checks"] if c["name"] == "brand_category")["passed"]
    for c in (adv, mia):
        await c.aclose()
