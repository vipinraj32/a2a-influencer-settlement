"""PostgresStore against a real database. Set TEST_DATABASE_URL to run, e.g.
postgresql://zyroxq:zyroxq@localhost:5432/zyroxq_test (tables are created; rows use unique ids)."""

import os
from datetime import UTC, datetime

import pytest

from zyroxq.models import AuditEvent, Campaign, ChannelInfo, Deal, DealState, User, new_id
from zyroxq.store import MemoryStore, PostgresStore

URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(params=["memory", "postgres"])
async def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    if not URL:
        pytest.skip("TEST_DATABASE_URL not set")
    s = await PostgresStore.connect(URL)
    yield s
    await s.close()


async def test_roundtrip(store, campaign, terms):
    campaign = campaign.model_copy(update={"id": new_id("CMP")})
    await store.save_campaign(Campaign(spec=campaign, created_at=datetime.now(UTC)))
    deal = Deal(id=new_id("DEAL"), campaign_id=campaign.id, influencer_id="ava-fit", terms=terms)
    await store.save_deal(deal)
    deal.state = DealState.FUNDED
    await store.save_deal(deal)

    got = await store.get_deal(deal.id)
    assert got is not None and got.state == DealState.FUNDED and got.terms.terms_hash() == terms.terms_hash()
    assert [d.id for d in await store.list_deals(campaign.id)] == [deal.id]
    assert (await store.get_campaign(campaign.id)).spec.budget == campaign.budget

    await store.add_event(AuditEvent(at=datetime.now(UTC), campaign_id=campaign.id, deal_id=deal.id, actor="hsp", kind="x", detail={"a": "1"}))
    events = await store.list_events(campaign.id, deal.id)
    assert len(events) == 1 and events[0].detail == {"a": "1"}

    rid = new_id("req")
    assert await store.claim_webhook(rid, "payment-finalized")
    assert not await store.claim_webhook(rid, "payment-finalized")
    assert await store.claim_webhook(rid, "payment-included")


async def test_users(store):
    uid = new_id("USR")
    now = datetime.now(UTC)
    user = User(id=uid, email=f"{uid}@example.com", name="Ava", role="influencer", created_at=now,
                channel=ChannelInfo(channel_id="UCava", title="Ava TV", subscribers=42_000, verified_at=now))  # fmt: skip
    await store.save_user(user)
    user.wallet = "0x" + "ab" * 20
    await store.save_user(user)
    got = await store.get_user(uid)
    assert got is not None and got.wallet == user.wallet and got.channel.channel_id == "UCava"
    assert (await store.get_user_by_email(user.email)).id == uid
    assert uid in {u.id for u in await store.list_users("influencer")}
    assert uid not in {u.id for u in await store.list_users("advertiser")}


async def test_budget_reservations_and_claims_are_atomic(store, campaign):
    import asyncio
    from decimal import Decimal

    cid = new_id("CMP")
    await store.save_campaign(Campaign(spec=campaign.model_copy(update={"id": cid}), created_at=datetime.now(UTC)))
    deals = [new_id("DEAL") for _ in range(6)]
    got = await asyncio.gather(*(store.reserve_budget(cid, d, Decimal("40"), Decimal("120")) for d in deals))
    assert sum(got) == 3 and await store.reserved_budget(cid) == Decimal("120")
    winner = deals[got.index(True)]
    assert await store.reserve_budget(cid, winner, Decimal("40"), Decimal("120"))  # idempotent
    await store.release_budget(winner)
    assert await store.reserved_budget(cid) == Decimal("80")
    assert await store.reserved_budget(cid, exclude=deals[got.index(True, got.index(True) + 1)]) == Decimal("40")

    nonce = "0x" + new_id("n").encode().hex().ljust(64, "0")
    owners = await asyncio.gather(*(store.claim("auth_nonce", nonce, d) for d in deals))
    assert len(set(owners)) == 1 and await store.claim("auth_nonce", nonce.upper().replace("0X", "0x"), "other") == owners[0]


async def test_versioned_documents_and_decisions(store):
    import asyncio

    key = new_id("USR")
    saved = await asyncio.gather(*(store.save_version("agent_policy", key, {"role": "advertiser", "n": i}) for i in range(5)))
    assert sorted(s["version"] for s in saved) == [1, 2, 3, 4, 5]  # concurrent writers never share a version
    assert (await store.get_version("agent_policy", key))["version"] == 5
    assert (await store.get_version("agent_policy", key, 2))["version"] == 2
    assert [v["version"] for v in await store.list_versions("agent_policy", key)] == [1, 2, 3, 4, 5]
    assert await store.get_version("agent_policy", key, 9) is None

    deal = new_id("DEAL")
    for party in ("advertiser", "influencer"):
        await store.add_decision({"id": new_id("DEC"), "deal_id": deal, "campaign_id": "CMP-x", "party": party,
                                  "at": datetime.now(UTC).isoformat(), "action": "ACCEPT"})  # fmt: skip
    assert {d["party"] for d in await store.list_decisions(deal)} == {"advertiser", "influencer"}
    assert [d["party"] for d in await store.list_decisions(deal, "influencer")] == ["influencer"]


