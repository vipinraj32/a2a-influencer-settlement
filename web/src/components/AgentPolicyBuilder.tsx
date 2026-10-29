import { useEffect, useState, type ReactNode } from "react";
import { ApiError, api } from "../api";
import { humanize, when } from "../format";
import type { AdvertiserGoal, AdvertiserPolicy, AgentPolicy, CompensationModel, InfluencerGoal, InfluencerPolicy } from "../types";
import { Card, ErrorText, Notice, Spinner } from "./ui";

const INFLUENCER_GOALS: InfluencerGoal[] = ["maximize_earnings", "guaranteed_income", "brand_fit", "long_term_partnership", "minimize_workload"];
const ADVERTISER_GOALS: AdvertiserGoal[] = ["maximize_roi", "minimize_cost", "maximize_reach", "audience_relevance", "evidence_quality", "speed_to_close"];
const MODELS: CompensationModel[] = ["fixed", "cpm", "hybrid"];

const list = (v: string) =>
  v
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label>
      {label}
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="check">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

/** Comma-separated list input that keeps what the user typed until blur. */
function ListInput({ value, onChange, placeholder }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  const [text, setText] = useState(value.join(", "));
  return <input value={text} placeholder={placeholder} onChange={(e) => setText(e.target.value)} onBlur={() => onChange(list(text))} />;
}

function Weights<G extends string>({ goals, primary, weights, onChange }: { goals: G[]; primary: G; weights: Partial<Record<G, number>>; onChange: (w: Partial<Record<G, number>>) => void }) {
  return (
    <div className="weights">
      {goals
        .filter((g) => g !== primary)
        .map((g) => (
          <label key={g} className="weight">
            <span>{humanize(g)}</span>
            <input type="range" min={0} max={1} step={0.05} value={weights[g] ?? 0} onChange={(e) => onChange({ ...weights, [g]: Number(e.target.value) })} />
            <span className="mono small">{(weights[g] ?? 0).toFixed(2)}</span>
          </label>
        ))}
    </div>
  );
}

