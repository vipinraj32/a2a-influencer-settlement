// How a creator reads their deals: the backend's DealState grouped into the stages they act on. Every state belongs to
// exactly one group, so the filter counts add up to the total. Nothing here decides anything; it only labels states.

import type { DealState, InfluencerDeal, Offer } from "./types";

export type DealGroup = "pending" | "negotiating" | "accepted" | "active" | "completed" | "rejected";

export const DEAL_GROUPS: { key: DealGroup; label: string; hint: string; states: DealState[] }[] = [
  { key: "negotiating", label: "Negotiating", hint: "Your agent and the brand's agent are exchanging offers", states: ["NEGOTIATING"] },
  {
    key: "pending",
    label: "Pending",
    hint: "Terms agreed or paused: waiting for the brand to approve and pay",
    states: ["ESCALATED", "AGREED", "CART_CREATED", "AWAITING_APPROVAL", "BLOCKED", "AWAITING_CHECKOUT", "SIGNED", "FUNDING", "PAYMENT_UNKNOWN"],
  },
  { key: "accepted", label: "Accepted", hint: "Paid into escrow: post your video", states: ["FUNDED", "PROOF_REJECTED"] },
  { key: "active", label: "Active", hint: "Video submitted: being verified and paid out", states: ["PROOF_SUBMITTED", "VERIFIED", "FIXED_RELEASED", "DISPUTED"] },
  { key: "completed", label: "Completed", hint: "All payouts done", states: ["COMPLETED"] },
  {
    key: "rejected",
    label: "Rejected",
    hint: "No deal, the payment did not go through, or the deadline passed",
    states: ["REJECTED", "PAYMENT_FAILED", "PAYMENT_EXPIRED", "EXPIRED_REFUNDED"],
  },
];

const GROUP_OF = new Map<DealState, DealGroup>(DEAL_GROUPS.flatMap((g) => g.states.map((s) => [s, g.key] as const)));

export const dealGroup = (state: DealState): DealGroup => GROUP_OF.get(state) ?? "pending";

/** States where the creator has to do something: post (or re-post) the video. */
export const NEEDS_PROOF = new Set<DealState>(["FUNDED", "PROOF_REJECTED"]);
/** States with the brand's money locked in escrow for this deal. */
export const IN_ESCROW = new Set<DealState>(["FUNDED", "PROOF_SUBMITTED", "PROOF_REJECTED", "VERIFIED", "FIXED_RELEASED", "DISPUTED"]);

/** The last offer on the table: the counter in the latest reply if there was one, else the latest offer. */
export function latestOffer(d: InfluencerDeal): Offer | null {
  const last = d.rounds.at(-1);
  if (!last) return null;
  return last.reply.counter ?? last.offer;
}

/** What this deal pays: the signed terms once agreed, otherwise the latest proposal. */
export function dealPay(d: InfluencerDeal): { fixed_fee: string; cpm: string; cap: string; model: string; final: boolean } | null {
  if (d.terms) return { fixed_fee: d.terms.fixed_fee, cpm: d.terms.cpm, cap: d.terms.cap, model: d.terms.compensation_model, final: true };
  const o = latestOffer(d);
  return o ? { fixed_fee: o.fixed_fee, cpm: o.cpm, cap: o.cap, model: o.compensation_model, final: false } : null;
}

export const paidOut = (d: InfluencerDeal) => d.payouts.filter((p) => p.kind !== "refund").reduce((s, p) => s + Number(p.amount), 0);

/** Search text: only what the creator is shown (brand, handle, niche, brief, format). */
export const dealSearchText = (d: InfluencerDeal) =>
  [d.campaign.advertiser, d.campaign.brand_handle, d.campaign.niche, d.campaign.brief, d.campaign.deliverable].join(" ").toLowerCase();
