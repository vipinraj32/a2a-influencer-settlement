// One deal, as the creator sees it: what the brand wants, what it pays, where it stands, and the one action the
// creator can take (submit the video once the escrow is funded). Offers are made and answered by the creator's agent
// inside the limits set on the My agent page, so there is no manual accept, reject or counter here.

import { Link, useParams } from "react-router";
import { api } from "../../api";
import { Breadcrumbs } from "../../components/Breadcrumbs";
import { DealDetail } from "../../components/DealDetail";
import { LiveNegotiation } from "../../components/LiveNegotiation";
import { PaymentLifecycle, PayoutTransactions } from "../../components/Payments";
import { ProofForm } from "../../components/ProofForm";
import { RejectionDialog } from "../../components/RejectionDialog";
import { Requirements } from "../../components/Requirements";
import { Card, Notice, Spinner, StateBadge } from "../../components/ui";
import { DEAL_GROUPS, IN_ESCROW, NEEDS_PROOF, dealGroup, latestOffer, paidOut } from "../../creatorDeals";
import { DELIVERABLE_LABEL, termsText, usd, videoLength, when } from "../../format";
import { useLiveEvents, useResource } from "../../live";
import { useMe } from "../../session";
import type { DealState, InfluencerDeal } from "../../types";
import "./deals.css";

/** What happens next, in the creator's words, for states where they wait on someone else. */
const NEXT: Partial<Record<DealState, string>> = {
  NEGOTIATING: "Your agent is negotiating with the brand's agent right now. Follow the conversation below.",
  ESCALATED: "An agent's policy handed this decision to a person. Nothing was agreed and no money is reserved.",
  AGREED: "Your agent signed these terms. The brand now approves and pays them into escrow.",
  CART_CREATED: "Terms agreed. The brand's payment is being prepared.",
  AWAITING_APPROVAL: "Terms agreed. Waiting for the brand to sign the payment.",
  BLOCKED: "The brand's payment policy refused this payment, so nothing was paid.",
  AWAITING_CHECKOUT: "Terms agreed. Waiting for the brand to pay at checkout.",
  SIGNED: "The brand signed the payment. Waiting for it to settle on-chain.",
  FUNDING: "The payment is settling. Your video window starts once it is confirmed.",
  PAYMENT_UNKNOWN: "The payment outcome is not confirmed yet. It is being checked; do not post until the deal shows Funded.",
  PROOF_SUBMITTED: "Your video is being checked against the signed terms.",
  VERIFIED: "Your video passed. The fixed fee is being released, or waits for the brand's review if the campaign requires one.",
  FIXED_RELEASED: "Fixed fee paid. The performance fee is paid at day 7, based on views.",
  DISPUTED: "The brand disputed this deal. Funds stay in escrow until it is resolved.",
  COMPLETED: "All payouts are done.",
  REJECTED: "No deal was made.",
  PAYMENT_FAILED: "The brand's payment did not go through. Nothing is locked for this deal.",
  PAYMENT_EXPIRED: "The brand's payment authorization expired before it was used.",
  EXPIRED_REFUNDED: "No accepted video by the deadline, so the escrow was refunded to the brand.",
};

