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
