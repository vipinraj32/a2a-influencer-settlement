import type {
  AuditEvent,
  Campaign,
  CampaignDetail,
  CampaignInput,
  Deal,
  DirectoryEntry,
  InfluencerDeal,
  AgentPolicy,
  ComplianceView,
  DecisionRecord,
  DisclosurePolicy,
  EvidenceItem,
  Me,
  NegotiationView,
  PlatformStatus,
  PolicyResponse,
  WalletBalance,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body: unknown = null,
  ) {
    super(message);
  }
}

function detail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (d && typeof d === "object" && "message" in d) return String((d as { message: unknown }).message);
    if (Array.isArray(d)) return d.map((e) => (e && typeof e === "object" && "msg" in e ? String(e.msg) : String(e))).join("; ");
  }
  return fallback;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let parsed: unknown = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = text; // proxy or server error page
  }
  if (!res.ok) throw new ApiError(res.status, detail(parsed, `${res.status} ${res.statusText}`), parsed);
  return parsed as T;
}

const get = <T>(path: string) => request<T>("GET", path);
const post = <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {});
const put = <T>(path: string, body: unknown) => request<T>("PUT", path, body);

export const api = {
  status: () => get<PlatformStatus>("/api/status"),
  me: () => get<Me>("/api/me"),
  logout: () => post<{ status: string }>("/auth/logout"),
  loginUrl: (role: "advertiser" | "influencer") => `/auth/google/login?role=${role}`,

  walletChallenge: (address: string) => post<{ message: string }>("/api/me/wallet/challenge", { address }),
  linkWallet: (signature: string) => post<Me>("/api/me/wallet", { signature }),
  walletBalance: () => get<WalletBalance>("/api/me/wallet/balance"),
  saveAdvertiser: (company: string, brand_handle: string) => put<Me>("/api/me/advertiser", { company, brand_handle }),
  saveInfluencer: (body: { niche: string; fixed_min: string; cpm_min: string; preferred_fixed: string; persona: string; agent_active: boolean }) =>
    put<Me>("/api/me/influencer", body),

  directory: () => get<DirectoryEntry[]>("/api/influencers"),

  campaigns: () => get<Campaign[]>("/api/campaigns"),
  campaign: (id: string) => get<CampaignDetail>(`/api/campaigns/${encodeURIComponent(id)}`),
  campaignDeals: (id: string) => get<Deal[]>(`/api/campaigns/${encodeURIComponent(id)}/deals`),
  campaignEvents: (id: string) => get<AuditEvent[]>(`/api/campaigns/${encodeURIComponent(id)}/events`),
  createCampaign: (body: CampaignInput) => post<Campaign>("/api/campaigns", body),
  parseBrief: (brief: string) => post<CampaignInput>("/api/campaigns/parse-brief", { brief }),
  decide: (id: string, approved: boolean, signatures: Record<string, string> = {}) =>
    post<{ status: string }>(`/api/campaigns/${encodeURIComponent(id)}/decision`, { approved, signatures }),
  redraft: (id: string) => post<{ redrafted: string[] }>(`/api/campaigns/${encodeURIComponent(id)}/redraft`),

  deal: (id: string) => get<Deal>(`/api/deals/${encodeURIComponent(id)}`),
  dealEvents: (id: string) => get<AuditEvent[]>(`/api/deals/${encodeURIComponent(id)}/events`),
  submitProof: (id: string, video_url: string, attestations: Record<string, boolean> = {}) =>
    post<Deal>(`/api/deals/${encodeURIComponent(id)}/proof`, { video_url, attestations }),
  negotiation: (id: string) => get<NegotiationView>(`/api/deals/${encodeURIComponent(id)}/negotiation`),
  decisions: (id: string) => get<DecisionRecord[]>(`/api/deals/${encodeURIComponent(id)}/decisions`),
  review: (id: string, approved: boolean, note: string) => post<Deal>(`/api/deals/${encodeURIComponent(id)}/compliance/review`, { approved, note }),
  compliance: (id: string) => get<ComplianceView>(`/api/campaigns/${encodeURIComponent(id)}/compliance`),

  agentPolicy: () => get<PolicyResponse>("/api/me/agent-policy"),
  saveAgentPolicy: (policy: AgentPolicy, confirm_material_change = false) =>
    put<PolicyResponse>("/api/me/agent-policy", { policy, confirm_material_change }),
  policyVersions: () => get<AgentPolicy[]>("/api/me/agent-policy/versions"),
  myEvidence: () => get<EvidenceItem[]>("/api/me/evidence"),
  creatorEvidence: (id: string) =>
    get<{ evidence: EvidenceItem[]; profile: Record<string, unknown> | null }>(`/api/influencers/${encodeURIComponent(id)}/evidence`),
  disclosurePolicies: () => get<{ policies: DisclosurePolicy[]; disclaimer: string }>("/api/disclosure-policies"),
  dispute: (id: string, reason: string) => post<Deal>(`/api/deals/${encodeURIComponent(id)}/dispute`, { reason }),

  influencerDeals: () => get<InfluencerDeal[]>("/api/influencer/deals"),
  influencerInbox: () => get<Record<string, unknown>[]>("/api/influencer/inbox"),

  ask: (question: string) => post<{ answer: string }>("/api/assistant", { question }),
  advance: (hours: number) => post<{ clock_offset_hours: number; actions: number }>("/api/dev/advance", { hours }),
};