async def test_negotiation_transcript_feedback_and_lessons(store):
    deal_id, now = new_id("DEAL"), datetime.now(UTC).isoformat()
    base = {"negotiation_id": deal_id, "campaign_id": "CMP-x", "agent_role": "advertiser", "timestamp": now}
    first = await store.add_negotiation_event({**base, "event_type": "negotiation_started"})
    second = await store.add_negotiation_event({**base, "event_type": "message_completed", "message_id": "MSG-1", "content": "Offer."})
    assert (first["event_id"], second["event_id"]) == (1, 2)
    assert [e["event_id"] for e in await store.list_negotiation_events(deal_id)] == [1, 2]
    assert [e["message_id"] for e in await store.list_negotiation_events(deal_id, after=1)] == ["MSG-1"]

    await store.add_negotiation_round({"deal_id": deal_id, "campaign_id": "CMP-x", "round": 1, "at": now, "reply": {"kind": "counter"}})
    assert [r["round"] for r in await store.list_negotiation_rounds(deal_id)] == [1]

    user = new_id("USR")
    fb = {"id": new_id("FBK"), "deal_id": deal_id, "campaign_id": "CMP-x", "party": "advertiser", "user_id": user, "rating": 2, "created_at": now}
    await store.save_feedback(fb)
    await store.save_feedback({**fb, "id": new_id("FBK"), "rating": 4})  # one per deal and party
    assert [f["rating"] for f in await store.list_feedback(deal_id=deal_id)] == [4]
    assert len(await store.list_feedback(user_id=user)) == 1

    row = {"deal_id": deal_id, "campaign_id": "CMP-x", "advertiser_id": user, "influencer_id": "USR-ava", "outcome": "agreed", "ended_at": now, "rounds": 2}
    await store.save_performance(row)
    await store.save_performance({**row, "rounds": 3})
    assert [r["rounds"] for r in await store.list_performance(user, "advertiser")] == [3]
    assert [r["deal_id"] for r in await store.list_performance("USR-ava", "influencer") if r["deal_id"] == deal_id] == [deal_id]

    lesson = {"id": new_id("LSN"), "owner_id": user, "role": "advertiser", "status": "pending", "feedback_id": fb["id"], "created_at": now, "guidance": "g"}
    await store.save_lesson(lesson)
    assert await store.list_lessons(user, "advertiser", status="approved") == []
    await store.save_lesson({**lesson, "status": "approved"})
    assert [x["id"] for x in await store.list_lessons(user, "advertiser", status="approved")] == [lesson["id"]]
    assert await store.list_lessons(user, "influencer") == [] and (await store.get_lesson(lesson["id"]))["status"] == "approved"


async def test_concurrent_sign_ins_create_one_user(store):
    import asyncio

    email = f"{new_id('race')}@example.com"
    now = datetime.now(UTC)
    a, b = (User(id=new_id("USR"), email=email, name="Racer", role="advertiser", created_at=now) for _ in range(2))
    first, second = await asyncio.gather(store.create_user(a), store.create_user(b))
    assert first.id == second.id and (await store.get_user_by_email(email)).id == first.id


async def test_negotiation_sessions_queue_once_and_compare_and_set(store):
    cid = new_id("CMP")

    def row(creator, position):
        return {"negotiation_id": new_id("DEAL"), "campaign_id": cid, "creator_id": creator, "position": position, "status": "QUEUED"}

    first = await store.queue_negotiations([row("ava", 0), row("kai", 1)])
    assert [s["creator_id"] for s in first] == ["ava", "kai"]
    again = await store.queue_negotiations([row("kai", 0), row("ava", 1)])  # same creators again: the stored rows win
    assert [(s["creator_id"], s["negotiation_id"]) for s in again] == [(s["creator_id"], s["negotiation_id"]) for s in first]

    ava = first[0]
    assert await store.save_negotiation_session({**ava, "status": "RUNNING"}, expect="QUEUED")
    assert not await store.save_negotiation_session({**ava, "status": "CANCELLED"}, expect="QUEUED")  # lost the race
    assert await store.save_negotiation_session({**ava, "status": "AGREED", "final_offer": {"cap": "30"}}, expect="RUNNING")
    stored = await store.list_negotiation_sessions(cid)
    assert [s["status"] for s in stored] == ["AGREED", "QUEUED"] and stored[0]["final_offer"] == {"cap": "30"}
    assert await store.save_negotiation_session({**stored[1], "status": "FAILED"})  # unconditional save
    assert [s["status"] for s in await store.list_negotiation_sessions(cid)] == ["AGREED", "FAILED"]
