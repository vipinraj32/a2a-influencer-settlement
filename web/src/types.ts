// Mirrors the pydantic models in src/zyroxq/models.py. Money is always a decimal string.

export type Role = "advertiser" | "influencer";

export interface RateCard {
  fixed_min: string;
  cpm_min: string;
  preferred_fixed: string;
}

export interface ChannelInfo {
  channel_id: string;
  title: string;
  subscribers: number;
  thumbnail_url: string;
  verified_at: string;
}

export interface User {
  id: string;
  email: string;
  name: string;
  picture: string;
  role: Role;
  created_at: string;
  wallet: string;
  company: string;
  brand_handle: string;
  channel: ChannelInfo | null;
  niche: string;
  rate_card: RateCard | null;
  persona: string;
  agent_active: boolean;
  agent_signer: string;
  delegation: string;
  delegation_signature: string;
}

export interface Me {
  user: User;
  profile_complete: boolean;
  agent_url?: string;
  agent_listed?: boolean;
}

export interface ChainInfo {
  id: number;
  name: string;
  rpc_url: string;
  explorer_url: string;
  currency: string;
}

export interface PlatformStatus {
  settlement_mode: "simulated" | "hosted";
  browser_signing: boolean;
  escrow: { address: string; simulated: boolean; chain_id: number };
  token: { coin: string; network: string; address: string; decimals: number };
  chain: ChainInfo;
  verifier: string;
  verifier_online: boolean;
  login_configured: boolean;
  llm: string | null;
  store: string;
  clock_offset_hours: number;
  now: string;
  warnings: string[];
}

export interface DirectoryEntry {
  id: string;
  name: string;
  channel_id: string;
  subscribers: number;
  niche: string;
  wallet: string;
  avatar_url: string;
  agent_url: string;
}

export interface CampaignSpec {
  id: string;
  advertiser: string;
  brand_handle: string;
  niche: string;
  budget: string;
  max_creators: number;
  min_subscribers: number;
  max_subscribers: number;
  max_fixed: string;
  max_cpm: string;
  cap_per_creator: string;
  deliverable: string;
  publish_within_days: number;
  required_tags: string[];
  brief: string;
  owner_id: string;
  payer: string;
}

export type CampaignStatus = "planning" | "negotiating" | "awaiting_approval" | "funding" | "active" | "done" | "failed";

export interface Campaign {
  spec: CampaignSpec;
  status: CampaignStatus;
  created_at: string;
  error: string;
}

export interface TypedData {
  types: Record<string, { name: string; type: string }[]>;
  primaryType: string;
  domain: Record<string, string | number>;
  message: Record<string, string>;
}

export interface SigningRequest {
  deal_id: string;
  influencer_id: string;
  amount: string | null;
  typed_data: TypedData | null;
  payment_url: string | null;
}

export interface CampaignDetail extends Campaign {
  pending_approval: { campaign_id: string; deals: string[]; total: string; payer: string; signing: "browser" | "hosted_checkout" } | null;
  signing_requests: SigningRequest[];
}

export type DealState =
  | "NEGOTIATING"
  | "REJECTED"
  | "AGREED"
  | "CART_CREATED"
  | "AWAITING_APPROVAL"
  | "BLOCKED"
  | "AWAITING_CHECKOUT"
  | "SIGNED"
  | "FUNDING"
  | "FUNDED"
  | "PAYMENT_FAILED"
  | "PROOF_SUBMITTED"
  | "PROOF_REJECTED"
  | "VERIFIED"
  | "FIXED_RELEASED"
  | "DISPUTED"
  | "COMPLETED"
  | "EXPIRED_REFUNDED";

export interface Offer {
  deal_id: string;
  campaign_id: string;
  round: number;
  fixed_fee: string;
  cpm: string;
  cap: string;
  deliverable: string;
  publish_within_days: number;
  required_tags: string[];
  note: string;
}

export interface Round {
  offer: Offer;
  reply: { kind: "counter" | "accept" | "reject"; reason: string; decided_by?: string; counter: Offer | null };
}

export interface DealTerms {
  deal_id: string;
  campaign_id: string;
  advertiser: string;
  influencer_id: string;
  channel_id: string;
  payee: string;
  asset: string;
  chain_id: number;
  fixed_fee: string;
  cpm: string;
  cap: string;
  deliverable: string;
  publish_by: string;
  required_tags: string[];
  min_live_hours: number;
  views_measured_after_days: number;
  review_window_hours: number;
  influencer_signature: string;
}

export interface ProofCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface Payout {
  kind: "fixed" | "performance" | "refund";
  to: string;
  amount: string;
  tx_hash: string;
  at: string;
}

export interface Deal {
  id: string;
  campaign_id: string;
  influencer_id: string;
  state: DealState;
  rounds: Round[];
  terms: DealTerms | null;
  agreed_at: string | null;
  payment_request_id: string | null;
  payment_url: string | null;
  payment_status: string | null;
  payer: string | null;
  funding_tx: string | null;
  proof: { video_id: string; submitted_at: string; checks: ProofCheck[]; published_at: string | null; views: number | null } | null;
  attestations: { kind: string; amount: string; evidence_hash: string; verifier: string; signature: string }[];
  payouts: Payout[];
  fixed_release_due: string | null;
  performance_due: string | null;
  policy_violations: string[];
  updated_at: string | null;
}

export interface InfluencerDeal extends Deal {
  campaign: Pick<CampaignSpec, "advertiser" | "brand_handle" | "niche" | "deliverable" | "brief" | "required_tags">;
}

export interface AuditEvent {
  at: string;
  campaign_id: string;
  deal_id: string | null;
  actor: string;
  kind: string;
  detail: Record<string, unknown>;
}

export interface LiveEvent {
  type: "audit" | "deal" | "campaign";
  campaign_id: string;
  deal_id?: string | null;
  kind?: string;
  state?: DealState;
  status?: CampaignStatus;
}

export interface CampaignInput {
  advertiser: string;
  brand_handle: string;
  niche: string;
  budget: string;
  max_creators: number;
  min_subscribers: number;
  max_subscribers: number;
  max_fixed: string;
  max_cpm: string;
  cap_per_creator: string;
  publish_within_days: number;
  brief: string;
}
