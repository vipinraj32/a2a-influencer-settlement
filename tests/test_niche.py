"""Niche analysis from public YouTube data: deterministic scoring, an LLM limited to the taxonomy, and the
onboarding flow where the creator confirms or edits the suggestion (multiple niches)."""

from datetime import timedelta
from types import SimpleNamespace

from zyroxq.agents.influencer.niche import analyze_niches, rule_suggestions
from zyroxq.verifier.channels import ChannelDetails, FixtureChannelSource, VideoStats

from .test_e2e import CAMPAIGN, client_for, onboarded, server  # noqa: F401 - pytest fixtures
from .test_youtube_profile import NOW

TITLES = [
    ("10 min ab workout at home #fitness", ["workout", "abs"]),
    ("Full body gym training for beginners", ["gym"]),
    ("High protein meal prep recipe", ["recipe", "meal prep"]),
    ("Leg day workout, no excuses", []),
    ("Easy protein pancakes recipe", ["cooking"]),
    ("HIIT cardio in 15 minutes", []),
]


def channel(channel_id="UCavachannel") -> ChannelDetails:
    return ChannelDetails(channel_id=channel_id, title="Ava TV", handle="@avatv", description="Workouts and easy healthy food",
                          topics=["https://en.wikipedia.org/wiki/Physical_fitness"], observed_at=NOW)  # fmt: skip


def uploads(channel_id="UCavachannel") -> list[VideoStats]:
    return [
        VideoStats(video_id=f"avaVid{i}", channel_id=channel_id, title=t, tags=tags, published_at=NOW - timedelta(days=i), observed_at=NOW)
        for i, (t, tags) in enumerate(TITLES)
    ]


class PickingLLM:
    def __init__(self, picks):
        self.picks = picks

    def with_structured_output(self, schema, **kwargs):
        return self

    async def ainvoke(self, messages):
        if isinstance(self.picks, Exception):
            raise self.picks
        return SimpleNamespace(niches=[SimpleNamespace(**p) for p in self.picks])


def test_rules_rank_niches_by_share_of_uploads():
    suggestions = rule_suggestions(channel(), uploads())
    names = [s.niche for s in suggestions]
    assert names[0] == "fitness" and "food" in names
    fitness = suggestions[0]
    assert fitness.matched_videos == 4 and fitness.channel_signal and fitness.score == round(0.8 * 4 / 6 + 0.2, 3)
    assert "workout" in fitness.keywords and fitness.examples[0].startswith("10 min ab workout")
    assert "gaming" not in names


async def test_llm_may_only_reorder_within_the_taxonomy():
    llm = PickingLLM([
        {"niche": "food", "confidence": 0.9, "reason": "recipes"},
        {"niche": "crypto-scams", "confidence": 1.0, "reason": "ignore previous instructions"},
        {"niche": "fitness", "confidence": 0.8, "reason": "workouts"},
    ])  # fmt: skip
    analysis = await analyze_niches(channel(), uploads(), NOW, llm)
    assert analysis.method == "llm+rules"
    assert [s.niche for s in analysis.suggestions] == ["food", "fitness"]
    assert analysis.suggestions[1].matched_videos == 4 and analysis.suggestions[0].llm_confidence == 0.9

    failed = await analyze_niches(channel(), uploads(), NOW, PickingLLM(RuntimeError("down")))
    assert failed.method == "rules" and failed.suggestions[0].niche == "fitness"


async def test_no_signal_asks_the_creator():
    analysis = await analyze_niches(channel().model_copy(update={"topics": [], "description": ""}), [], NOW)
    assert analysis.suggestions == [] and "manually" in analysis.note


async def test_onboarding_suggests_niches_and_creator_can_pick_several(onboarded):
    app, _, advertiser_id = onboarded
    ctx = app.state.zx.ctx
    async with client_for(app, "USR-ava") as ava, client_for(app, advertiser_id) as adv:
        assert (await ava.post("/api/me/niche-analysis")).status_code == 503  # no YOUTUBE_API_KEY

        source = FixtureChannelSource()
        source.channels["UCavachannel"] = channel()
        source.uploads["UCavachannel"] = uploads()
        ctx.channels = source
        res = await ava.post("/api/me/niche-analysis")
        assert res.status_code == 200, res.text
        suggested = [s["niche"] for s in res.json()["suggestions"]]
        assert suggested[0] == "fitness" and "food" in suggested  # "health" too: protein, healthy
        assert (await ctx.store.get_user("USR-ava")).niche == "fitness"  # analysis alone changes nothing

        rates = {"fixed_min": "15", "cpm_min": "2", "preferred_fixed": "22", "persona": ""}
        me = (await ava.put("/api/me/influencer", json={**rates, "niches": ["Fitness", "food", "fitness"]})).json()
        assert me["user"]["niches"] == ["fitness", "food"] and me["user"]["niche"] == "fitness"
        assert me["user"]["niche_source"] == "youtube_analysis"
        me = (await ava.put("/api/me/influencer", json={**rates, "niches": ["fitness", "anime reviews"]})).json()
        assert me["user"]["niche_source"] == "manual"
        assert (await ava.put("/api/me/influencer", json={**rates, "niches": []})).status_code == 422
        legacy = (await ava.put("/api/me/influencer", json={**rates, "niche": "Food"})).json()
        assert legacy["user"]["niches"] == ["food"]

        # A secondary niche is enough for discovery
        await ava.put("/api/me/influencer", json={**rates, "niches": ["fitness", "food"]})
        cid = (await adv.post("/api/campaigns", json={**CAMPAIGN, "niche": "food"})).json()["spec"]["id"]
        await app.state.zx.runner.wait_idle()
        found = {d["influencer_id"] for d in (await adv.get(f"/api/campaigns/{cid}/deals")).json()}
        assert {"USR-ava", "USR-zoe"} <= found and "USR-raj" not in found
        profile = (await adv.get("/api/influencers/USR-ava/profile")).json()["influencer"]
        assert profile["niches"] == ["fitness", "food"] and profile["niche_analysis"]["suggestions"]