function InfluencerForm({ p, set }: { p: InfluencerPolicy; set: (p: InfluencerPolicy) => void }) {
  const hard = (k: Partial<InfluencerPolicy["hard"]>) => set({ ...p, hard: { ...p.hard, ...k } });
  const pref = (k: Partial<InfluencerPolicy["preferences"]>) => set({ ...p, preferences: { ...p.preferences, ...k } });
  const prem = (k: Partial<InfluencerPolicy["preferences"]["premiums"]>) => pref({ premiums: { ...p.preferences.premiums, ...k } });
  return (
    <>
      <Card title="1 · Objective" className="policy-card">
        <div className="grid-3 form">
          <Field label="Primary goal">
            <select value={p.objective.primary} onChange={(e) => set({ ...p, objective: { ...p.objective, primary: e.target.value as InfluencerGoal } })}>
              {INFLUENCER_GOALS.map((g) => (
                <option key={g} value={g}>
                  {humanize(g)}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <p className="small muted">Secondary goals, weighted. They shape scoring and concessions, never the hard limits.</p>
        <Weights goals={INFLUENCER_GOALS} primary={p.objective.primary} weights={p.objective.weights} onChange={(w) => set({ ...p, objective: { ...p.objective, weights: w } })} />
      </Card>

      <Card title="2 · Hard constraints (never crossed)" className="policy-card">
        <div className="grid-3 form">
          <Field label="Minimum fixed fee (USD)" hint="Raised automatically by your premiums below">
            <input type="number" min="0" step="0.01" value={p.hard.min_fixed_fee} onChange={(e) => hard({ min_fixed_fee: e.target.value })} />
          </Field>
          <Field label="Minimum CPM (USD)">
            <input type="number" min="0" step="0.01" value={p.hard.min_cpm} onChange={(e) => hard({ min_cpm: e.target.value })} />
          </Field>
          <Field label="Shortest publish window (days)">
            <input type="number" min={0} max={90} value={p.hard.min_turnaround_days} onChange={(e) => hard({ min_turnaround_days: Number(e.target.value) })} />
          </Field>
          <Field label="Max revision rounds">
            <input type="number" min={0} max={5} value={p.hard.max_revisions} onChange={(e) => hard({ max_revisions: Number(e.target.value) })} />
          </Field>
          <Field label="Max content license (days)">
            <input type="number" min={0} max={730} value={p.hard.max_license_days} onChange={(e) => hard({ max_license_days: Number(e.target.value) })} />
          </Field>
          <Field label="Max exclusivity (days)">
            <input
              type="number"
              min={0}
              max={365}
              value={p.hard.max_exclusivity_days}
              disabled={!p.hard.allow_exclusivity}
              onChange={(e) => hard({ max_exclusivity_days: Number(e.target.value) })}
            />
          </Field>
          <Field label="Never promote (categories)">
            <ListInput value={p.hard.forbidden_categories} onChange={(v) => hard({ forbidden_categories: v })} placeholder="alcohol, gambling" />
          </Field>
        </div>
        <div className="row">
          {MODELS.map((m) => (
            <Check
              key={m}
              label={`Accept ${m} deals`}
              checked={p.hard.accepted_models.includes(m)}
              onChange={(v) => hard({ accepted_models: v ? [...p.hard.accepted_models, m] : p.hard.accepted_models.filter((x) => x !== m) })}
            />
          ))}
        </div>
        <div className="row">
          <Check label="Allow paid amplification" checked={p.hard.allow_paid_amplification} onChange={(v) => hard({ allow_paid_amplification: v })} />
          <Check label="Allow exclusivity" checked={p.hard.allow_exclusivity} onChange={(v) => hard({ allow_exclusivity: v, max_exclusivity_days: v ? p.hard.max_exclusivity_days : 0 })} />
          <Check label="Require a written brief" checked={p.hard.require_brief} onChange={(v) => hard({ require_brief: v })} />
        </div>
      </Card>

      <Card title="3 · Flexible preferences" className="policy-card">
        <div className="grid-3 form">
          <Field label="Opening ask, fixed (USD)">
            <input type="number" min="0" step="0.01" value={p.preferences.preferred_fixed_fee} onChange={(e) => pref({ preferred_fixed_fee: e.target.value })} />
          </Field>
          <Field label="Opening ask, CPM (USD)" hint="Empty = your minimum CPM">
            <input type="number" min="0" step="0.01" value={p.preferences.preferred_cpm ?? ""} onChange={(e) => pref({ preferred_cpm: e.target.value || null })} />
          </Field>
          <Field label="Expected views per Short" hint="Your own estimate, labelled as one">
            <input type="number" min={0} value={p.preferences.expected_views ?? ""} onChange={(e) => pref({ expected_views: e.target.value ? Number(e.target.value) : null })} />
          </Field>
          <Field label="Preferred brand categories">
            <ListInput value={p.preferences.preferred_categories} onChange={(v) => pref({ preferred_categories: v })} placeholder="fitness, food" />
          </Field>
          <Field label="Brand-fit discount (%)" hint="Concede faster for a good fit, never below the floor">
            <input type="number" min={0} max={30} value={p.preferences.brand_fit_discount_pct} onChange={(e) => pref({ brand_fit_discount_pct: e.target.value })} />
          </Field>
          <Field label="Minimum score to accept (0-1)" hint="0 accepts anything that clears the hard limits">
            <input type="number" min={0} max={1} step={0.05} value={p.preferences.min_acceptable_score} onChange={(e) => pref({ min_acceptable_score: Number(e.target.value) })} />
          </Field>
        </div>
        <Check
          label="Ask me instead of declining when a hard constraint fails"
          checked={p.preferences.escalate_instead_of_reject}
          onChange={(v) => pref({ escalate_instead_of_reject: v })}
        />
        <h3>Premiums on your floor and ask</h3>
        <div className="grid-3 form">
          <Field label="Paid amplification (%)">
            <input type="number" min={0} value={p.preferences.premiums.paid_amplification_pct} onChange={(e) => prem({ paid_amplification_pct: e.target.value })} />
          </Field>
          <Field label="Exclusivity per 30 days (%)">
            <input type="number" min={0} value={p.preferences.premiums.exclusivity_pct_per_30_days} onChange={(e) => prem({ exclusivity_pct_per_30_days: e.target.value })} />
          </Field>
          <Field label="License per 30 days (%)">
            <input type="number" min={0} value={p.preferences.premiums.license_pct_per_30_days} onChange={(e) => prem({ license_pct_per_30_days: e.target.value })} />
          </Field>
          <Field label="Rush (%)" hint={`Publish window under ${p.preferences.premiums.rush_days} days`}>
            <input type="number" min={0} value={p.preferences.premiums.rush_pct} onChange={(e) => prem({ rush_pct: e.target.value })} />
          </Field>
          <Field label="Rush threshold (days)">
            <input type="number" min={0} max={30} value={p.preferences.premiums.rush_days} onChange={(e) => prem({ rush_days: Number(e.target.value) })} />
          </Field>
          <Field label="Extra revision fee (USD each)">
            <input type="number" min={0} step="0.01" value={p.preferences.premiums.extra_revision_fee} onChange={(e) => prem({ extra_revision_fee: e.target.value })} />
          </Field>
        </div>
      </Card>

      <Card title="4 · Creator profile and disclosure (shared with advertisers as self-reported)" className="policy-card">
        <div className="grid-3 form">
          <Field label="Audience countries (ISO, largest first)">
            <ListInput value={p.creator.audience_countries} onChange={(v) => set({ ...p, creator: { ...p.creator, audience_countries: v.map((c) => c.toUpperCase()) } })} placeholder="IN, US" />
          </Field>
          <Field label="Content languages">
            <ListInput value={p.creator.content_languages} onChange={(v) => set({ ...p, creator: { ...p.creator, content_languages: v } })} placeholder="English, Hindi" />
          </Field>
          <Field label="Preferred disclosure label">
            <input value={p.disclosure.preferred_label} maxLength={40} onChange={(e) => set({ ...p, disclosure: { ...p.disclosure, preferred_label: e.target.value } })} />
          </Field>
        </div>
        <Field label="Short bio">
          <textarea rows={2} maxLength={500} value={p.creator.bio} onChange={(e) => set({ ...p, creator: { ...p.creator, bio: e.target.value } })} />
        </Field>
      </Card>
    </>
  );
}

function AdvertiserForm({ p, set }: { p: AdvertiserPolicy; set: (p: AdvertiserPolicy) => void }) {
  const hard = (k: Partial<AdvertiserPolicy["hard"]>) => set({ ...p, hard: { ...p.hard, ...k } });
  const pref = (k: Partial<AdvertiserPolicy["preferences"]>) => set({ ...p, preferences: { ...p.preferences, ...k } });
  return (
    <>
      <Card title="1 · Objective" className="policy-card">
        <div className="grid-3 form">
          <Field label="Primary goal">
            <select value={p.objective.primary} onChange={(e) => set({ ...p, objective: { ...p.objective, primary: e.target.value as AdvertiserGoal } })}>
              {ADVERTISER_GOALS.map((g) => (
                <option key={g} value={g}>
                  {humanize(g)}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Weights goals={ADVERTISER_GOALS} primary={p.objective.primary} weights={p.objective.weights} onChange={(w) => set({ ...p, objective: { ...p.objective, weights: w } })} />
      </Card>
      <Card title="2 · Hard constraints" className="policy-card">
        <p className="small muted">Money ceilings (max fixed fee, max CPM, cap, budget) are set per campaign. These apply to every campaign on top.</p>
        <div className="grid-3 form">
          <Field label="Min verified subscribers" hint="From the YouTube Data API, never self-reported">
            <input type="number" min={0} value={p.hard.min_verified_subscribers} onChange={(e) => hard({ min_verified_subscribers: Number(e.target.value) })} />
          </Field>
          <Field label="Blocked niches">
            <ListInput value={p.hard.blocked_niches} onChange={(v) => hard({ blocked_niches: v })} />
          </Field>
        </div>
        <Check label="Only OAuth-verified channels" checked={p.hard.require_verified_channel} onChange={(v) => hard({ require_verified_channel: v })} />
      </Card>
      <Card title="3 · Flexible preferences" className="policy-card">
        <div className="grid-3 form">
          <Field label="Opening fixed fee (% of max)">
            <input type="number" min={1} max={100} value={p.preferences.opening_fixed_pct} onChange={(e) => pref({ opening_fixed_pct: e.target.value })} />
          </Field>
          <Field label="Opening CPM (% of max)">
            <input type="number" min={1} max={100} value={p.preferences.opening_cpm_pct} onChange={(e) => pref({ opening_cpm_pct: e.target.value })} />
          </Field>
          <Field label="Concession per round (% of the gap)">
            <input type="number" min={1} max={100} value={p.preferences.concession_pct} onChange={(e) => pref({ concession_pct: e.target.value })} />
          </Field>
          <Field label="Ask me above this fixed fee (USD)" hint="Empty = never escalate">
            <input type="number" min="0" step="0.01" value={p.preferences.escalate_fixed_above ?? ""} onChange={(e) => pref({ escalate_fixed_above: e.target.value || null })} />
          </Field>
          <Field label="Niche-fit premium (%)" hint="Concede faster for a niche match, within the ceilings">
            <input type="number" min={0} max={50} value={p.preferences.fit_premium_pct} onChange={(e) => pref({ fit_premium_pct: e.target.value })} />
          </Field>
          <Field label="Minimum score to accept (0-1)">
            <input type="number" min={0} max={1} step={0.05} value={p.preferences.min_acceptable_score} onChange={(e) => pref({ min_acceptable_score: Number(e.target.value) })} />
          </Field>
        </div>
      </Card>
    </>
  );
}

/** Agent Profile Builder: three layers (objective, hard constraints, preferences), saved as immutable versions. */
export function AgentPolicyBuilder() {
  const [policy, setPolicy] = useState<AgentPolicy | null>(null);
  const [isDefault, setIsDefault] = useState(false);
  const [versions, setVersions] = useState<AgentPolicy[]>([]);
  const [pending, setPending] = useState<string[] | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    const [p, v] = await Promise.all([api.agentPolicy(), api.policyVersions()]);
    setPolicy(p.policy);
    setIsDefault(p.is_default);
    setVersions(v);
  }
  useEffect(() => {
    load().catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  async function save(confirm: boolean) {
    if (!policy) return;
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const res = await api.saveAgentPolicy(policy, confirm);
      setPending(null);
      setSaved(`Saved as version ${res.policy?.version}. Negotiations already running keep the version they started with.`);
      await load();
    } catch (e) {
      const body = e instanceof ApiError ? (e.body as { detail?: { material_changes?: string[] } } | null) : null;
      if (e instanceof ApiError && e.status === 409 && body?.detail?.material_changes) setPending(body.detail.material_changes);
      else setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (error && !policy) return <ErrorText error={error} />;
  if (!policy) return isDefault ? <Notice tone="warn">Finish your rates first: the profile builder starts from them.</Notice> : <Spinner />;
  return (
    <div className="stack">
      <Notice>
        {isDefault ? "You are on the defaults derived from your rates. " : `Version ${policy.version}, saved ${when(policy.updated_at)}. `}
        Everything here is private to your agent. The other side only sees your offers and short, fixed explanations — never your limits, weights or scores.
      </Notice>
      {policy.role === "influencer" ? <InfluencerForm p={policy} set={setPolicy} /> : <AdvertiserForm p={policy} set={setPolicy} />}
      <Card title="Negotiation style">
        <label className="form">
          Persona for the AI (it suggests; the rules above decide)
          <textarea rows={2} maxLength={500} value={policy.persona} onChange={(e) => setPolicy({ ...policy, persona: e.target.value })} />
        </label>
      </Card>
      {pending && (
        <Notice tone="warn">
          <strong>These changes loosen hard constraints:</strong>
          <ul className="bullets">
            {pending.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
          <button type="button" className="btn btn-danger" disabled={busy} onClick={() => save(true)}>
            Confirm and save
          </button>
        </Notice>
      )}
      <div className="row">
        <button type="button" className="btn btn-primary" disabled={busy} onClick={() => save(false)}>
          {busy ? "Saving…" : "Save new version"}
        </button>
        {saved && <span className="small muted">{saved}</span>}
        <ErrorText error={error} />
      </div>
      {versions.length > 0 && (
        <Card title="Version history">
          <table className="table">
            <thead>
              <tr>
                <th>Version</th>
                <th>Saved</th>
                <th>Primary goal</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <tr key={v.version}>
                  <td>v{v.version}</td>
                  <td>{when(v.updated_at)}</td>
                  <td>{humanize(v.objective.primary)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
