"""Content agent: requirements checked inside the video (spoken mentions from captions, logo by vision),
suggested from the brief, signed with it, and wired into proof verification and the advertiser's review."""

from datetime import UTC, datetime
from types import SimpleNamespace

from zyroxq.models import ContentCheck
from zyroxq.verifier.content import (
    CaptionLine,
    Captions,
    FixtureCaptions,
    FixtureFrames,
    Frame,
    LogoVerdict,
    analyze_content,
    count_mentions,
    rule_checks,
    suggest_checks,
)

from .test_e2e import ATTESTED, CAMPAIGN, by_influencer, client_for, onboarded, post_video, server  # noqa: F401 - pytest fixtures
from .test_negotiation_api import fund

NOW = datetime(2026, 10, 30, tzinfo=UTC)
SAY = ContentCheck(id="say_brand", kind="spoken_mention", phrase="FitFuel", min_count=4, max_count=5)
LOGO = ContentCheck(id="show_logo", kind="logo_visible", phrase="FitFuel")


def captions(*lines: str, kind="auto_captions") -> Captions:
    return Captions(kind=kind, language="en", lines=[CaptionLine(start=i * 4.0, text=t) for i, t in enumerate(lines)])


class VisionLLM:
    def __init__(self, verdict):
        self.verdict = verdict
        self.messages = None

    def with_structured_output(self, schema, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.messages = messages
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict


def test_mentions_are_counted_in_any_spelling_the_captions_use():
    track = captions("hey guys this is fit fuel", "FitFuel protein bites are", "great, Fitfuel!", "my code at fit", "fuel dot com")
    hits = count_mentions(track, "FitFuel", [])
    assert len(hits) == 4  # "fit fuel", "FitFuel", "Fitfuel", and "fit / fuel" split across two lines
    assert hits[0] == (0.0, "hey guys this is fit fuel") and hits[3][0] == 12.0
    assert len(count_mentions(captions("I love fitness fuel"), "FitFuel", [])) == 0


def test_rules_read_counts_and_logo_from_the_brief():
    brief = "## Must show\n- Show the FitFuel logo on the pack.\n- Mention the company name in your real voice 4/5 times.\n"
    checks = rule_checks(brief, "FitFuel")
    say = next(c for c in checks if c.kind == "spoken_mention")
    assert (say.min_count, say.max_count, say.phrase) == (4, 5, "FitFuel")
    assert any(c.kind == "logo_visible" for c in checks)
    assert rule_checks("Say FitFuel twice.", "FitFuel")[0].min_count == 2
    assert rule_checks("Unboxing and taste reaction.", "FitFuel") == []


async def test_suggestions_without_llm_use_the_rules():
    checks, method = await suggest_checks("Mention FitFuel three times.", "FitFuel")
    assert method == "rules" and checks[0].min_count == 3


async def test_caption_counts_decide_and_vision_only_advises():
    source = FixtureCaptions()
    source.tracks["vid"] = captions("FitFuel", "FitFuel again", "FitFuel")
    frames = FixtureFrames()
    frames.by_video["vid"] = [Frame(url="https://i.ytimg.com/vi/vid/hq1.jpg", data=b"jpeg")]
    llm = VisionLLM(LogoVerdict(visible=True, confidence=0.9, frames_with_logo=[1], description="Pack with the FitFuel logo in hand"))
    report = await analyze_content([SAY, LOGO], "vid", source, frames, llm, NOW)
    say, logo = report.results
    assert say.status == "fail" and say.decisive and say.observed["count"] == 3 and say.method == "captions"
    assert logo.status == "pass" and not logo.decisive and logo.method == "vision" and "hq1.jpg" in logo.evidence[1]
    assert report.needs_human  # a vision result always goes to the advertiser
    image = llm.messages[1].content[1]
    assert image["type"] == "image" and image["source"]["type"] == "base64"

    unsure = await analyze_content([LOGO], "vid", source, frames, VisionLLM(LogoVerdict(visible=True, confidence=0.4)), NOW)
    assert unsure.results[0].status == "unverified"
    broken = await analyze_content([LOGO], "vid", source, frames, VisionLLM(RuntimeError("down")), NOW)
    assert broken.results[0].status == "unverified" and "failed" in broken.results[0].detail
    nothing = await analyze_content([SAY], "missing", source, frames, None, NOW)
    assert nothing.transcript == "unavailable" and nothing.results[0].status == "unverified"


async def test_checks_are_signed_verified_and_reviewed(onboarded):
    app, videos, advertiser_id = onboarded
    ctx = app.state.zx.ctx
    ctx.captions, ctx.frames = FixtureCaptions(), FixtureFrames()
    adv, ava = client_for(app, advertiser_id), client_for(app, "USR-ava")
    brief = CAMPAIGN["requirements"] + "\nMention FitFuel in your real voice 4/5 times and show the FitFuel logo.\n"
    suggested = (await adv.post("/api/campaigns/content-checks/suggest", json={"requirements": brief, "brand": "FitFuel"})).json()
    kinds = {c["kind"] for c in suggested["checks"]}
    assert kinds == {"spoken_mention", "logo_visible"}

    campaign = (await adv.post("/api/campaigns", json={**CAMPAIGN, "requirements": brief, "content_checks": suggested["checks"]})).json()
    cid = campaign["spec"]["id"]
    await app.state.zx.runner.wait_idle()
    deal = (await by_influencer(adv, cid))["ava"]
    assert deal["state"] == "AWAITING_APPROVAL"  # the creator's agent signed the document including the checks
    mine = next(d for d in (await ava.get("/api/influencer/deals")).json() if d["id"] == deal["id"])
    assert {c["id"] for c in mine["campaign"]["content_checks"]} == {c["id"] for c in suggested["checks"]}
    await fund(app, adv, cid)

    # Said only twice: rejected with the count, resubmittable
    url = post_video(app, videos, "vidAvaSay2", "UCavachannel", "#ad FitFuel bites @fitfuel", 5_000)
    ctx.captions.tracks["vidAvaSay2"] = captions("FitFuel bites", "so good", "fit fuel")
    res = (await ava.post(f"/api/deals/{deal['id']}/proof", json={"video_url": url, "attestations": ATTESTED})).json()
    assert res["state"] == "PROOF_REJECTED"
    failed = next(c for c in res["proof"]["checks"] if c["name"] == "content:say_brand")
    assert not failed["passed"] and "2 time(s)" in failed["detail"]
    assert any(i["check"] == "content:say_brand" and "captions" in i["fix"] for i in res["proof"]["review"]["issues"])
    assert res["proof"]["content"]["results"][1]["status"] == "unverified"  # no vision model in tests

    # Said five times: verified, but the logo was not machine-checked, so the advertiser must review
    url = post_video(app, videos, "vidAvaSay5", "UCavachannel", "#ad FitFuel bites @fitfuel", 5_000)
    ctx.captions.tracks["vidAvaSay5"] = captions(*["FitFuel"] * 5)
    res = (await ava.post(f"/api/deals/{deal['id']}/proof", json={"video_url": url, "attestations": ATTESTED})).json()
    assert res["state"] == "VERIFIED", res["proof"]["checks"]
    assert res["proof"]["content"]["results"][0]["observed"]["count"] == 5
    await adv.post("/api/dev/advance", json={"hours": 49})
    assert (await adv.get(f"/api/deals/{deal['id']}")).json()["state"] == "VERIFIED"  # held for review
    row = next(d for d in (await adv.get(f"/api/campaigns/{cid}/compliance")).json()["deals"] if d["deal_id"] == deal["id"])
    assert row["review"] == "pending" and any("content agent" in g for g in row["gates"])
    assert (await adv.post(f"/api/deals/{deal['id']}/compliance/review", json={"approved": True, "note": "logo at 0:05"})).status_code == 200
    await adv.post("/api/dev/advance", json={"hours": 1})
    assert (await adv.get(f"/api/deals/{deal['id']}")).json()["state"] == "FIXED_RELEASED"
    for c in (adv, ava):
        await c.aclose()


def test_requirements_document_without_checks_is_unchanged():
    from zyroxq.models import CampaignSpec

    spec = SimpleNamespace(requirements="Brief", content_checks=[])
    assert CampaignSpec.requirements_document(spec) == "Brief"
    spec.content_checks = [SAY]
    assert "[say_brand]" in CampaignSpec.requirements_document(spec)
