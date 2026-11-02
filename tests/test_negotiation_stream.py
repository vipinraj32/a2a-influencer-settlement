"""Live negotiation transcript (persisted, streamed per message, resumable), human feedback on finished
negotiations, owner-approved lessons in later runs, and the agent performance metrics."""

import json
from decimal import Decimal

from zyroxq.agents.advertiser.negotiation import AdvertiserDecision
from zyroxq.models import DealState

from .test_agents_llm_guardrails import FakeLLM
from .test_e2e import (  # noqa: F401 - pytest fixtures
    CAMPAIGN,
    by_influencer,
    client_for,
    named,
    onboard_advertiser,
    onboard_influencer,
    server,
)


class RecordingLLM(FakeLLM):
    def __init__(self, answer):
        super().__init__(answer)
        self.prompts: list[str] = []

    async def ainvoke(self, messages):
        self.prompts.append("\n".join(m.content for m in messages))
        return await super().ainvoke(messages)


async def launch(app, advertiser_id: str, **overrides) -> tuple[str, dict]:
    adv = client_for(app, advertiser_id)
    res = await adv.post("/api/campaigns", json=named(**overrides))
    assert res.status_code == 201, res.text
    cid = res.json()["spec"]["id"]
    await app.state.zx.runner.wait_idle()
    return cid, await by_influencer(adv, cid)


