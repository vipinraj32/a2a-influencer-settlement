"""Creator profiles: YouTube channel/uploads/comments client (mocked HTTP), the profile endpoint's privacy
and access rules, and the MCP tools on top of it."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import respx

from zyroxq.models import User
from zyroxq.verifier.channels import API, ChannelDetails, CommentsPage, FixtureChannelSource, VideoStats, YouTubeChannelApi, parse_channel_ref, summarize

from .test_e2e import CAMPAIGN, client_for, onboarded, server  # noqa: F401 - pytest fixtures

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
CHANNEL_ID = "UC" + "a" * 22


def channel_item(**stats):
    return {
        "id": CHANNEL_ID,
        "snippet": {"title": "Ava TV", "customUrl": "@avatv", "description": "Fitness Shorts", "country": "IN", "publishedAt": "2020-01-01T00:00:00Z",
                    "thumbnails": {"high": {"url": "https://yt.example/ava.jpg"}}},
        "statistics": {"viewCount": "1200000", "subscriberCount": "42000", "videoCount": "120", "hiddenSubscriberCount": False, **stats},
        "contentDetails": {"relatedPlaylists": {"uploads": "UU" + "a" * 22}},
        "topicDetails": {"topicCategories": ["https://en.wikipedia.org/wiki/Physical_fitness"]},
    }  # fmt: skip


def video_item(vid, days_ago, views, likes=None, comments=None):
    stats = {"viewCount": str(views)}
    if likes is not None:
        stats["likeCount"] = str(likes)
    if comments is not None:
        stats["commentCount"] = str(comments)
    published = (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {
        "id": vid,
        "snippet": {"channelId": CHANNEL_ID, "title": f"Short {vid}", "publishedAt": published, "tags": ["fitness"]},
        "statistics": stats,
        "contentDetails": {"duration": "PT45S"},
        "status": {"privacyStatus": "public", "madeForKids": False},
    }


def test_parse_channel_ref():
    assert parse_channel_ref(CHANNEL_ID) == ("id", CHANNEL_ID)
    assert parse_channel_ref(f"https://www.youtube.com/channel/{CHANNEL_ID}/videos") == ("id", CHANNEL_ID)
    assert parse_channel_ref("https://youtube.com/@avatv") == ("handle", "@avatv")
    assert parse_channel_ref("@avatv") == ("handle", "@avatv")
    assert parse_channel_ref("not a channel") is None


@respx.mock
async def test_channel_uploads_and_comments_from_the_data_api():
    channels = respx.get(f"{API}/channels").mock(return_value=httpx.Response(200, json={"items": [channel_item()]}))
    respx.get(f"{API}/playlistItems").mock(return_value=httpx.Response(200, json={"items": [{"contentDetails": {"videoId": v}} for v in ("v3", "v2", "v1")]}))
    uploads = [video_item("v1", 14, 8_000, 400, 40), video_item("v2", 7, 12_000, 600, 60), video_item("v3", 0, 4_000)]
    respx.get(f"{API}/videos").mock(return_value=httpx.Response(200, json={"items": uploads}))
    respx.get(f"{API}/commentThreads", params={"videoId": "v1"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "snippet": {
                            "totalReplyCount": 2,
                            "topLevelComment": {
                                "snippet": {"authorDisplayName": "@fan", "textDisplay": "Love it", "likeCount": 5, "publishedAt": "2026-10-20T00:00:00Z"}
                            },
                        }
                    }
                ]
            },
        )  # fmt: skip
    )
    respx.get(f"{API}/commentThreads", params={"videoId": "v3"}).mock(
        return_value=httpx.Response(403, json={"error": {"errors": [{"reason": "commentsDisabled"}]}})
    )
    yt = YouTubeChannelApi("test-key", httpx.AsyncClient(), now=lambda: NOW)

    details = await yt.channel("@avatv")
    assert details and details.channel_id == CHANNEL_ID and details.subscribers == 42_000 and details.country == "IN"
    assert details.url == "https://www.youtube.com/@avatv"
    assert channels.calls.last.request.url.params["forHandle"] == "@avatv"
    assert channels.calls.last.request.url.params["key"] == "test-key"

    recent = await yt.recent_videos(CHANNEL_ID, 3)
    assert [v.video_id for v in recent] == ["v3", "v2", "v1"]  # playlist order: newest first
    assert recent[0].likes is None and recent[0].engagement_rate is None  # hidden likes stay unknown
    assert recent[1].engagement_rate == 0.055 and recent[1].duration_seconds == 45

    summary = summarize(recent)
    assert summary.median_views == 8_000 and summary.engagement_sample == 2 and summary.mean_engagement_rate == 0.055
    assert summary.uploads_per_week == 1.0  # 2 gaps over 14 days

    calls = channels.call_count
    await yt.channel(CHANNEL_ID)
    await yt.channel(CHANNEL_ID)
    assert channels.call_count == calls  # recent_videos already looked it up by id: served from the cache

    page = await yt.comments("v1")
    assert page.enabled and page.comments[0].text == "Love it" and page.comments[0].replies == 2
    assert (await yt.comments("v3")).enabled is False


@respx.mock
async def test_hidden_subscribers_are_unknown():
    respx.get(f"{API}/channels").mock(return_value=httpx.Response(200, json={"items": [channel_item(hiddenSubscriberCount=True)]}))
    details = await YouTubeChannelApi("k", httpx.AsyncClient(), now=lambda: NOW).channel(CHANNEL_ID)
    assert details and details.subscribers is None


def fixture_channels(now) -> FixtureChannelSource:
    source = FixtureChannelSource()
    source.channels["UCavachannel"] = ChannelDetails(channel_id="UCavachannel", title="Ava TV", handle="@avatv", subscribers=42_000, total_views=900_000,
                                                     video_count=80, observed_at=now)  # fmt: skip
    source.uploads["UCavachannel"] = [
        VideoStats(
            video_id=f"avaVid{i}",
            channel_id="UCavachannel",
            title=f"Short {i}",
            published_at=now - timedelta(days=3 * i),
            views=10_000 + i * 1_000,
            likes=500,
            comments=50,
            observed_at=now,
        )  # fmt: skip
        for i in range(4)
    ]
    source.comment_pages["avaVid0"] = CommentsPage(video_id="avaVid0", enabled=False, observed_at=now)
    return source


async def test_profile_shows_everything_public_and_nothing_private(onboarded):
    app, _, advertiser_id = onboarded
    ctx = app.state.zx.ctx
    ctx.channels = fixture_channels(ctx.clock.now())
    rival = User(id="USR-rival", email="rival@example.com", name="Rival", role="advertiser", created_at=ctx.clock.now())
    await ctx.store.save_user(rival)
    async with client_for(app, advertiser_id) as adv, client_for(app, rival.id) as other, client_for(app, "USR-ava") as ava:
        cid = (await adv.post("/api/campaigns", json=CAMPAIGN)).json()["spec"]["id"]
        await app.state.zx.runner.wait_idle()

        res = await adv.get("/api/influencers/USR-ava/profile")
        assert res.status_code == 200, res.text
        profile = res.json()
        assert profile["influencer"]["name"] == "Ava TV" and profile["influencer"]["listed"]
        assert profile["verified_channel"]["channel_id"] == "UCavachannel"
        yt = profile["youtube"]
        assert yt["available"] and yt["channel"]["total_views"] == 900_000 and len(yt["recent_videos"]) == 4
        assert yt["engagement"]["median_views"] == 11_500 and yt["engagement"]["sample_size"] == 4
        assert [d["campaign_id"] for d in profile["deals"]] == [cid]
        assert any(e["metric"] == "subscribers" and e["verified"] for e in profile["evidence"])
        text = res.text
        for private in ("rate_card", "fixed_min", "cpm_min", "agent_key", "weights", "acceptance"):
            assert private not in text, private

        # Another advertiser sees the creator but not FitFuel's deals; the creator sees their own
        assert (await other.get("/api/influencers/USR-ava/profile")).json()["deals"] == []
        assert len((await ava.get("/api/influencers/USR-ava/profile")).json()["deals"]) == 1
        assert (await adv.get("/api/influencers/USR-fitfuel/profile")).status_code == 404

        # Paused agents leave the directory, but an advertiser with a contract can still open the profile
        user = await ctx.store.get_user("USR-ava")
        await ctx.store.save_user(user.model_copy(update={"agent_active": False}))
        assert (await adv.get("/api/influencers/USR-ava/profile")).json()["influencer"]["listed"] is False
        assert (await other.get("/api/influencers/USR-ava/profile")).status_code == 404

        assert (await adv.get("/api/channels/@avatv/videos?limit=2")).json()["engagement"]["sample_size"] == 2
        assert (await adv.get("/api/videos/avaVid1/stats")).json()["engagement_rate"] == 0.05
        assert (await adv.get("/api/videos/avaVid0/comments")).json()["enabled"] is False

    # MCP tools read the same data (operator token)
    result = await app.state.mcp.call_tool("get_influencer_profile", {"influencer_id": "USR-ava", "recent_videos": 2})
    content = result[0] if isinstance(result, tuple) else result
    data = json.loads(content[0].text)
    assert len(data["youtube"]["recent_videos"]) == 2 and "rate_card" not in content[0].text
    result = await app.state.mcp.call_tool("analyze_video_engagement", {"video": "avaVid2"})
    content = result[0] if isinstance(result, tuple) else result
    assert json.loads(content[0].text)["comments_per_view"] == 50 / 12_000


async def test_profile_without_youtube_key_still_shows_sign_in_data(onboarded):
    app, _, advertiser_id = onboarded
    assert app.state.zx.ctx.channels is None
    async with client_for(app, advertiser_id) as adv:
        profile = (await adv.get("/api/influencers/USR-raj/profile")).json()
        assert profile["youtube"]["available"] is False and "YOUTUBE_API_KEY" in profile["youtube"]["error"]
        assert profile["verified_channel"]["subscribers"] == 88_000
        assert (await adv.get("/api/channels/@rajtv")).status_code == 503
