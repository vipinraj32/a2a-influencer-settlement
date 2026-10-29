import { Link, useParams } from "react-router";
import { api } from "../api";
import { EvidenceTable } from "../components/Evidence";
import { Address, Avatar, Card, Empty, ErrorText, Notice, Spinner, StateBadge, Stat } from "../components/ui";
import { MODEL_LABEL, humanize, num, usd, when } from "../format";
import { useLiveEvents, useResource } from "../live";
import { useMe } from "../session";
import type { CreatorProfileView } from "../types";

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(2)}%`);
const duration = (s: number | null) => (s === null ? "—" : s < 3600 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : `${(s / 3600).toFixed(1)} h`);
const topic = (url: string) => decodeURIComponent(url.split("/").pop() ?? url).replace(/_/g, " ");
const day = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString() : "—");

/** Everything an advertiser may see about one creator. The creator sees the same page as "My public profile". */
export function CreatorProfile() {
  const { user } = useMe();
  const params = useParams();
  const id = params.id ?? user.id;
  const isSelf = id === user.id;
  const view = useResource(() => api.creatorProfile(id, 12), `creator-profile:${id}`);
  useLiveEvents((e) => e.type === "deal", view.reload); // the stream only carries this user's deals

  if (view.error) return <ErrorText error={view.error} />;
  if (!view.data) return <Spinner label="Loading creator profile and YouTube statistics…" />;
  return <ProfileBody p={view.data} isSelf={isSelf} />;
}

function ProfileBody({ p, isSelf }: { p: CreatorProfileView; isSelf: boolean }) {
  const { influencer: who, youtube: yt, verified_channel: signedIn } = p;
  const ch = yt.channel;
  const e = yt.engagement;
  const subscribers = ch ? ch.subscribers : signedIn && !signedIn.subscribers_hidden ? signedIn.subscribers : null;
  const pub = p.public_profile;

  return (
    <div className="stack creator-profile">
      {ch?.banner_url && <div className="banner" style={{ backgroundImage: `url(${ch.banner_url})` }} />}
      <div className="page-head">
        <div className="who-cell">
          <Avatar src={ch?.thumbnail_url || who.avatar_url} name={who.name} size={64} />
          <div>
            {!isSelf && (
              <Link to="/advertiser/creators" className="muted small">
                ← Creators
              </Link>
            )}
            <h1>
              {who.name} {ch?.handle && <span className="muted">{ch.handle}</span>}
            </h1>
            <p className="muted small">
              {who.niches.length ? who.niches.join(", ") : "no niche set"}{who.niche_source === "youtube_analysis" ? " (from channel analysis)" : ""} · {ch?.country ? `channel country ${ch.country} · ` : ""}on ZyroXQ since {day(who.member_since)} ·{" "}
              <span className="mono">{who.id}</span>
            </p>
          </div>
        </div>
        <div className="row">
          <span className={`chip ${who.listed ? "good" : "warn"}`}>{who.listed ? "Agent live" : "Agent not listed"}</span>
          {ch && (
            <a className="btn btn-ghost btn-sm" href={ch.url} target="_blank" rel="noreferrer">
              Open on YouTube
            </a>
          )}
        </div>
      </div>
      {isSelf && <Notice>This is what advertisers see when they open your profile. Your rate floors and agent policy are never shown.</Notice>}
      {!yt.available && <Notice tone="warn">Live YouTube statistics unavailable: {yt.error}. Showing what was verified at sign-in.</Notice>}

      <div className="stats">
        <Stat label="Subscribers" value={subscribers === null ? "hidden" : num(subscribers)} hint="YouTube rounds public subscriber counts" />
        <Stat label="Total views" value={num(ch?.total_views ?? null)} />
        <Stat label="Public videos" value={num(ch?.video_count ?? null)} />
        <Stat label="Median views (recent)" value={num(e?.median_views ?? null)} hint={e?.limitations} />
        <Stat label="Engagement (recent)" value={pct(e?.mean_engagement_rate)} hint="(likes + comments) ÷ views, over videos where both are visible" />
        <Stat label="Uploads / week" value={e?.uploads_per_week ?? "—"} hint={e?.window_days ? `over the last ${e.window_days} days` : undefined} />
      </div>
      {(ch || e) && (
        <p className="small muted">
          Live from the YouTube Data API {ch ? when(ch.observed_at) : ""}. Recent figures cover the latest {e?.sample_size ?? 0} public uploads
          {e && e.engagement_sample < e.sample_size ? ` (engagement over ${e.engagement_sample} with visible likes and comments)` : ""}. They describe
          the past and are not a forecast for a sponsored post.
        </p>
      )}

      <div className="split">
        <Card title="Channel">
          {ch ? (
            <dl className="kv">
              <dt>Channel</dt>
              <dd>
                <a href={ch.url} target="_blank" rel="noreferrer">
                  {ch.title}
                </a>{" "}
                <span className="muted small mono">{ch.channel_id}</span>
              </dd>
              <dt>Created</dt>
              <dd>{day(ch.published_at)}</dd>
              <dt>Country</dt>
              <dd>{ch.country ?? "—"}</dd>
              <dt>Topics</dt>
              <dd>{ch.topics.length ? ch.topics.map(topic).join(", ") : "—"}</dd>
              {ch.keywords && (
                <>
                  <dt>Keywords</dt>
                  <dd className="small">{ch.keywords}</dd>
                </>
              )}
              <dt>About</dt>
              <dd className="small pre">{ch.description || "—"}</dd>
            </dl>
          ) : signedIn ? (
            <dl className="kv">
              <dt>Channel</dt>
              <dd>
                {signedIn.title} <span className="muted small mono">{signedIn.channel_id}</span>
              </dd>
              <dt>Subscribers</dt>
              <dd>{signedIn.subscribers_hidden ? "hidden" : num(signedIn.subscribers)}</dd>
              <dt>Verified</dt>
              <dd>{when(signedIn.verified_at)} at Google sign-in</dd>
            </dl>
          ) : (
            <Empty>No YouTube channel connected.</Empty>
          )}
        </Card>

        <Card title="Agent and payouts">
          <dl className="kv">
            <dt>Payout wallet</dt>
            <dd>
              <Address value={who.wallet} />
            </dd>
            <dt>Agent signing key</dt>
            <dd>
              <Address value={who.agent_signer} /> <span className="muted small">authorized by the wallet</span>
            </dd>
            <dt>A2A Agent Card</dt>
            <dd>
              {who.agent_url ? (
                <a href={new URL(".well-known/agent-card.json", who.agent_url).pathname} target="_blank" rel="noreferrer">
                  agent-card.json
                </a>
              ) : (
                <span className="muted">not listed (agent paused or onboarding incomplete)</span>
              )}
            </dd>
            {pub && (
              <>
                <dt>Deal types</dt>
                <dd>{pub.accepted_models.map((m) => MODEL_LABEL[m] ?? m).join(", ") || "—"}</dd>
                <dt>Disclosure label</dt>
                <dd>{pub.disclosure_label}</dd>
                <dt>Audience countries</dt>
                <dd>
                  {pub.creator.audience_countries.join(", ") || "—"} <span className="chip warn">self-reported</span>
                </dd>
                <dt>Languages</dt>
                <dd>{pub.creator.content_languages.join(", ") || "—"}</dd>
                {pub.creator.bio && (
                  <>
                    <dt>Bio</dt>
                    <dd className="small">{pub.creator.bio}</dd>
                  </>
                )}
              </>
            )}
          </dl>
        </Card>
      </div>

      <Card title={`Recent uploads (${yt.recent_videos.length})`}>
        {yt.recent_videos.length === 0 ? (
          <Empty>{yt.available ? "No public uploads returned." : "Needs the YouTube Data API (YOUTUBE_API_KEY on the server)."}</Empty>
        ) : (
          <div className="table-scroll">
            <table className="table compact">
              <thead>
                <tr>
                  <th>Video</th>
                  <th>Published</th>
                  <th>Length</th>
                  <th>Views</th>
                  <th>Likes</th>
                  <th>Comments</th>
                  <th>Engagement</th>
                </tr>
              </thead>
              <tbody>
                {yt.recent_videos.map((v) => (
                  <tr key={v.video_id}>
                    <td>
                      <a className="video-cell" href={v.url} target="_blank" rel="noreferrer">
                        {v.thumbnail_url && <img src={v.thumbnail_url} alt="" width={64} height={36} referrerPolicy="no-referrer" />}
                        <span>{v.title}</span>
                      </a>
                    </td>
                    <td className="small">{day(v.published_at)}</td>
                    <td>{duration(v.duration_seconds)}</td>
                    <td>{num(v.views)}</td>
                    <td title={v.likes === null ? "hidden by the creator" : undefined}>{v.likes === null ? "hidden" : num(v.likes)}</td>
                    <td title={v.comments === null ? "comments are off" : undefined}>{v.comments === null ? "off" : num(v.comments)}</td>
                    <td>{pct(v.engagement_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Evidence used by the agents">
        <EvidenceTable items={p.evidence} />
        <p className="small muted">Hover a row for its limitations. Unknown values stay unknown; nothing is estimated.</p>
      </Card>

      <Card title={isSelf ? "Your deals" : `Your deals with ${who.name} (${p.deals.length})`}>
        {p.deals.length === 0 ? (
          <Empty>No deals yet.</Empty>
        ) : (
          <table className="table compact">
            <thead>
              <tr>
                <th>Campaign</th>
                <th>State</th>
                <th>Terms</th>
                <th>Post</th>
                <th>Paid</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {p.deals.map((d) => (
                <tr key={d.deal_id}>
                  <td>
                    {isSelf ? d.campaign : <Link to={`/advertiser/campaigns/${encodeURIComponent(d.campaign_id)}`}>{d.campaign}</Link>}
                    <div className="muted small mono">{d.deal_id}</div>
                  </td>
                  <td>
                    <StateBadge state={d.state} />
                  </td>
                  <td className="small">
                    {d.terms
                      ? `${MODEL_LABEL[d.terms.compensation_model] ?? humanize(d.terms.compensation_model)} · ${usd(d.terms.fixed_fee)} + ${usd(d.terms.cpm)} CPM · cap ${usd(d.terms.cap)}`
                      : "—"}
                  </td>
                  <td className="small">
                    {d.video_id ? (
                      <a href={`https://www.youtube.com/watch?v=${d.video_id}`} target="_blank" rel="noreferrer">
                        {d.video_id}
                      </a>
                    ) : (
                      "—"
                    )}
                    {d.views !== null && <div className="muted">{num(d.views)} views at check</div>}
                  </td>
                  <td>{usd(d.paid)}</td>
                  <td className="small muted">{when(d.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