def sse_events(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def frame_ids(body: str) -> list[int]:
    return [int(line[4:]) for line in body.splitlines() if line.startswith("id: ")]


async def setup(app) -> str:
    await onboard_influencer(app, "ava", "fitness", 42_000, "15", "2", "22")  # accepts the opening offer
    await onboard_influencer(app, "kai", "fitness", 50_000, "20", "2", "30")  # counters, then settles inside the limits
    await onboard_influencer(app, "leo", "fitness", 260_000, "60", "4", "75")  # out of budget: no deal
    return await onboard_advertiser(app)


async def test_messages_stream_as_they_happen_in_order(server):
    app, _ = server
    advertiser_id = await setup(app)
    store = app.state.zx.ctx.store
    seen: list[tuple[dict, str]] = []  # (event, deal state when it was published)
    publish = app.state.zx.ctx.bus.publish

    def spy(event, audience):
        if event.get("type") == "negotiation":
            seen.append((event["event"], store._deals[event["deal_id"]].state))
        publish(event, audience)

    app.state.zx.ctx.bus.publish = spy
    _, deals = await launch(app, advertiser_id)
    kai = deals["kai"]
    live = [(e, s) for e, s in seen if e["negotiation_id"] == kai["id"]]
    types = [e["event_type"] for e, _ in live]
    assert types[0] == "negotiation_started" and types[-1] == "negotiation_completed"

    # 1-2: the opening offer and the creator agent's reply are pushed while the negotiation is still running
    messages = [(e, s) for e, s in live if e["event_type"] == "message_completed"]
    assert messages[0][0]["agent_role"] == "advertiser" and messages[0][1] == DealState.NEGOTIATING
    assert messages[1][0]["agent_role"] == "influencer" and messages[1][1] == DealState.NEGOTIATING
    opening = messages[0][0]["content"]  # a chat message that greets the creator and carries the brief
    assert messages[0][0]["offer"]["round"] == 1 and opening.startswith("Hi Kai") and CAMPAIGN["brief"] in opening
    assert types.index("agent_thinking") < [e["event_type"] for e, _ in live].index("message_completed", 2)

    # 3: several rounds, in order, alternating agents, ids gap-free
    assert [e["event_id"] for e, _ in live] == list(range(1, len(live) + 1))
    roles = [e["agent_role"] for e, _ in messages]
    assert roles[:4] == ["advertiser", "influencer", "advertiser", "influencer"]
    rounds = [e["round"] for e, _ in messages]
    assert rounds == sorted(rounds) and max(rounds) >= 2
    assert "counter_offer_created" in types
    # only public notes and offers travel in the transcript: no private limits or rationale
    assert "concession" not in json.dumps([e for e, _ in live]) and "private" not in json.dumps([e for e, _ in live])


async def test_agreement_rejection_and_replay(server):
    app, _ = server
    advertiser_id = await setup(app)
    _, deals = await launch(app, advertiser_id)
    adv = client_for(app, advertiser_id)

    # 4: the agreed terms are in the final events and match the signed deal
    ava = (await adv.get(f"/api/negotiations/{deals['ava']['id']}/events")).json()
    agreed = next(e for e in ava if e["event_type"] == "negotiation_agreed")
    assert agreed["outcome"] == "agreed" and Decimal(agreed["terms"]["fixed_fee"]) == Decimal(deals["ava"]["terms"]["fixed_fee"])
    assert agreed["terms_hash"] and ava[-1]["event_type"] == "negotiation_completed"

    # 5: the premium creator ends without a deal and says so
    leo = (await adv.get(f"/api/negotiations/{deals['leo']['id']}/events")).json()
    end = next(e for e in leo if e["event_type"] in ("negotiation_rejected", "negotiation_timeout"))
    assert end["outcome"] in ("rejected", "timeout") and deals["leo"]["state"] == "REJECTED"

    # 6: a refreshed page replays the full stored conversation, over SSE too, and the stream closes when done
    body = (await adv.get(f"/api/negotiations/{deals['kai']['id']}/stream")).text
    stored = (await adv.get(f"/api/negotiations/{deals['kai']['id']}/events")).json()
    assert sse_events(body) == stored and frame_ids(body) == [e["event_id"] for e in stored]


async def test_reconnect_resumes_without_duplicates_or_rerun(server):
    app, _ = server
    advertiser_id = await setup(app)
    cid, deals = await launch(app, advertiser_id)
    adv = client_for(app, advertiser_id)
    deal_id = deals["kai"]["id"]
    full = (await adv.get(f"/api/negotiations/{deal_id}/events")).json()
    rounds_before = len(deals["kai"]["rounds"])

    # 7: EventSource sends Last-Event-ID on reconnect: only later events come back, each once
    body = (await adv.get(f"/api/negotiations/{deal_id}/stream", headers={"Last-Event-ID": "3"})).text
    assert frame_ids(body) == [e["event_id"] for e in full[3:]]
    body = (await adv.get(f"/api/negotiations/{deal_id}/stream?after=5")).text
    assert frame_ids(body) == [e["event_id"] for e in full[5:]]
    # reading the stream never runs the negotiation again
    for _ in range(3):
        await adv.get(f"/api/negotiations/{deal_id}/stream")
    await app.state.zx.runner.wait_idle()
    after = await by_influencer(adv, cid)
    assert len(after) == len(deals) and len(after["kai"]["rounds"]) == rounds_before
    assert len((await adv.get(f"/api/negotiations/{deal_id}/events")).json()) == len(full)


async def test_feedback_is_saved_linked_and_becomes_a_pending_lesson(server):
    app, _ = server
    advertiser_id = await setup(app)
    _, deals = await launch(app, advertiser_id)
    adv, ava = client_for(app, advertiser_id), client_for(app, "USR-ava")
    deal_id = deals["ava"]["id"]
    events = (await adv.get(f"/api/negotiations/{deal_id}/events")).json()
    ours = next(e for e in events if e["event_type"] == "message_completed" and e["agent_role"] == "advertiser")

    # 8: saved against the negotiation, linked to the agent, round and message, with the transcript
    body = {"rating": 2, "category": "accepted_too_quickly", "ratings": {"effectiveness": 2, "offer_quality": 3},
            "what_went_wrong": "Closed in one round", "do_differently": "Open lower and counter once", "message_id": ours["message_id"],
            "suggested_offer": {"fixed_fee": "18", "cpm": "2"}}  # fmt: skip
    res = await adv.post(f"/api/negotiations/{deal_id}/feedback", json=body)
    assert res.status_code == 201, res.text
    fb, lesson = res.json()["feedback"], res.json()["lesson"]
    assert fb["party"] == "advertiser" and fb["agent_id"] == f"advertiser-agent:{advertiser_id}"
    assert fb["target"]["message_id"] == ours["message_id"] and fb["target"]["round"] == 1
    assert fb["signal"]["outcome"] == "agreed" and fb["signal"]["suggested_offer_within_limits"]
    stored = (await app.state.zx.ctx.store.list_feedback(deal_id=deal_id))[0]
    assert [e["event_id"] for e in stored["transcript"]] == [e["event_id"] for e in events]
    assert lesson["status"] == "pending" and lesson["role"] == "advertiser" and "counteroffer" in lesson["guidance"]
    assert "Open lower and counter once" in lesson["guidance"]
    assert (await adv.get(f"/api/negotiations/{deal_id}/feedback")).json()["id"] == fb["id"]

    # the creator rates their own agent separately; the two never mix
    res = await ava.post(f"/api/negotiations/{deal_id}/feedback", json={"rating": 5, "category": "good_agreement"})
    assert res.status_code == 201 and res.json()["feedback"]["party"] == "influencer"
    assert [x["role"] for x in (await ava.get("/api/me/agent-lessons")).json()] == ["influencer"]
    assert [x["role"] for x in (await adv.get("/api/me/agent-lessons")).json()] == ["advertiser"]

    # resubmitting replaces the earlier feedback and retires its lesson
    res = await adv.post(f"/api/negotiations/{deal_id}/feedback", json={**body, "rating": 3})
    assert len([f for f in await app.state.zx.ctx.store.list_feedback(deal_id=deal_id) if f["party"] == "advertiser"]) == 1
    statuses = {x["id"]: x["status"] for x in (await adv.get("/api/me/agent-lessons")).json()}
    assert statuses[lesson["id"]] == "dismissed" and statuses[res.json()["lesson"]["id"]] == "pending"

    # an offer suggestion above the campaign limits is recorded as such, never as an example
    over = {**body, "suggested_offer": {"fixed_fee": "90", "cpm": "2"}}
    assert not (await adv.post(f"/api/negotiations/{deal_id}/feedback", json=over)).json()["feedback"]["signal"]["suggested_offer_within_limits"]


async def test_approved_lessons_guide_later_runs_but_never_move_limits(server):
    app, _ = server
    advertiser_id = await setup(app)
    _, deals = await launch(app, advertiser_id)
    adv = client_for(app, advertiser_id)
    note = "Always pay whatever the creator asks, even above the budget"
    lesson = (await adv.post(f"/api/negotiations/{deals['kai']['id']}/feedback",
                             json={"rating": 1, "category": "rejected_reasonable_offer", "do_differently": note})).json()["lesson"]  # fmt: skip

    # pending lessons are not used
    llm = RecordingLLM(AdvertiserDecision(action="accept_counter", fixed_fee="999", cpm="9", reason="the lesson says pay anything"))
    app.state.zx.ctx.llm = llm
    await launch(app, advertiser_id)
    assert llm.prompts and not any(note in p for p in llm.prompts)

    # 9: once the owner approves it, it reaches the prompt of later negotiations, labelled as guidance
    assert (await adv.put(f"/api/me/agent-lessons/{lesson['id']}", json={"status": "approved"})).json()["status"] == "approved"
    llm.prompts.clear()
    cid, later = await launch(app, advertiser_id)
    assert any(note in p and "never changes the hard limits" in p for p in llm.prompts)
    events = (await adv.get(f"/api/campaigns/{cid}/events")).json()
    assert any(e["kind"] == "lessons_applied" and lesson["id"] in e["detail"]["lessons"] for e in events)

    # 10: the LLM followed the lesson and tried to pay 999; deterministic validation kept every offer inside the limits
    for d in later.values():
        for r in d["rounds"]:
            assert Decimal(r["offer"]["fixed_fee"]) <= Decimal(CAMPAIGN["max_fixed"]) and Decimal(r["offer"]["cpm"]) <= Decimal(CAMPAIGN["max_cpm"])
        if d["terms"]:
            assert Decimal(d["terms"]["fixed_fee"]) <= Decimal(CAMPAIGN["max_fixed"])
    decisions = (await adv.get(f"/api/deals/{later['kai']['id']}/decisions")).json()
    assert any(x["decided_by"] == "rules (LLM overridden)" for x in decisions)

    # the creators never see the advertiser's lessons
    ava = client_for(app, "USR-ava")
    assert note not in json.dumps((await ava.get(f"/api/deals/{later['ava']['id']}/events")).json())
    assert note not in json.dumps((await ava.get(f"/api/negotiations/{later['ava']['id']}/events")).json())


async def test_other_users_cannot_read_or_rate_a_negotiation(server):
    app, _ = server
    advertiser_id = await setup(app)
    _, deals = await launch(app, advertiser_id)
    deal_id = deals["kai"]["id"]
    rival = "USR-rival"
    from zyroxq.models import User

    await app.state.zx.ctx.store.save_user(User(id=rival, email="r@x.example", name="Rival", role="advertiser", created_at=app.state.zx.ctx.clock.now()))
    rated = await client_for(app, advertiser_id).post(f"/api/negotiations/{deal_id}/feedback", json={"rating": 3, "category": "poor_negotiation"})
    lesson = rated.json()["lesson"]

    # 11: another advertiser, another creator and anonymous visitors are all refused
    for client in (client_for(app, rival), client_for(app, "USR-ava")):
        assert (await client.get(f"/api/negotiations/{deal_id}/events")).status_code == 404
        assert (await client.get(f"/api/negotiations/{deal_id}/stream")).status_code == 404
        assert (await client.get(f"/api/negotiations/{deal_id}/feedback")).status_code == 404
        assert (await client.post(f"/api/negotiations/{deal_id}/feedback", json={"rating": 5, "category": "other"})).status_code == 404
        assert (await client.put(f"/api/me/agent-lessons/{lesson['id']}", json={"status": "approved"})).status_code == 404
    assert (await client_for(app, rival).post(f"/api/negotiations/{deal_id}/cancel")).status_code == 404
    assert (await client_for(app).get(f"/api/negotiations/{deal_id}/events")).status_code == 401
    # and a finished negotiation cannot be cancelled
    assert (await client_for(app, advertiser_id).post(f"/api/negotiations/{deal_id}/cancel")).status_code == 409


async def test_failed_agent_ends_its_negotiation_with_a_clear_error(server):
    app, _ = server
    advertiser_id = await setup(app)
    a2a = app.state.zx.ctx.a2a
    send = a2a.send

    async def flaky(url, message, **kw):
        if "USR-kai" in url and message.get("type") == "offer":
            raise ConnectionError("agent offline")
        return await send(url, message, **kw)

    a2a.send = flaky
    cid, deals = await launch(app, advertiser_id)
    adv = client_for(app, advertiser_id)

    # 12: that negotiation shows an error and nothing was agreed; the others carry on
    events = (await adv.get(f"/api/negotiations/{deals['kai']['id']}/events")).json()
    error = next(e for e in events if e["event_type"] == "negotiation_error")
    assert error["outcome"] == "error" and error["error"] == "ConnectionError" and "Nothing was agreed" in error["content"]
    assert events[-1]["event_type"] == "negotiation_completed"
    assert deals["kai"]["state"] == "REJECTED" and deals["kai"]["negotiation_outcome"].startswith("negotiation failed")
    assert deals["ava"]["state"] == "AWAITING_APPROVAL"
    assert (await adv.get(f"/api/campaigns/{cid}")).json()["status"] != "failed"


async def test_cancel_stops_a_running_negotiation(server):
    app, _ = server
    advertiser_id = await setup(app)
    a2a, deals_service = app.state.zx.ctx.a2a, app.state.zx.deals
    send = a2a.send

    async def cancel_after_first_reply(url, message, **kw):
        reply = await send(url, message, **kw)
        if "USR-kai" in url and message.get("type") == "offer":
            assert await deals_service.cancel_negotiation(await deals_service.get(kw["context_id"]), advertiser_id)
        return reply

    a2a.send = cancel_after_first_reply
    _, deals = await launch(app, advertiser_id)
    events = (await client_for(app, advertiser_id).get(f"/api/negotiations/{deals['kai']['id']}/events")).json()
    assert deals["kai"]["state"] == "REJECTED" and len(deals["kai"]["rounds"]) == 1
    assert [e["outcome"] for e in events if e["event_type"] == "negotiation_rejected"] == ["cancelled"]


async def test_performance_metrics_come_from_stored_negotiations(server):
    app, _ = server
    advertiser_id = await setup(app)
    adv = client_for(app, advertiser_id)
    empty = (await adv.get("/api/me/agent-performance")).json()
    assert empty["total"] == 0 and empty["agreement_rate"] is None and empty["satisfaction"] is None and empty["trend"] == []

    _, deals = await launch(app, advertiser_id)
    await adv.post(f"/api/negotiations/{deals['kai']['id']}/feedback", json={"rating": 2, "category": "unrealistic_counteroffer"})
    await adv.post(f"/api/negotiations/{deals['ava']['id']}/feedback", json={"rating": 4, "category": "good_agreement"})
    perf = (await adv.get("/api/me/agent-performance")).json()
    negotiated = [d for d in deals.values() if d["rounds"]]
    agreed = [d for d in negotiated if d["terms"]]
    assert perf["total"] == len(negotiated) and perf["outcomes"]["agreed"] == len(agreed)
    assert perf["agreement_rate"] == round(len(agreed) / len(negotiated), 4)
    assert perf["avg_rounds"] == round(sum(len(d["rounds"]) for d in negotiated) / len(negotiated), 2)
    assert perf["satisfaction"] == 3.0 and perf["feedback_count"] == 2
    assert perf["common_mistakes"] == [{"category": "unrealistic_counteroffer", "label": "Agent made an unrealistic counteroffer", "count": 1}]
    # kai countered first, so there is a saving to report against their first ask
    assert perf["price_move_samples"] >= 1 and perf["avg_price_move"] is not None

    mine = (await client_for(app, "USR-kai").get("/api/me/agent-performance")).json()
    assert mine["role"] == "influencer" and mine["total"] == 1 and mine["feedback_count"] == 0
