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
  AGREED: { label: "Agreed", tone: "info", hint: "Influencer agent signed the terms" },
  CART_CREATED: { label: "Cart created", tone: "info", hint: "Cart Mandate issued, policy check running" },
  AWAITING_APPROVAL: { label: "Awaiting signature", tone: "warn", hint: "Advertiser must sign the Payment Mandate" },
  BLOCKED: { label: "Blocked by policy", tone: "bad", hint: "Policy refused to sign" },
  AWAITING_CHECKOUT: { label: "Awaiting checkout", tone: "warn", hint: "Pay in HSP's hosted checkout" },
  SIGNED: { label: "Signed", tone: "info", hint: "Payment Mandate submitted to HSP" },
  FUNDING: { label: "Funding", tone: "info", hint: "HSP is settling on-chain" },
  FUNDED: { label: "Funded — post now", tone: "good", hint: "Money locked in escrow; waiting for the video" },
  PAYMENT_FAILED: { label: "Payment failed", tone: "bad", hint: "HSP or the escrow rejected the payment" },
  PROOF_SUBMITTED: { label: "Proof submitted", tone: "info", hint: "Verifier is checking the video" },
  PROOF_REJECTED: { label: "Proof rejected", tone: "bad", hint: "Fix the post and resubmit before the deadline" },
  VERIFIED: { label: "Verified", tone: "good", hint: "Fixed fee releases after the live window" },
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
