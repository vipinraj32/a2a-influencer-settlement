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
  view_count: number | null;
  video_count: number | null;
  subscribers_hidden: boolean;
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
  settlement_simulated: boolean;
  settlement_min_confirmations: number;
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
  min_duration_seconds: number;
  max_duration_seconds: number;
  requirements: string;
  owner_id: string;
  payer: string;
  compensation_model: CompensationModel;
  scope: ContentScope;
  category: string;
  jurisdictions: string[];
  disclosure_policies: string[];
  max_rounds: number;
  review_required: boolean;
  agent_policy_version: number;
}

export type CompensationModel = "fixed" | "cpm" | "hybrid";

export interface ContentScope {
  usage_rights: "organic_only" | "brand_repost" | "paid_ads";
  license_days: number;
  paid_amplification: boolean;
  exclusivity_days: number;
  exclusivity_category: string;
  revisions: number;
  deliverable_count: number;
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
  state: DealState;
  typed_data: TypedData | null;
  payment_url: string | null;
  /** Server-side preflight: why this authorization cannot be submitted (empty = OK). */
  problems: string[];
  expired: boolean;
  valid_before: number | null;
}

/** The budget is a spending limit, not money held: reserved = caps of deals being or already paid. */
export interface FundingSummary {
  budget: string;
  reserved: string;
  funded: string;
  remaining: string;
}

export interface CampaignDetail extends Campaign {
  pending_approval: { campaign_id: string; deals: string[]; total: string; payer: string; signing: "browser" | "hosted_checkout" } | null;
  signing_requests: SigningRequest[];
  funding: FundingSummary;
}

export interface WalletBalance {
  wallet: string;
  token: string;
  token_address: string;
  chain_id: number;
  decimals: number;
  balance: string | null;
  units: string | null;
  error: string | null;
  checked_at: string;
  settlement_simulated: boolean;
}

export interface PaymentAttempt {
  idempotency_key: string;
  payer: string;
  to: string;
  value_units: number;
  valid_before: number;
  status: "submitted" | "pending" | "succeeded" | "failed" | "unknown";
  gateway: string;
  simulated: boolean;
  tx_hash: string | null;
  error: string;
  created_at: string;
  updated_at: string;
}

export type DealState =
  | "NEGOTIATING"
  | "REJECTED"
  | "ESCALATED"
  | "AGREED"
  | "CART_CREATED"
  | "AWAITING_APPROVAL"
  | "BLOCKED"
  | "AWAITING_CHECKOUT"
  | "SIGNED"
  | "FUNDING"
  | "PAYMENT_UNKNOWN"
  | "FUNDED"
  | "PAYMENT_FAILED"
  | "PAYMENT_EXPIRED"
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
  proposer: "advertiser" | "influencer";
  max_rounds: number;
  compensation_model: CompensationModel;
  scope: ContentScope;
  category: string;
  disclosure_policies: string[];
  pricing_version: string;
  fixed_fee: string;
  cpm: string;
  cap: string;
  deliverable: string;
  publish_within_days: number;
  required_tags: string[];
  min_duration_seconds: number;
  max_duration_seconds: number;
  requirements_hash: string;
  note: string;
}

export interface Round {
  offer: Offer;
  reply: { kind: ReplyKind; reason: string; decided_by?: string; counter: Offer | null };
}

export type ReplyKind = "counter" | "accept" | "reject" | "clarify" | "escalate";

export interface DealTerms {
  deal_id: string;
  campaign_id: string;
  advertiser: string;
  influencer_id: string;
  channel_id: string;
  payee: string;
  asset: string;
  chain_id: number;
  compensation_model: CompensationModel;
  pricing_version: string;
  scope: ContentScope;
  disclosure_policies: string[];
  fixed_fee: string;
  cpm: string;
  cap: string;
  deliverable: string;
  publish_by: string;
  required_tags: string[];
  min_duration_seconds: number;
  max_duration_seconds: number;
  requirements_hash: string;
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
  simulated: boolean;
}

export interface Deal {
  id: string;
  campaign_id: string;
  influencer_id: string;
  state: DealState;
  rounds: Round[];
  negotiation_outcome: string;
  terms: DealTerms | null;
  approved_terms_hash: string | null;
  approvals: Approval[];
  agreed_at: string | null;
  payment_request_id: string | null;
  payment_url: string | null;
  payment_status: string | null;
  payer: string | null;
  payment_attempts: PaymentAttempt[];
  funding_tx: string | null;
  funding_simulated: boolean;
  proof: {
    video_id: string;
    submitted_at: string;
    checks: ProofCheck[];
    published_at: string | null;
    views: number | null;
    attestations: Record<string, boolean>;
  } | null;
  attestations: { kind: string; amount: string; evidence_hash: string; verifier: string; signature: string }[];
  payouts: Payout[];
  fixed_release_due: string | null;
  performance_due: string | null;
  policy_violations: string[];
  updated_at: string | null;
}

export interface InfluencerDeal extends Deal {
  campaign: Pick<
    CampaignSpec,
    "advertiser" | "brand_handle" | "niche" | "deliverable" | "brief" | "required_tags" | "requirements" | "min_duration_seconds" | "max_duration_seconds"
  >;
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
  deliverable: Deliverable;
  min_duration_seconds: number;
  max_duration_seconds: number;
  requirements: string;
  compensation_model: CompensationModel;
  scope: ContentScope;
  category: string;
  jurisdictions: string[];
  max_rounds: number;
  review_required: boolean;
  required_tags: string[];
}

export type Deliverable = "youtube_short" | "youtube_video";

