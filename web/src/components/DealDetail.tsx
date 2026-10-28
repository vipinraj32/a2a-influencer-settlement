import type { ReactNode } from "react";
import { api } from "../api";
import { humanize, num, usd, when } from "../format";
import { useLiveEvents, useResource } from "../live";
import { useSession } from "../session";
import type { Deal } from "../types";
import { Timeline } from "./Timeline";
import { Address, Card, Empty, StateBadge } from "./ui";

/** Everything about one deal, shared by the advertiser and influencer views. `actions` renders role-specific buttons. */
export function DealDetail({ deal, title, actions }: { deal: Deal; title: ReactNode; actions?: ReactNode }) {
  const { status } = useSession();
  const explorer = status?.escrow.simulated ? undefined : status?.chain.explorer_url;
  const events = useResource(() => api.dealEvents(deal.id), deal.id);
  useLiveEvents((e) => e.deal_id === deal.id, events.reload);
  const t = deal.terms;
  const paid = deal.payouts.filter((p) => p.kind !== "refund").reduce((s, p) => s + Number(p.amount), 0);

  return (
    <Card title={title} actions={<StateBadge state={deal.state} />} className="deal-detail">
      {actions && <div className="deal-actions">{actions}</div>}

      {deal.policy_violations.length > 0 && (
        <div className="notice tone-bad">
          <strong>Policy blocked this payment:</strong>
          <ul>
            {deal.policy_violations.map((v) => (
              <li key={v}>{v}</li>
            ))}
          </ul>
        </div>
      )}

      <h3>Negotiation over A2A</h3>
      {deal.rounds.length === 0 ? (
        <Empty>No rounds yet.</Empty>
      ) : (
        <ol className="rounds">
          {deal.rounds.map((r) => (
            <li key={r.offer.round}>
              <div>
                <strong>Round {r.offer.round}</strong> · advertiser offered {usd(r.offer.fixed_fee)} fixed + {usd(r.offer.cpm)} CPM (cap {usd(r.offer.cap)})
              </div>
              <div className={`reply reply-${r.reply.kind}`}>
                Influencer agent <strong>{r.reply.kind}</strong>
                {r.reply.counter && r.reply.kind === "counter" && (
                  <>
                    {" "}
                    → {usd(r.reply.counter.fixed_fee)} + {usd(r.reply.counter.cpm)} CPM
                  </>
                )}
                {r.reply.reason && <span className="muted"> “{r.reply.reason}”</span>}
                {r.reply.decided_by && <span className="chip">{r.reply.decided_by}</span>}
              </div>
            </li>
          ))}
        </ol>
      )}

      {t && (
        <>
          <h3>Signed terms</h3>
          <dl className="kv">
            <dt>Fixed fee</dt>
            <dd>{usd(t.fixed_fee)}</dd>
            <dt>Per 1,000 views</dt>
            <dd>{usd(t.cpm)}</dd>
            <dt>Escrow cap</dt>
            <dd>{usd(t.cap)}</dd>
            <dt>Publish by</dt>
            <dd>{when(t.publish_by)}</dd>
            <dt>Must stay live</dt>
            <dd>{t.min_live_hours} h</dd>
            <dt>Required tags</dt>
            <dd>{t.required_tags.join(" ")}</dd>
            <dt>Payee</dt>
            <dd>
              <Address value={t.payee} explorer={explorer} />
            </dd>
            <dt>Channel</dt>
            <dd className="mono">{t.channel_id}</dd>
          </dl>
        </>
      )}

      {(deal.payment_status || deal.payer) && (
        <>
          <h3>Payment (HSP)</h3>
          <dl className="kv">
            <dt>Payer wallet</dt>
            <dd>
              <Address value={deal.payer} explorer={explorer} />
            </dd>
            <dt>Status</dt>
            <dd>{deal.payment_status ?? "not paid yet"}</dd>
            <dt>Request</dt>
            <dd className="mono">{deal.payment_request_id ?? "—"}</dd>
            {deal.funding_tx && (
              <>
                <dt>Funding tx</dt>
                <dd className="mono">{deal.funding_tx.slice(0, 18)}…</dd>
              </>
            )}
          </dl>
        </>
      )}

      {deal.proof && (
        <>
          <h3>
            Proof: <a href={`https://youtube.com/watch?v=${deal.proof.video_id}`} target="_blank" rel="noreferrer">{deal.proof.video_id}</a>
          </h3>
          <ul className="checks">
            {deal.proof.checks.map((c) => (
              <li key={c.name} className={c.passed ? "pass" : "fail"}>
                <span aria-hidden>{c.passed ? "✓" : "✗"}</span> {humanize(c.name)}
                {c.detail && <span className="muted"> — {c.detail}</span>}
              </li>
            ))}
          </ul>
          <p className="muted small">
            Views at check: {num(deal.proof.views)} · fixed fee due {when(deal.fixed_release_due)} · performance due {when(deal.performance_due)}
          </p>
        </>
      )}

      {deal.payouts.length > 0 && (
        <>
          <h3>Payouts · {usd(paid)} to creator</h3>
          <table className="table compact">
            <tbody>
              {deal.payouts.map((p, i) => (
                <tr key={i}>
                  <td>{humanize(p.kind)}</td>
                  <td>{usd(p.amount)}</td>
                  <td>
                    <Address value={p.to} explorer={explorer} />
                  </td>
                  <td className="muted">{when(p.at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h3>Timeline</h3>
      {events.data ? <Timeline events={events.data} /> : <Empty>Loading…</Empty>}
    </Card>
  );
}
