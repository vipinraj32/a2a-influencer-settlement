import { useState } from "react";
import { Link } from "react-router";
import { api } from "../../api";
import { DealDetail } from "../../components/DealDetail";
import { Requirements } from "../../components/Requirements";
import { ActionButton, Avatar, Card, Empty, ErrorText, Notice, Spinner, StateBadge, Stat } from "../../components/ui";
import { DELIVERABLE_LABEL, humanize, num, usd, videoLength, when } from "../../format";
import { useLiveEvents, useResource } from "../../live";
import { useMe, useSession } from "../../session";
import type { InfluencerDeal } from "../../types";

const NEEDS_PROOF = new Set(["FUNDED", "PROOF_REJECTED"]);
const IN_ESCROW = new Set(["FUNDED", "PROOF_SUBMITTED", "PROOF_REJECTED", "VERIFIED", "FIXED_RELEASED", "DISPUTED"]);

function ProofForm({ deal, onDone }: { deal: InfluencerDeal; onDone: () => void }) {
  const { status } = useSession();
  const [url, setUrl] = useState("");
  const [inVideo, setInVideo] = useState(false);
  const tags = deal.terms?.required_tags.join(" ") ?? "#ad";
  const rules = deal.terms?.disclosure_policies ?? [];
  return (
    <div className="proof-form">
      <p>
        Post your {DELIVERABLE_LABEL[deal.campaign.deliverable] ?? deal.campaign.deliverable} on your verified channel before{" "}
        <strong>{when(deal.terms?.publish_by)}</strong>. Put <code>{deal.campaign.brand_handle}</code> and <code>{tags}</code> in the description, then
        paste the link here.
      </p>
      {deal.campaign.max_duration_seconds > 0 && (
        <p className="small">
          Video length must be <strong>{videoLength(deal.campaign.min_duration_seconds, deal.campaign.max_duration_seconds)}</strong>. The verifier reads it
          from YouTube.
        </p>
      )}
      {rules.length > 0 && (
        <p className="small">
          Disclosure rules {rules.join(", ")}: put the label in the title or at the very start of the description, and show or say it in the video itself.
        </p>
      )}
      <label className="check">
        <input type="checkbox" checked={inVideo} onChange={(e) => setInVideo(e.target.checked)} />I confirm the sponsorship disclosure is shown or said in the
        video itself (YouTube's API cannot check this, so it is recorded as your statement)
      </label>
      {!status?.verifier_online && <Notice tone="warn">The verifier is offline: the server needs YOUTUBE_API_KEY.</Notice>}
      <div className="row">
        <input className="grow" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://youtube.com/shorts/…" maxLength={300} />
        <ActionButton
          disabled={!url.trim()}
          onClick={async () => {
            await api.submitProof(deal.id, url.trim(), inVideo ? { in_video_disclosure: true } : {});
            setUrl("");
            onDone();
          }}
        >
          Submit for verification
        </ActionButton>
      </div>
    </div>
  );
}

export function InfluencerHome() {
  const me = useMe();
  const user = me.user;
  const deals = useResource(api.influencerDeals, "my-deals");
  const inbox = useResource(api.influencerInbox, "inbox");
  const [selected, setSelected] = useState<string | null>(null);
  useLiveEvents(
    () => true, // the stream only carries events for this user's own deals
    () => {
      void deals.reload();
      void inbox.reload();
    },
  );

  const list = deals.data ?? [];
  const earned = list.flatMap((d) => d.payouts).filter((p) => p.kind !== "refund").reduce((s, p) => s + Number(p.amount), 0);
  const locked = list
    .filter((d) => IN_ESCROW.has(d.state) && d.terms)
    .reduce((s, d) => s + Number(d.terms!.cap) - d.payouts.filter((p) => p.kind !== "refund").reduce((a, p) => a + Number(p.amount), 0), 0);
  const todo = list.filter((d) => NEEDS_PROOF.has(d.state));
  const deal = list.find((d) => d.id === selected) ?? null;

  return (
    <div className="stack">
      <div className="page-head">
        <div className="who-cell">
          <Avatar src={user.channel?.thumbnail_url} name={user.channel?.title ?? user.name} size={44} />
          <div>
            <h1>{user.channel?.title}</h1>
            <p className="muted small">
              {num(user.channel?.subscribers)} subscribers · {(user.niches?.length ? user.niches : [user.niche]).join(", ")} ·{" "}
              {me.agent_listed ? <span className="chip good">agent live</span> : <span className="chip warn">agent paused</span>}
            </p>
          </div>
        </div>
        <Link to="/influencer/agent" className="btn btn-secondary">
          Agent settings
        </Link>
      </div>

      {!me.agent_listed && (
        <Notice tone="warn">
          Your agent is not discoverable. <Link to="/influencer/agent">Switch it on</Link> so advertiser agents can negotiate with you.
        </Notice>
      )}

      <div className="stats">
        <Stat label="Earned" value={usd(earned)} />
        <Stat label="Waiting in escrow for you" value={usd(locked)} hint="Up to this much, depending on views" />
        <Stat label="Deals" value={list.length} />
        <Stat label="Need your video" value={todo.length} />
      </div>

      <div className="split">
        <Card title="My deals">
          <ErrorText error={deals.error} />
          {deals.loading && !deals.data ? (
            <Spinner />
          ) : list.length === 0 ? (
            <Empty>No deals yet. When an advertiser's agent contacts yours, the negotiation appears here live.</Empty>
          ) : (
            <table className="table selectable">
              <thead>
                <tr>
                  <th>Brand</th>
                  <th>State</th>
                  <th>Fixed</th>
                  <th>CPM</th>
                  <th>Cap</th>
                  <th>Publish by</th>
                </tr>
              </thead>
              <tbody>
                {list.map((d) => (
                  <tr key={d.id} className={d.id === selected ? "selected" : ""} onClick={() => setSelected(d.id)}>
                    <td>
                      <strong>{d.campaign.advertiser}</strong> <span className="muted">{d.campaign.brand_handle}</span>
                    </td>
                    <td>
                      <StateBadge state={d.state} />
                    </td>
                    <td>{usd(d.terms?.fixed_fee)}</td>
                    <td>{usd(d.terms?.cpm)}</td>
                    <td>{usd(d.terms?.cap)}</td>
                    <td className="muted">{when(d.terms?.publish_by)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        {deal ? (
          <DealDetail
            deal={deal}
            title={`${deal.campaign.advertiser} ${deal.campaign.brand_handle}`}
            actions={
              <>
                {deal.campaign.brief && <p className="brief">“{deal.campaign.brief}”</p>}
                <h3>Content requirements</h3>
                <Requirements
                  deliverable={deal.campaign.deliverable}
                  min={deal.campaign.min_duration_seconds}
                  max={deal.campaign.max_duration_seconds}
                  text={deal.campaign.requirements}
                  hash={deal.terms?.requirements_hash}
                />
                {NEEDS_PROOF.has(deal.state) && <ProofForm deal={deal} onDone={() => void deals.reload()} />}
              </>
            }
          />
        ) : (
          <Card title="Agent inbox (A2A)">
            <p className="muted small">Settlement notices your agent received from advertiser agents.</p>
            {!inbox.data?.length ? (
              <Empty>Nothing yet.</Empty>
            ) : (
              <ul className="inbox">
                {inbox.data.map((m, i) => (
                  <li key={i}>
                    <strong>{humanize(String(m.type))}</strong> · <span className="mono">{String(m.deal_id)}</span>
                    {m.amount_locked !== undefined && <> · {usd(String(m.amount_locked))} locked</>}
                    {m.amount !== undefined && (
                      <>
                        {" "}
                        · {humanize(String(m.kind))} {usd(String(m.amount))}
                      </>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )}
      </div>
    </div>
  );
}