export interface Approval {
  kind: "payment" | "compliance_review";
  by: string;
  approved: boolean;
  terms_hash: string;
  note: string;
  at: string;
}

// ---- configurable agents (src/zyroxq/agent_policy) -------------------------------------

export type InfluencerGoal = "maximize_earnings" | "guaranteed_income" | "brand_fit" | "long_term_partnership" | "minimize_workload";
export type AdvertiserGoal = "minimize_cost" | "maximize_reach" | "maximize_roi" | "audience_relevance" | "evidence_quality" | "speed_to_close";

export interface InfluencerPolicy {
  role: "influencer";
  version: number;
  updated_at: string | null;
  objective: { primary: InfluencerGoal; weights: Partial<Record<InfluencerGoal, number>> };
  hard: {
    min_fixed_fee: string;
    min_cpm: string;
    accepted_models: CompensationModel[];
    min_turnaround_days: number;
    max_revisions: number;
    allow_paid_amplification: boolean;
    max_license_days: number;
    allow_exclusivity: boolean;
    max_exclusivity_days: number;
    forbidden_categories: string[];
    require_brief: boolean;
  };
  preferences: {
    preferred_fixed_fee: string;
    preferred_cpm: string | null;
    preferred_categories: string[];
    brand_fit_discount_pct: string;
    expected_views: number | null;
    min_acceptable_score: number;
    escalate_instead_of_reject: boolean;
    premiums: {
      paid_amplification_pct: string;
      exclusivity_pct_per_30_days: string;
      license_pct_per_30_days: string;
      rush_pct: string;
      rush_days: number;
      extra_revision_fee: string;
    };
  };
  creator: { audience_countries: string[]; content_languages: string[]; bio: string };
  disclosure: { preferred_label: string; jurisdictions: string[] };
  persona: string;
}

export interface AdvertiserPolicy {
  role: "advertiser";
  version: number;
  updated_at: string | null;
  objective: { primary: AdvertiserGoal; weights: Partial<Record<AdvertiserGoal, number>> };
  hard: { min_verified_subscribers: number; require_verified_channel: boolean; blocked_niches: string[] };
  preferences: {
    opening_fixed_pct: string;
    opening_cpm_pct: string;
    concession_pct: string;
    escalate_fixed_above: string | null;
    min_acceptable_score: number;
    fit_premium_pct: string;
  };
  persona: string;
}

export type AgentPolicy = InfluencerPolicy | AdvertiserPolicy;

export interface PolicyResponse<P extends AgentPolicy = AgentPolicy> {
  policy: P | null;
  is_default: boolean;
  material_changes?: string[];
}

export type DecisionAction = "ACCEPT" | "COUNTEROFFER" | "REJECT" | "REQUEST_CLARIFICATION" | "ESCALATE_TO_HUMAN";

export interface DecisionRecord {
  id: string;
  deal_id: string;
  campaign_id: string;
  party: "advertiser" | "influencer";
  round: number;
  objective: string;
  hard_checks: ProofCheck[];
  preferences_applied: string[];
  scores: Record<string, number>;
  score: number | null;
  action: DecisionAction;
  public_explanation: string;
  private_rationale: string;
  decided_by: string;
  llm_suggestion: Record<string, string> | null;
  llm_overridden: string[];
  policy_version: number;
  pricing_version: string;
  offer_id: string;
  response_offer_id: string | null;
  at: string;
}

export interface EvidenceItem {
  metric: string;
  value: unknown;
  source: "youtube_data_api" | "self_reported" | "derived_estimate" | "unavailable";
  verified: boolean;
  observed_at: string | null;
  limitations: string;
}

export interface PriceExample {
  model: CompensationModel;
  fixed: string;
  performance: string;
  uncapped_total: string;
  total: string;
  capped: boolean;
  eligible_views: number;
  version: string;
}

type Amounts = { fixed_fee: string; cpm: string; cap: string };
type Moved = { fixed_fee: string; cpm: string };

export interface NegotiationView {
  deal_id: string;
  status: "open" | "agreed" | "failed" | "escalated" | "agreed_then_cancelled";
  outcome: string;
  max_rounds: number;
  rounds_used: number;
  rounds_remaining: number;
  compensation_model: CompensationModel;
  scope: ContentScope;
  rounds: {
    round: number;
    advertiser_offer: Amounts | null;
    advertiser_moved: Moved | null;
    reply: ReplyKind;
    explanation: string;
    counter: Amounts | null;
    creator_moved: Moved | null;
  }[];
  payable: { model: CompensationModel; views_to_cap: number | null; examples: PriceExample[] } | null;
  agreement: { terms: DealTerms; terms_hash: string; approved_terms_hash: string | null; approvals: Approval[] } | null;
}

export interface DisclosurePolicy {
  id: string;
  jurisdiction: string;
  version: number;
  effective_from: string;
  sources: string[];
  approved_labels: string[];
  discouraged_labels: string[];
  placement_chars: number;
  in_video_required: boolean;
  livestream_rule: string;
  notes: string;
  disclaimer: string;
}

export interface ComplianceView {
  campaign_id: string;
  jurisdictions: string[];
  required_tags: string[];
  review_required: boolean;
  policies: DisclosurePolicy[];
  disclaimer: string;
  settlement_simulated: boolean;
  deals: {
    deal_id: string;
    influencer_id: string;
    state: DealState;
    disclosure_policies: string[];
    checks: ProofCheck[];
    attestations: Record<string, boolean>;
    review: "not_required" | "pending" | "approved" | "rejected";
    payment_approval: Approval | null;
    terms_hash: string;
    terms_hash_matches_approval: boolean;
    gates: string[];
    funding_simulated: boolean;
  }[];
}
