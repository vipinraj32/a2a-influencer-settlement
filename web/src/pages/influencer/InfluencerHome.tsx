import { Link } from "react-router";
import { api } from "../../api";
import { Avatar, Card, Empty, ErrorText, Notice, Spinner, Stat } from "../../components/ui";
import { IN_ESCROW, NEEDS_PROOF } from "../../creatorDeals";
import { humanize, num, usd } from "../../format";
import { useLiveEvents, useResource } from "../../live";
import { useMe } from "../../session";
import { DealCard } from "./MyDeals";
import "../advertiser/dashboard.css";
import "./deals.css";

export function InfluencerHome() {
  const me = useMe();
  const user = me.user;
  const deals = useResource(api.influencerDeals, "my-deals");
  const inbox = useResource(api.influencerInbox, "inbox");
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

      {todo.length > 0 && (
        <Notice tone="warn">
          {todo.length === 1 ? "1 deal is" : `${todo.length} deals are`} funded and waiting for your video.{" "}
          <Link to="/influencer/deals?status=accepted">Open them</Link>
        </Notice>
      )}

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

      <div className="dash-grid">
        <Card
          title="Recent deals"
          actions={
            list.length > 0 && (
              <Link to="/influencer/deals" className="btn btn-ghost btn-sm">
                View all
              </Link>
            )
          }
        >
          <ErrorText error={deals.error} />
          {deals.loading && !deals.data ? (
            <Spinner />
          ) : list.length === 0 ? (
            <Empty>No deals yet. When an advertiser's agent contacts yours, the negotiation appears here live.</Empty>
          ) : (
            <ul className="md-list">
              {list.slice(0, 4).map((d) => (
                <DealCard key={d.id} deal={d} />
              ))}
            </ul>
          )}
        </Card>

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
      </div>
    </div>
  );
}
