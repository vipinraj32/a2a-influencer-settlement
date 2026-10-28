import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { api } from "../../api";
import { Card, ErrorText, Notice } from "../../components/ui";
import { usd } from "../../format";
import { useMe, useSession } from "../../session";
import type { CampaignInput } from "../../types";

export function NewCampaign() {
  const { user } = useMe();
  const { status } = useSession();
  const navigate = useNavigate();
  const [form, setForm] = useState<CampaignInput>({
    advertiser: user.company,
    brand_handle: user.brand_handle || "@",
    niche: "fitness",
    budget: "120.00",
    max_creators: 3,
    min_subscribers: 0,
    max_subscribers: 10_000_000,
    max_fixed: "25.00",
    max_cpm: "3.00",
    cap_per_creator: "40.00",
    publish_within_days: 7,
    brief: "",
  });
  const [busy, setBusy] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = <K extends keyof CampaignInput>(key: K, value: CampaignInput[K]) => setForm((f) => ({ ...f, [key]: value }));
  const money = (v: string) => (v === "" ? "" : Number(v).toFixed(2));

  async function draft() {
    setDrafting(true);
    setError(null);
    try {
      const d = await api.parseBrief(form.brief);
      setForm((f) => ({ ...f, ...d, advertiser: f.advertiser || d.advertiser, brand_handle: f.brand_handle.length > 1 ? f.brand_handle : d.brand_handle }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDrafting(false);
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = { ...form, budget: money(form.budget), max_fixed: money(form.max_fixed), max_cpm: money(form.max_cpm), cap_per_creator: money(form.cap_per_creator) };
      const created = await api.createCampaign(body);
      navigate(`/advertiser/campaigns/${created.spec.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  const maxEscrow = Number(form.cap_per_creator || 0) * form.max_creators;

  return (
    <div className="stack narrow">
      <h1>New campaign</h1>
      <Card title="Brief">
        <label className="form">
          Describe the campaign
          <textarea
            rows={3}
            value={form.brief}
            onChange={(e) => set("brief", e.target.value)}
            maxLength={2000}
            placeholder="Launching a protein snack. Want 3 fitness creators to post a 30s Short with #ad, budget about $120…"
          />
        </label>
        <div className="row">
          <button type="button" className="btn btn-secondary" disabled={drafting || form.brief.length < 10 || !status?.llm} onClick={draft}>
            {drafting ? "Drafting…" : "Fill the fields with AI"}
          </button>
          {!status?.llm && <span className="muted small">Needs ANTHROPIC_API_KEY on the server.</span>}
          <span className="muted small">The AI only drafts. You review and submit, and the policy engine enforces every limit.</span>
        </div>
      </Card>

      <form onSubmit={submit} className="stack">
        <Card title="Brand and targeting">
          <div className="grid-3 form">
            <label>
              Brand
              <input value={form.advertiser} onChange={(e) => set("advertiser", e.target.value)} required maxLength={80} />
            </label>
            <label>
              Brand handle
              <input value={form.brand_handle} onChange={(e) => set("brand_handle", e.target.value)} pattern="@[A-Za-z0-9_.]{2,40}" required />
            </label>
            <label>
              Niche
              <input value={form.niche} onChange={(e) => set("niche", e.target.value)} required minLength={2} />
            </label>
            <label>
              Min subscribers
              <input type="number" min={0} value={form.min_subscribers} onChange={(e) => set("min_subscribers", Number(e.target.value))} />
            </label>
            <label>
              Max subscribers
              <input type="number" min={1} value={form.max_subscribers} onChange={(e) => set("max_subscribers", Number(e.target.value))} />
            </label>
            <label>
              Publish within (days)
              <input type="number" min={1} max={60} value={form.publish_within_days} onChange={(e) => set("publish_within_days", Number(e.target.value))} />
            </label>
          </div>
        </Card>

        <Card title="Money: hard limits your agent cannot exceed">
          <div className="grid-3 form">
            <label>
              Total budget (USD)
              <input type="number" min="0.01" step="0.01" value={form.budget} onChange={(e) => set("budget", e.target.value)} required />
            </label>
            <label>
              Max creators
              <input type="number" min={1} max={10} value={form.max_creators} onChange={(e) => set("max_creators", Number(e.target.value))} />
            </label>
            <label>
              Escrow cap per creator (USD)
              <input type="number" min="0.01" step="0.01" value={form.cap_per_creator} onChange={(e) => set("cap_per_creator", e.target.value)} required />
              <small>Fixed fee + maximum performance fee.</small>
            </label>
            <label>
              Max fixed fee (USD)
              <input type="number" min="0.01" step="0.01" value={form.max_fixed} onChange={(e) => set("max_fixed", e.target.value)} required />
            </label>
            <label>
              Max per 1,000 views (USD)
              <input type="number" min="0" step="0.01" value={form.max_cpm} onChange={(e) => set("max_cpm", e.target.value)} required />
            </label>
          </div>
          <Notice>
            Up to {usd(maxEscrow)} can be locked in escrow ({form.max_creators} × {usd(form.cap_per_creator)}). You sign each Payment Mandate yourself
            with wallet {user.wallet.slice(0, 8)}…, and unspent money is refunded to that wallet.
          </Notice>
        </Card>

        <div className="row">
          <button className="btn btn-primary" disabled={busy}>
            {busy ? "Launching agent…" : "Launch advertiser agent"}
          </button>
          <ErrorText error={error} />
        </div>
      </form>
    </div>
  );
}
