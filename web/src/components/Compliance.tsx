import { useState } from "react";
import { api } from "../api";
import { humanize, when } from "../format";
import { useLiveEvents, useResource } from "../live";
import { ActionButton, Card, Empty, Notice, StateBadge } from "./ui";

/** Campaign Compliance & Audit: disclosure policy versions in force and every deal's gates. */
export function ComplianceCard({ campaignId, names }: { campaignId: string; names: Map<string, string> }) {
  const view = useResource(() => api.compliance(campaignId), `compliance:${campaignId}`);
  useLiveEvents((e) => e.campaign_id === campaignId, view.reload);
  const v = view.data;
  if (!v) return null;
  return (
    <Card title="Compliance and audit">
      {v.settlement_simulated && <Notice tone="warn">Simulated settlement: escrow funding and payouts on this server are in-process ledger entries. No USDC moves.</Notice>}
      <div className="policies">
        {v.policies.map((p) => (
          <details key={p.id}>
            <summary>
              <strong>{p.id}</strong> · labels {p.approved_labels.join(", ")} · first {p.placement_chars} chars
              {p.in_video_required ? " · in-video attestation" : ""}
            </summary>
            <ul className="bullets small">
              {p.sources.map((s) => (
                <li key={s}>{s}</li>
              ))}
              {p.notes && <li>{p.notes}</li>}
              {p.livestream_rule && <li>Livestreams: {p.livestream_rule}</li>}
              {p.discouraged_labels.length > 0 && <li>Never counts: {p.discouraged_labels.join(", ")}</li>}
            </ul>
          </details>
        ))}
      </div>
      <p className="small muted">
        Required tags {v.required_tags.join(" ")} · {v.review_required ? "your review gates the fixed fee" : "no manual review"}. {v.disclaimer}
      </p>
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
                <td>{names.get(d.influencer_id) ?? d.influencer_id}</td>
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
