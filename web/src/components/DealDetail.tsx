import type { ReactNode } from "react";
import { api } from "../api";
import { MODEL_LABEL, humanize, num, usd, videoLength, when } from "../format";
import { useLiveEvents, useResource } from "../live";
import { useMe, useSession } from "../session";
import type { Deal } from "../types";
import { ContentReportView } from "./ContentChecks";
import { LiveNegotiation } from "./LiveNegotiation";
import { NegotiationRoom } from "./NegotiationRoom";
import { ProofHistory, ProofReviewCard } from "./ProofReview";
import { Timeline } from "./Timeline";
import { Address, Card, Empty, StateBadge } from "./ui";

/** Everything about one deal, shared by the advertiser and influencer views. `actions` renders role-specific buttons. */
export function DealDetail({ deal, title, actions }: { deal: Deal; title: ReactNode; actions?: ReactNode }) {
  const me = useMe().user;
  const role = me.role;
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

      {role === "influencer" && (
        <>
          <h3>Conversation with the advertiser's agent</h3>
          <LiveNegotiation key={deal.id} deal={deal} viewer="influencer" creator={me.name} avatar={me.picture} brand={deal.terms?.advertiser ?? "Advertiser"} />
        </>
      )}

      <h3>Negotiation room</h3>
      <NegotiationRoom deal={deal} />

      {t && (
        <>
          <h3>Signed terms</h3>
          <dl className="kv">
            <dt>Model</dt>
            <dd>
              {MODEL_LABEL[t.compensation_model] ?? t.compensation_model} <span className="muted small">({t.pricing_version})</span>
            </dd>
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
            <dt>Disclosure rules</dt>
            <dd>{t.disclosure_policies.length ? t.disclosure_policies.join(", ") : "—"}</dd>
            <dt>Rights</dt>
            <dd>
              {humanize(t.scope.usage_rights)}
              {t.scope.license_days ? `, ${t.scope.license_days}-day license` : ""}
              {t.scope.paid_amplification ? ", paid amplification" : ""}
              {t.scope.exclusivity_days ? `, ${t.scope.exclusivity_days}-day exclusivity` : ""}
            </dd>
            <dt>Video length</dt>
            <dd>{videoLength(t.min_duration_seconds, t.max_duration_seconds)}</dd>
            <dt>Requirements hash</dt>
            <dd className="mono" title={t.requirements_hash}>
              {t.requirements_hash ? `${t.requirements_hash.slice(0, 10)}…${t.requirements_hash.slice(-6)}` : "none"}
            </dd>
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
                <dd className="mono">
                  {explorer && !deal.funding_simulated ? (
                    <a href={`${explorer}/tx/${deal.funding_tx}`} target="_blank" rel="noreferrer">
                      {deal.funding_tx.slice(0, 18)}…
                    </a>
                  ) : (
                    `${deal.funding_tx.slice(0, 18)}…`
                  )}
                  {deal.funding_simulated && <span className="chip warn">simulated, no USDC moved</span>}
                </dd>
              </>
            )}
            {deal.payment_attempts.length > 0 && (
              <>
                <dt>Attempts</dt>
                <dd>
                  {deal.payment_attempts.map((a) => (
                    <div key={a.idempotency_key} className="small">
                      {humanize(a.status)} via {a.gateway}
                      {a.simulated ? " (simulated)" : ""} · {when(a.updated_at)}
                      {a.error && <span className="muted"> — {a.error}</span>}
                    </div>
                  ))}
                </dd>
              </>
            )}
          </dl>
        </>
      )}

      {deal.proof && (
        <>
          <h3>
            Proof: <a href={`https://youtube.com/watch?v=${deal.proof.video_id}`} target="_blank" rel="noreferrer">{deal.proof.video_id}</a>
            {deal.proof.attempt > 1 && <span className="muted small"> · submission #{deal.proof.attempt}</span>}
          </h3>
          {deal.proof.review && deal.state === "PROOF_REJECTED" && <ProofReviewCard review={deal.proof.review} viewer={role === "advertiser" ? "advertiser" : "creator"} />}
          <ul className="checks">
            {deal.proof.checks.map((c) => (
              <li key={c.name} className={c.passed ? "pass" : "fail"}>
                <span aria-hidden>{c.passed ? "✓" : "✗"}</span> {humanize(c.name)}
                {c.detail && <span className="muted"> — {c.detail}</span>}
              </li>
            ))}
          </ul>
          {deal.proof.content && <ContentReportView report={deal.proof.content} />}
          {Object.keys(deal.proof.attestations ?? {}).length > 0 && (
            <p className="small muted">Creator attested: {Object.keys(deal.proof.attestations).map(humanize).join(", ")} (statements, not machine-verified)</p>
          )}
          <p className="muted small">
            Views at check: {num(deal.proof.views)} · fixed fee due {when(deal.fixed_release_due)} · performance due {when(deal.performance_due)}
          </p>
          <ProofHistory history={deal.proof_history ?? []} />
        </>
      )}

      {deal.payouts.length > 0 && (
        <>
          <h3>Payouts · {usd(paid)} to creator</h3>
          <table className="table compact">
            <tbody>
              {deal.payouts.map((p, i) => (
                <tr key={i}>
                  <td>
                    {humanize(p.kind)}
                    {p.simulated && <span className="chip warn">simulated</span>}
                  </td>
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
