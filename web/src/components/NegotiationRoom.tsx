import { api } from "../api";
import { MODEL_LABEL, humanize, num, termsText, usd, when } from "../format";
import { useLiveEvents, useResource } from "../live";
import type { Deal } from "../types";
import { Empty } from "./ui";

const signed = (v: string) => (Number(v) > 0 ? `+${usd(v)}` : Number(v) < 0 ? `−${usd(String(-Number(v)))}` : "±0");

/** Structured offers, what moved each round, the payable breakdown and this party's own decision records. */
export function NegotiationRoom({ deal }: { deal: Deal }) {
  const view = useResource(() => api.negotiation(deal.id), deal.id);
  const decisions = useResource(() => api.decisions(deal.id), deal.id);
  useLiveEvents(
    (e) => e.deal_id === deal.id,
    () => {
      view.reload();
      decisions.reload();
    },
  );
  const v = view.data;
  if (!v) return <Empty>Loading negotiation…</Empty>;
  const model = v.compensation_model;
  const s = v.scope;

  return (
    <div className="negotiation">
      <div className="row spread">
        <span>
          <span className={`chip ${v.status === "agreed" ? "good" : v.status === "open" ? "" : "warn"}`}>{humanize(v.status)}</span> {MODEL_LABEL[model]} · round{" "}
          {v.rounds_used} of {v.max_rounds}
          {v.status === "open" && ` · ${v.rounds_remaining} left`}
        </span>
        <span className="small muted">
          {humanize(s.usage_rights)}
          {s.license_days ? ` · license ${s.license_days}d` : ""}
          {s.paid_amplification ? " · paid amplification" : ""}
          {s.exclusivity_days ? ` · exclusivity ${s.exclusivity_days}d ${s.exclusivity_category}` : ""} · {s.revisions} revision(s)
        </span>
      </div>
      {v.outcome && <p className="small">Outcome: {v.outcome}</p>}

      {v.rounds.length === 0 ? (
        <Empty>No offers yet.</Empty>
      ) : (
        <table className="table compact">
          <thead>
            <tr>
              <th>Round</th>
              <th>Advertiser offer</th>
              <th>Creator agent</th>
              <th>Explanation</th>
            </tr>
          </thead>
          <tbody>
            {v.rounds.map((r) => (
              <tr key={r.round}>
                <td>{r.round}</td>
                <td>
                  {r.advertiser_offer && termsText(r.advertiser_offer, model)}
                  {r.advertiser_moved && <div className="small muted">fixed {signed(r.advertiser_moved.fixed_fee)} · CPM {signed(r.advertiser_moved.cpm)}</div>}
                </td>
                <td>
                  <strong className={`reply-${r.reply}`}>{humanize(r.reply)}</strong>
                  {r.counter && <div>→ {termsText(r.counter, model)}</div>}
                  {r.creator_moved && <div className="small muted">fixed {signed(r.creator_moved.fixed_fee)} · CPM {signed(r.creator_moved.cpm)}</div>}
                </td>
                <td className="small">{r.explanation}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {v.payable && (
        <>
          <h4>What it pays</h4>
          <table className="table compact">
            <thead>
              <tr>
                <th>Eligible views</th>
                <th>Fixed</th>
                <th>Performance</th>
                <th>Total</th>
              </tr>
            </thead>
            <tbody>
              {v.payable.examples.map((e) => (
                <tr key={e.eligible_views}>
                  <td>{num(e.eligible_views)}</td>
                  <td>{usd(e.fixed)}</td>
                  <td>{usd(e.performance)}</td>
                  <td>
                    {usd(e.total)}
                    {e.capped && <span className="chip">capped (uncapped {usd(e.uncapped_total)})</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="small muted">
            {v.payable.views_to_cap ? `The cap is reached at ${num(v.payable.views_to_cap)} views. ` : ""}Eligible views are YouTube's count when the
            performance fee is due, for a post that is still public and still passes its checks. Rules {v.payable.examples[0]?.version}.
          </p>
        </>
      )}

      {decisions.data && decisions.data.length > 0 && (
        <>
          <h4>Your agent's decisions (private to you)</h4>
          <ol className="decisions">
            {decisions.data.map((d) => {
              const failed = d.hard_checks.filter((c) => !c.passed);
              return (
                <li key={d.id}>
                  <div>
                    <strong>Round {d.round}</strong> · <span className="chip">{humanize(d.action)}</span> by {d.decided_by}
                    {d.score !== null && <> · score {d.score.toFixed(2)}</>} · goal {humanize(d.objective)} · policy v{d.policy_version}
                  </div>
                  <div className="small">{d.private_rationale}</div>
                  {failed.length > 0 && (
                    <div className="small error">
                      Failed: {failed.map((c) => `${humanize(c.name)} (${c.detail})`).join("; ")}
                    </div>
                  )}
                  {d.preferences_applied.length > 0 && <div className="small muted">Applied: {d.preferences_applied.join("; ")}</div>}
                  {d.llm_overridden.length > 0 && <div className="small muted">AI suggestion overridden: {d.llm_overridden.join("; ")}</div>}
                  <div className="small muted">
                    The other side saw: “{d.public_explanation}” · {when(d.at)}
                  </div>
                </li>
              );
            })}
          </ol>
        </>
      )}

      {v.agreement && (
        <p className="small muted">
          Agreement hash <span className="mono">{v.agreement.terms_hash.slice(0, 14)}…</span>
          {v.agreement.approved_terms_hash && (v.agreement.approved_terms_hash === v.agreement.terms_hash ? " · payment drafted for these exact terms" : " · terms differ from the approved hash!")}
          {v.agreement.approvals.map((a) => (
            <span key={a.at + a.kind}>
              {" "}
              · {humanize(a.kind)} {a.approved ? "approved" : "declined"} {when(a.at)}
            </span>
          ))}
        </p>
      )}
    </div>
  );
}
