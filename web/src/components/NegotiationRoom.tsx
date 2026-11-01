import { api } from "../api";
import { MODEL_LABEL, humanize, num, termsText, usd, when } from "../format";
import { useLiveEvents, useResource } from "../live";
import type { Deal } from "../types";
import { Empty } from "./ui";

// "creator declined: Declined: no agreement…" → "No agreement within the round limit."
function plainOutcome(outcome: string): string {
  const text = outcome.replace(/^[a-z' ]+:\s*/i, "").replace(/^(declined|accepted|counter-offer):\s*/i, "");
  return text ? text[0].toUpperCase() + text.slice(1) : outcome;
}

// The creator agent's note only when it says more than the terms already shown
const restatesTerms = (text: string) => /^(counter-offer|offer|accepted)[.:]/i.test(text.trim()) && /\$\d/.test(text);

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
          {s.exclusivity_days ? ` · exclusivity ${s.exclusivity_days}d ${s.exclusivity_category}` : ""} · {s.revisions} revision{s.revisions === 1 ? "" : "s"}
        </span>
      </div>
      {v.outcome && <p className="nr-outcome">{plainOutcome(v.outcome)}</p>}

      {v.rounds.length === 0 ? (
        <Empty>No offers yet.</Empty>
      ) : (
        <table className="table compact">
          <thead>
            <tr>
              <th>Round</th>
              <th>Your offer</th>
              <th>Creator's answer</th>
            </tr>
          </thead>
          <tbody>
            {v.rounds.map((r) => (
              <tr key={r.round}>
                <td>{r.round}</td>
                <td>{r.advertiser_offer && termsText(r.advertiser_offer, model)}</td>
                <td>
                  <strong className={`reply-${r.reply}`}>{humanize(r.reply)}</strong>
                  {r.counter && <> · {termsText(r.counter, model)}</>}
                  {r.explanation && !restatesTerms(r.explanation) && !r.counter && <div className="small muted">{plainOutcome(r.explanation)}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {v.payable && (v.status === "agreed" || v.status === "open") && (
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
          <p className="small muted" title="Eligible views are YouTube's count when the performance fee is due, for a post that is still public and still passes its checks.">
            {v.payable.views_to_cap ? `Reaches the cap at ${num(v.payable.views_to_cap)} views.` : "Views counted when the performance fee is due."}
          </p>
        </>
      )}

      {decisions.data && decisions.data.length > 0 && (
        <details className="nr-decisions">
          <summary>Why your agent decided this · only you see this</summary>
          <ol className="decisions">
            {decisions.data.map((d) => {
              const failed = d.hard_checks.filter((c) => !c.passed);
              return (
                <li key={d.id}>
                  <div>
                    <strong>Round {d.round}</strong> · {humanize(d.action)}
                    <span className="muted small"> · {when(d.at)}</span>
                  </div>
                  {failed.length > 0 && <div className="small muted">Their ask broke your limits: {failed.map((c) => c.detail).join("; ")}</div>}
                  {d.llm_overridden.length > 0 && <div className="small muted">AI suggestion overridden: {d.llm_overridden.join("; ")}</div>}
                </li>
              );
            })}
          </ol>
        </details>
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
