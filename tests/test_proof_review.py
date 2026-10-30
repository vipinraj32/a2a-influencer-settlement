"""Rejected proofs explain themselves to both parties, and the creator can resubmit until the publish deadline."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from zyroxq.models import ProofCheck
from zyroxq.verifier.explain import explain_rejection, explain_sent_back

from .test_e2e import ATTESTED, CAMPAIGN, by_influencer, client_for, onboarded, post_video, server  # noqa: F401 - pytest fixtures
from .test_negotiation_api import fund


class SummaryLLM:
    def __init__(self, text):
        self.text = text

    def with_structured_output(self, schema, **kwargs):
        return self

    async def ainvoke(self, messages):
        if isinstance(self.text, Exception):
            raise self.text
        return SimpleNamespace(summary=self.text)


def terms_stub(now):
    return SimpleNamespace(channel_id="UCavachannel", publish_by=now + timedelta(days=1), required_tags=["#ad"], min_duration_seconds=15,
                           max_duration_seconds=60)  # fmt: skip


FAILS = [
    ProofCheck(name="exists", passed=True, detail="vid"),
    ProofCheck(name="public", passed=False, detail="unlisted"),
    ProofCheck(name="disclosure_and_brand", passed=False, detail="missing ['@fitfuel']"),
    ProofCheck(name="disclosure_label:US", passed=False, detail="no approved label (#ad) in the title or first 200 characters (US@1)"),
]


async def test_each_failed_check_gets_a_problem_and_a_fix():
    now = datetime(2026, 10, 30, tzinfo=UTC)
    review = await explain_rejection(FAILS, terms_stub(now), "@fitfuel", now - timedelta(hours=1), now)
    assert [i.check for i in review.issues] == ["public", "disclosure_and_brand", "disclosure_label:US"]
    assert "unlisted" in review.issues[0].problem and "Public" in review.issues[0].fix
    assert "@fitfuel" in review.issues[1].problem and "same link" in review.issues[1].fix
    assert review.can_resubmit and review.resubmit_by == now + timedelta(days=1) and review.explained_by == "rules"
    assert "3 checks failed" in review.summary

    # Published before the deal was agreed: a new upload is needed, still fixable before the deadline
    old = [ProofCheck(name="published_in_window", passed=False, detail="…")]
    early = await explain_rejection(old, terms_stub(now), "@fitfuel", now, now, published_at=now - timedelta(days=3))
    assert "before the deal was agreed" in early.issues[0].problem and early.can_resubmit
    late = await explain_rejection(old, terms_stub(now), "@fitfuel", now - timedelta(days=3), now, published_at=now + timedelta(days=2))
    assert not late.can_resubmit and late.resubmit_by is None and "cannot be fixed" in late.summary


async def test_llm_only_rewrites_the_summary():
    now = datetime(2026, 10, 30, tzinfo=UTC)
    review = await explain_rejection(FAILS, terms_stub(now), "@fitfuel", now, now, llm=SummaryLLM("Make it public and add @fitfuel."))
    assert review.summary == "Make it public and add @fitfuel." and review.explained_by == "llm"
    assert len(review.issues) == 3 and review.can_resubmit  # issues and verdict come from the templates
    fallback = await explain_rejection(FAILS, terms_stub(now), "@fitfuel", now, now, llm=SummaryLLM(RuntimeError("down")))
    assert fallback.explained_by == "rules"

    back = explain_sent_back("Label is hidden below the fold", terms_stub(now), now)
    assert back.decided_by == "advertiser" and back.issues[0].problem == "Label is hidden below the fold" and back.can_resubmit


async def test_rejection_is_explained_to_both_sides_and_resubmission_works(onboarded):
    app, videos, advertiser_id = onboarded
    adv, ava = client_for(app, advertiser_id), client_for(app, "USR-ava")
    cid = (await adv.post("/api/campaigns", json={**CAMPAIGN, "review_required": True})).json()["spec"]["id"]
    await app.state.zx.runner.wait_idle()
    await fund(app, adv, cid)
    deal_id = (await by_influencer(adv, cid))["ava"]["id"]

    bad = post_video(app, videos, "vidAvaBad", "UCavachannel", "FitFuel bites", 5_000)  # no #ad, no @fitfuel
    res = (await ava.post(f"/api/deals/{deal_id}/proof", json={"video_url": bad})).json()
    assert res["state"] == "PROOF_REJECTED"
    review = res["proof"]["review"]
    assert review["decided_by"] == "verifier" and review["can_resubmit"] and review["resubmit_by"]
    assert {"disclosure_and_brand", "disclosure_in_video:US"} <= {i["check"] for i in review["issues"]}

    # The advertiser sees the same explanation, and both timelines carry it
    seen = (await adv.get(f"/api/deals/{deal_id}")).json()["proof"]["review"]
    assert seen["summary"] == review["summary"]
    for client in (adv, ava):
        kinds = [e["kind"] for e in (await client.get(f"/api/deals/{deal_id}/events")).json()]
        assert "proof_rejected" in kinds

    good = post_video(app, videos, "vidAvaGood", "UCavachannel", "#ad FitFuel bites @fitfuel", 5_000)
    res = (await ava.post(f"/api/deals/{deal_id}/proof", json={"video_url": good, "attestations": ATTESTED})).json()
    assert res["state"] == "VERIFIED" and res["proof"]["attempt"] == 2 and res["proof"]["review"] is None
    assert [p["video_id"] for p in res["proof_history"]] == ["vidAvaBad"]

    # The advertiser's review sends it back with a reason; the creator resubmits again
    sent = (await adv.post(f"/api/deals/{deal_id}/compliance/review", json={"approved": False, "note": "Say 'sponsored' on screen"})).json()
    assert sent["state"] == "PROOF_REJECTED" and sent["proof"]["review"]["decided_by"] == "advertiser"
    assert sent["proof"]["review"]["issues"][0]["problem"] == "Say 'sponsored' on screen"
    res = (await ava.post(f"/api/deals/{deal_id}/proof", json={"video_url": good, "attestations": ATTESTED})).json()
    assert res["state"] == "VERIFIED" and res["proof"]["attempt"] == 3

    # After the deadline no resubmission is accepted
    deal = await app.state.zx.ctx.store.get_deal(deal_id)
    await app.state.zx.ctx.store.save_deal(deal.model_copy(update={"state": "PROOF_REJECTED"}))
    app.state.zx.ctx.clock.advance(timedelta(days=30))  # no scheduler tick: the deal stays PROOF_REJECTED
    late = await ava.post(f"/api/deals/{deal_id}/proof", json={"video_url": good, "attestations": ATTESTED})
    assert late.status_code >= 400 and "deadline" in late.text
    for c in (adv, ava):
        await c.aclose()