export function InfluencerDealPage() {
  const { id = "" } = useParams();
  const me = useMe().user;
  const deals = useResource(api.influencerDeals, "my-deals");
  useLiveEvents((e) => e.deal_id === id, deals.reload);
  const deal = deals.data?.find((d) => d.id === id) ?? null;
  const crumbs = [
    { label: "Overview", to: "/influencer" },
    { label: "My deals", to: "/influencer/deals" },
  ];

  if (!deals.data) {
    return (
      <div className="stack">
        <Breadcrumbs items={[...crumbs, { label: "Deal" }]} />
        {deals.error ? (
          <div className="md-state card" role="alert">
            <strong>Could not load this deal</strong>
            <span className="muted">{deals.error}</span>
            <button type="button" className="btn btn-secondary" onClick={() => void deals.reload()}>
              Retry
            </button>
          </div>
        ) : (
          <Spinner />
        )}
      </div>
    );
  }
  if (!deal) {
    return (
      <div className="stack">
        <Breadcrumbs items={[...crumbs, { label: "Not found" }]} />
        <div className="md-state card">
          <strong>Deal not found</strong>
          <span className="muted">It is not one of your deals, or the link is wrong.</span>
          <Link to="/influencer/deals" className="btn btn-secondary">
            Back to My deals
          </Link>
        </div>
      </div>
    );
  }

  const c = deal.campaign;
  const group = DEAL_GROUPS.find((g) => g.key === dealGroup(deal.state))!;
  const review = deal.state === "PROOF_REJECTED" ? deal.proof?.review : null;
  const toProofForm = () => {
    const form = document.getElementById("md-next");
    form?.scrollIntoView({ behavior: "smooth", block: "start" });
    form?.querySelector<HTMLInputElement>("input.grow")?.focus({ preventScroll: true });
  };
  return (
    <div className="stack md-detail">
      <Breadcrumbs items={[...crumbs, { label: c.advertiser }]} />
      <header className="md-detail-head">
        <div>
          <h1>{c.advertiser}</h1>
          <p className="muted">
            {c.brand_handle} · {c.niche} · {DELIVERABLE_LABEL[c.deliverable] ?? c.deliverable}
          </p>
        </div>
        <div className="md-status">
          <span className="chip">{group.label}</span>
          <StateBadge state={deal.state} />
        </div>
      </header>
      {review && <RejectionDialog dealId={deal.id} review={review} onFix={toProofForm} />}

      <div className="md-split">
        <div className="md-main stack">
          {NEEDS_PROOF.has(deal.state) ? (
            <Card title={review ? "Fix and resubmit" : "Your turn: post the video"} className="md-next md-next-turn" id="md-next">
              {review && (
                <p className="md-why">
                  <strong>Rejected:</strong> {review.issues.map((i) => i.problem).join(" ") || review.summary}
                </p>
              )}
              <ProofForm key={deal.proof?.submitted_at ?? deal.id} deal={deal} onDone={() => void deals.reload()} />
            </Card>
          ) : (
            <Notice tone={group.key === "rejected" ? "warn" : group.key === "completed" ? "good" : "info"}>{NEXT[deal.state] ?? group.hint}</Notice>
          )}

          <Pay deal={deal} />

          {deal.funding_tx && (
            <Card title="Payment">
              <PaymentLifecycle deal={deal} />
              {deal.payouts.length > 0 && <PayoutTransactions payouts={deal.payouts} />}
            </Card>
          )}

          <details className="md-more card">
            <summary>Campaign and requirements</summary>
            {c.brief && <p className="brief">“{c.brief}”</p>}
            <dl className="kv">
              <dt>Deliverable</dt>
              <dd>
                {DELIVERABLE_LABEL[c.deliverable] ?? c.deliverable}, {videoLength(c.min_duration_seconds, c.max_duration_seconds).toLowerCase()}
              </dd>
              <dt>Required tags</dt>
              <dd>{(deal.terms?.required_tags ?? c.required_tags).join(" ") || "—"}</dd>
            </dl>
            <Requirements
              deliverable={c.deliverable}
              min={c.min_duration_seconds}
              max={c.max_duration_seconds}
              text={c.requirements}
              hash={deal.terms?.requirements_hash}
            />
          </details>

          <details className="md-more">
            <summary>Full deal record: terms, payment, proof checks</summary>
            <DealDetail deal={deal} title="Deal record" conversation={false} />
          </details>
        </div>

        <aside className="md-side">
          <Card title="Conversation">
            <LiveNegotiation key={deal.id} deal={deal} viewer="influencer" creator={me.name} avatar={me.picture} brand={deal.terms?.advertiser ?? c.advertiser} />
            {deal.state === "NEGOTIATING" && (
              <p className="small muted">
                Your agent decides within the limits on <Link to="/influencer/agent">My agent</Link>.
              </p>
            )}
          </Card>
        </aside>
      </div>
    </div>
  );
}

function paymentStatus(d: InfluencerDeal): string {
  const sim = d.funding_simulated ? " (simulated, no USDC moved)" : "";
  if (d.state === "COMPLETED") return `Paid out${sim}`;
  if (d.state === "EXPIRED_REFUNDED") return `Refunded to the brand${sim}`;
  if (IN_ESCROW.has(d.state)) return `Locked in escrow${sim}`;
  return d.payment_status ?? "Not paid yet";
}

/** Proposed vs agreed pay, what has been paid, and the dates that matter. */
function Pay({ deal }: { deal: InfluencerDeal }) {
  const offer = latestOffer(deal);
  const t = deal.terms;
  const paid = paidOut(deal);
  return (
    <Card title="Pay">
      <dl className="kv">
        {offer && !t && (
          <>
            <dt>Latest offer</dt>
            <dd>
              {termsText(offer, offer.compensation_model)}{" "}
              <span className="muted small">
                round {offer.round}/{offer.max_rounds}, from {offer.proposer === "influencer" ? "your agent" : "the brand's agent"}
              </span>
            </dd>
          </>
        )}
        {t && (
          <>
            <dt>Agreed terms</dt>
            <dd>
              <strong>{termsText(t, t.compensation_model)}</strong>
            </dd>
          </>
        )}
        {!offer && !t && (
          <>
            <dt>Offer</dt>
            <dd className="muted">No offer yet</dd>
          </>
        )}
        <dt>Paid to you</dt>
        <dd>{usd(paid)}</dd>
        <dt>Payment status</dt>
        <dd>{paymentStatus(deal)}</dd>
        <dt>Publish by</dt>
        <dd>{t ? when(t.publish_by) : offer ? `${offer.publish_within_days} days after payment` : "—"}</dd>
        {deal.fixed_release_due && (
          <>
            <dt>Fixed fee due</dt>
            <dd>{when(deal.fixed_release_due)}</dd>
          </>
        )}
        {deal.performance_due && (
          <>
            <dt>Performance fee due</dt>
            <dd>{when(deal.performance_due)}</dd>
          </>
        )}
      </dl>
    </Card>
  );
}
