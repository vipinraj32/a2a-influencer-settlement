import { useState } from "react";
import { api } from "../api";
import { CreatorLink } from "./CreatorLink";
import { humanize, when } from "../format";
import { useLiveEvents, useResource } from "../live";
import { ActionButton, Card, Empty, StateBadge } from "./ui";

const COUNTRY: Record<string, string> = { IN: "India", US: "United States", GB: "United Kingdom", EU: "European Union" };

/** Campaign Compliance & Audit: disclosure policy versions in force and every deal's gates. */
export function ComplianceCard({ campaignId, names }: { campaignId: string; names: Map<string, string> }) {
  const view = useResource(() => api.compliance(campaignId), `compliance:${campaignId}`);
  useLiveEvents((e) => e.campaign_id === campaignId, view.reload);
  const v = view.data;
  if (!v) return null;
  return (
    <Card title="Compliance" actions={v.settlement_simulated ? <span className="chip warn" title="Escrow funding and payouts on this server are in-process ledger entries. No USDC moves.">Simulated</span> : undefined}>
      <ul className="compliance-summary">
        <li>
          <span>Posts must show</span>
          <strong>{v.required_tags.join(" ")}</strong>
        </li>
        <li>
          <span>Disclosure rules</span>
          <strong>{v.policies.map((p) => COUNTRY[p.id.split("@")[0]] ?? p.id.split("@")[0]).join(", ") || "none"}</strong>
        </li>
        <li>
          <span>Your review</span>
          <strong>{v.review_required ? "Required before the fixed fee" : "Not required"}</strong>
        </li>
      </ul>
      <details className="policies">
        <summary>Rule details</summary>
        {v.policies.map((p) => (
          <div key={p.id} className="small">
            <strong>
              {COUNTRY[p.id.split("@")[0]] ?? p.id} <span className="muted">({p.id})</span>
            </strong>
            : accepted labels {p.approved_labels.join(", ")}; within the first {p.placement_chars} characters
            {p.in_video_required ? "; also said in the video" : ""}.
            {p.discouraged_labels.length > 0 && <span className="muted"> Never counts: {p.discouraged_labels.join(", ")}.</span>}
          </div>
        ))}
        <p className="small muted">{v.disclaimer}</p>
      </details>
      {v.deals.length === 0 ? (
        <Empty>No agreed deals yet.</Empty>
      ) : (
        <table className="table compact">
          <thead>
            <tr>
              <th>Creator</th>
              <th>State</th>
              <th>Disclosure checks</th>
              <th>Review</th>
              <th>Approval</th>
              <th>Gates</th>
            </tr>
          </thead>
          <tbody>
            {v.deals.map((d) => (
              <tr key={d.deal_id}>
                <td>
                  <CreatorLink id={d.influencer_id}>{names.get(d.influencer_id) ?? d.influencer_id}</CreatorLink>
                </td>
                <td>
                  <StateBadge state={d.state} />
                </td>
                <td className="small">
                  {d.checks.length === 0
                    ? "—"
                    : d.checks.map((c) => (
                        <div key={c.name} className={c.passed ? "" : "error"} title={c.detail}>
                          {c.passed ? "✓" : "✗"} {humanize(c.name)}
                        </div>
                      ))}
                </td>
                <td>{humanize(d.review)}</td>
                <td className="small">
                  {d.payment_approval ? `${d.payment_approval.approved ? "Approved" : "Declined"} ${when(d.payment_approval.at)}` : "—"}
                  {!d.terms_hash_matches_approval && <div className="error">terms changed after approval</div>}
                </td>
                <td className="small">{d.gates.length ? d.gates.join("; ") : "clear"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

/** Advertiser's disclosure review of a verified post (campaigns with review_required). */
export function ReviewForm({ dealId, onDone }: { dealId: string; onDone: () => void }) {
  const [note, setNote] = useState("");
  return (
    <div className="row">
      <input className="grow" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Review note (optional)" maxLength={500} />
      <ActionButton
        onClick={async () => {
          await api.review(dealId, true, note);
          onDone();
        }}
      >
        Disclosure OK
      </ActionButton>
      <ActionButton
        variant="danger"
        onClick={async () => {
          await api.review(dealId, false, note);
          onDone();
        }}
      >
        Send back
      </ActionButton>
    </div>
  );
}
