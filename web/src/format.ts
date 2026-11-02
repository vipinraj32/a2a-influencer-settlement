import type { DealState } from "./types";

export const usd = (v: string | number | null | undefined) =>
  v === null || v === undefined || v === "" ? "—" : `$${Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const num = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v.toLocaleString("en-US"));

export const short = (addr?: string | null, n = 6) => (!addr ? "—" : addr.length <= 2 * n + 2 ? addr : `${addr.slice(0, n)}…${addr.slice(-4)}`);

export function when(iso?: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export const humanize = (s: string) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

type Tone = "neutral" | "info" | "warn" | "good" | "bad";

export const STATE_INFO: Record<DealState, { label: string; tone: Tone; hint: string }> = {
  NEGOTIATING: { label: "Negotiating", tone: "info", hint: "Agents are exchanging offers over A2A" },
  REJECTED: { label: "Rejected", tone: "neutral", hint: "No agreement, or not selected" },
  ESCALATED: { label: "Needs a human", tone: "warn", hint: "An agent's policy handed this decision to a person; nothing was agreed or reserved" },
  AGREED: { label: "Agreed", tone: "info", hint: "Influencer agent signed the terms" },
  CART_CREATED: { label: "Cart created", tone: "info", hint: "Cart Mandate issued, policy check running" },
  AWAITING_APPROVAL: { label: "Awaiting signature", tone: "warn", hint: "Advertiser must sign the Payment Mandate" },
  BLOCKED: { label: "Blocked by policy", tone: "bad", hint: "Policy refused to sign" },
  AWAITING_CHECKOUT: { label: "Awaiting checkout", tone: "warn", hint: "Pay in HSP's hosted checkout" },
  SIGNED: { label: "Payment pending", tone: "info", hint: "Signed authorization submitted to HSP; no settlement yet" },
  FUNDING: { label: "Settling", tone: "info", hint: "Waiting for the settlement transaction to be verified" },
  PAYMENT_UNKNOWN: { label: "Payment unconfirmed", tone: "warn", hint: "Outcome unclear (timeout or unverifiable). Being reconciled; do not sign again" },
  FUNDED: { label: "Funded — post now", tone: "good", hint: "Settlement verified and locked in escrow; waiting for the video" },
  PAYMENT_FAILED: { label: "Payment failed", tone: "bad", hint: "HSP or the chain rejected the payment; nothing is locked" },
  PAYMENT_EXPIRED: { label: "Authorization expired", tone: "neutral", hint: "The signature expired before it was used; nothing moved" },
  PROOF_SUBMITTED: { label: "Proof submitted", tone: "info", hint: "Verifier is checking the video" },
  PROOF_REJECTED: { label: "Proof rejected", tone: "bad", hint: "Fix the post and resubmit before the deadline" },
  VERIFIED: { label: "Verified", tone: "good", hint: "Fixed fee releases now, or after your review if required" },
  FIXED_RELEASED: { label: "Fixed fee paid", tone: "good", hint: "Performance fee pays out at day 7" },
  DISPUTED: { label: "Disputed", tone: "bad", hint: "Funds frozen until resolved" },
  COMPLETED: { label: "Completed", tone: "good", hint: "All payouts done, remainder refunded" },
  EXPIRED_REFUNDED: { label: "Expired · refunded", tone: "neutral", hint: "No valid proof by the deadline" },
};

export const CAMPAIGN_TONE: Record<string, Tone> = {
  planning: "info",
  negotiating: "info",
  awaiting_approval: "warn",
  funding: "info",
  active: "good",
  done: "neutral",
  failed: "bad",
};

export const ACTOR_LABEL = (actor: string) => {
  if (actor.startsWith("influencer-agent")) return "Influencer agent";
  return (
    {
      "advertiser-agent": "Advertiser agent",
      policy: "Policy engine",
      wallet: "Wallet",
      hsp: "HSP gateway",
      escrow: "Escrow",
      verifier: "Verifier",
      human: "Human",
      "zyroxq-merchant": "ZyroXQ merchant",
    }[actor] ?? actor
  );
};

/** 75 → "1:15" */
export function clock(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

export function videoLength(min: number, max: number): string {
  if (!max) return "Any length";
  return min ? `${clock(min)} – ${clock(max)}` : `Up to ${clock(max)}`;
}

export const DELIVERABLE_LABEL: Record<string, string> = { youtube_short: "YouTube Short", youtube_video: "YouTube video" };

const WORDS_PER_PAGE = 500;

export function docSize(text: string): { words: number; pages: number } {
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  return { words, pages: words / WORDS_PER_PAGE };
}

export const MODEL_LABEL: Record<string, string> = { fixed: "Fixed fee", cpm: "CPM only", hybrid: "Fixed + CPM" };

/** "$19 fixed + $2.50 CPM, cap $40" for any compensation model. */
export function termsText(t: { fixed_fee: string; cpm: string; cap: string }, model = "hybrid"): string {
  if (model === "fixed") return `${usd(t.fixed_fee)} fixed`;
  if (model === "cpm") return `${usd(t.cpm)} CPM, cap ${usd(t.cap)}`;
  return `${usd(t.fixed_fee)} fixed + ${usd(t.cpm)} CPM, cap ${usd(t.cap)}`;
}

export const SOURCE_LABEL: Record<string, string> = {
  youtube_data_api: "YouTube API · verified",
  self_reported: "Self-reported",
  derived_estimate: "Estimate",
  unavailable: "Unknown",
};

/** What the advertiser calls a campaign; older campaigns without a name read as "Brand · niche". */
export const campaignName = (spec: { name?: string; advertiser: string; niche: string }) => spec.name?.trim() || `${spec.advertiser} · ${spec.niche}`;
