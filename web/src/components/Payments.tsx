// Where a deal's money is, step by step, and the transactions that moved it. Every step is read from the deal record
// the backend keeps (funding evidence, verified proof, payouts recorded from receipts or escrow events); nothing here
// marks a payment done on its own. Simulated entries are labelled as such: no token moved for them.

import { humanize, usd, when } from "../format";
import { useSession } from "../session";
import type { Deal, Payout } from "../types";
import { Address } from "./ui";

const FUNDED_AT_LEAST = new Set(["FUNDED", "PROOF_SUBMITTED", "PROOF_REJECTED", "VERIFIED", "FIXED_RELEASED", "DISPUTED", "COMPLETED", "EXPIRED_REFUNDED"]);
const VERIFIED_AT_LEAST = new Set(["VERIFIED", "FIXED_RELEASED", "COMPLETED"]);

/** The explorer base for real transactions, or undefined when the escrow is the in-process simulation. */
export function useExplorer(): string | undefined {
  const { status } = useSession();
  return status?.escrow.simulated ? undefined : status?.chain.explorer_url;
}

export function TxLink({ hash, explorer, simulated }: { hash: string | null | undefined; explorer?: string; simulated?: boolean }) {
  if (!hash) return <span className="muted">—</span>;
  const label = `${hash.slice(0, 10)}…${hash.slice(-6)}`;
  return explorer && !simulated && /^0x[0-9a-fA-F]{64}$/.test(hash) ? (
    <a className="mono" href={`${explorer}/tx/${hash}`} target="_blank" rel="noreferrer" title={hash}>
      {label} ↗
    </a>
  ) : (
    <span className="mono" title={hash}>
      {label}
    </span>
  );
}

type Step = { label: string; done: boolean; detail?: string; bad?: boolean };

function steps(d: Deal): Step[] {
  const paid = (kind: Payout["kind"]) => d.payouts.find((p) => p.kind === kind);
  const fixed = paid("fixed");
  const perf = paid("performance");
  const sending = d.payout_intent && !paid(d.payout_intent.kind) ? d.payout_intent : null;
  const verified = VERIFIED_AT_LEAST.has(d.state) || !!fixed;
  const fixedDue = d.fixed_release_due ? new Date(d.fixed_release_due) : null;
  return [
    { label: "Escrow funded", done: !!d.funding_tx && FUNDED_AT_LEAST.has(d.state), detail: d.terms ? `${usd(d.terms.cap)} cap locked` : undefined },
    { label: "Proof submitted", done: !!d.proof, detail: d.proof ? when(d.proof.submitted_at) : undefined },
    d.state === "PROOF_REJECTED"
      ? { label: "Proof rejected", done: false, bad: true, detail: d.proof?.review?.resubmit_by ? `resubmit by ${when(d.proof.review.resubmit_by)}` : undefined }
      : { label: "Proof verified", done: verified },
    {
      label: fixed ? "Fixed fee paid" : fixedDue && fixedDue <= new Date() && verified ? "Fixed fee eligible" : "Fixed fee pending",
      done: !!fixed,
      detail: fixed
        ? usd(fixed.amount)
        : sending?.kind === "fixed"
          ? `sending ${usd(sending.amount)}${sending.error ? ` (retrying: ${sending.error})` : ""}`
          : d.fixed_release_due
            ? `due ${when(d.fixed_release_due)}`
            : undefined,
    },
    {
      label: perf ? "Performance fee paid" : "Performance fee pending",
      done: !!perf,
      detail: perf
        ? `${usd(perf.amount)}${perf.views != null ? ` for ${perf.views.toLocaleString()} views` : ""}`
        : sending?.kind === "performance"
          ? `sending ${usd(sending.amount)}`
          : d.performance_due
            ? `views measured ${when(d.performance_due)}`
            : undefined,
    },
  ];
}

/** The payment lifecycle as a checklist; the first unfinished step is the current one. */
export function PaymentLifecycle({ deal }: { deal: Deal }) {
  const list = steps(deal);
  const current = list.findIndex((s) => !s.done);
  return (
    <ol className="pay-steps">
      {list.map((s, i) => (
        <li key={s.label} className={s.done ? "done" : s.bad ? "bad current" : i === current ? "current" : ""}>
          <span className="pay-dot" aria-hidden>
            {s.done ? "✓" : s.bad ? "✗" : i + 1}
          </span>
          <span>
            {s.label}
            {s.detail && <span className="muted small"> · {s.detail}</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** Each payout with everything needed to check it independently on the block explorer. */
export function PayoutTransactions({ payouts, showDeal }: { payouts: (Payout & { deal_id?: string })[]; showDeal?: boolean }) {
  const { status } = useSession();
  const explorer = useExplorer();
  const coin = status?.token.coin ?? "USDC";
  const network = status?.chain.name ?? "";
  if (payouts.length === 0) return <p className="muted small">No payouts yet.</p>;
  return (
    <div className="table-scroll">
      <table className="table compact pay-tx">
        <thead>
          <tr>
            {showDeal && <th>Deal</th>}
            <th>Payout</th>
            <th>Amount</th>
            <th>Recipient</th>
            <th>Network</th>
            <th>Transaction</th>
            <th>Block</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {payouts.map((p) => (
            <tr key={`${p.deal_id ?? ""}${p.kind}${p.tx_hash}`}>
              {showDeal && <td className="mono small">{p.deal_id}</td>}
              <td>{p.kind === "refund" ? "Refund to brand" : `${humanize(p.kind)} fee`}</td>
              <td>
                {usd(p.amount)} <span className="muted small">{coin}</span>
              </td>
              <td>
                <Address value={p.to} explorer={p.simulated ? undefined : explorer} />
              </td>
              <td className="small">{p.simulated ? "none (simulated)" : network}</td>
              <td>
                <TxLink hash={p.tx_hash} explorer={explorer} simulated={p.simulated} />
              </td>
              <td className="mono small">{p.block_number ?? "—"}</td>
              <td>
                {p.simulated ? (
                  <span className="chip warn" title="In-process ledger entry: no token moved">
                    simulated
                  </span>
                ) : (
                  <span className="chip good" title={`Recorded from the escrow's receipt or event · ${when(p.at)}`}>
                    confirmed on-chain
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
