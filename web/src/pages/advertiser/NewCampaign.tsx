import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { api } from "../../api";
import { Card, ErrorText, Notice } from "../../components/ui";
import { MODEL_LABEL, clock, docSize, usd } from "../../format";
import { useMe, useSession } from "../../session";
import type { CampaignInput, CompensationModel, ContentScope, Deliverable, DisclosurePolicy } from "../../types";

// Keep in sync with api/app.py
const MAX_REQUIREMENTS_CHARS = 15_000;
const SHORTS_MAX_SECONDS = 180;
const MAX_VIDEO_SECONDS = 4 * 3600;

const OUTLINE = `1. Goal
What the video should achieve (awareness, sign-ups, sales).

2. Key messages
- The one thing viewers must remember
- Product benefits to mention

3. Must include
- Show the product on screen within the first 5 seconds
- Spoken call to action and the link in the description

4. Do not
- Mention competitors
- Make medical or financial claims

5. Style and tone
Energetic, honest, in your usual style.

6. Disclosure
Say "paid partnership" and add #ad.
`;

/** Minutes + seconds editor for a length in seconds. */
function Duration({ label, value, onChange, max }: { label: string; value: number; onChange: (s: number) => void; max: number }) {
  const m = Math.floor(value / 60);
  const s = value % 60;
  const update = (mins: number, secs: number) => onChange(Math.min(max, Math.max(0, (mins || 0) * 60 + (secs || 0))));
  return (
    <label>
      {label}
      <span className="duration">
        <input type="number" min={0} max={Math.floor(max / 60)} value={m} onChange={(e) => update(Number(e.target.value), s)} aria-label={`${label} minutes`} />
        <span className="muted">min</span>
        <input type="number" min={0} max={59} value={s} onChange={(e) => update(m, Number(e.target.value))} aria-label={`${label} seconds`} />
        <span className="muted">sec</span>
      </span>
    </label>
  );
}

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
    deliverable: "youtube_short",
    min_duration_seconds: 15,
    max_duration_seconds: 60,
    requirements: "",
    compensation_model: "hybrid",
    scope: { usage_rights: "organic_only", license_days: 0, paid_amplification: false, exclusivity_days: 0, exclusivity_category: "", revisions: 1, deliverable_count: 1 },
    category: "",
    jurisdictions: ["IN", "US"],
    max_rounds: 4,
    review_required: false,
    required_tags: ["#ad"],
  });
  const [policies, setPolicies] = useState<DisclosurePolicy[]>([]);
  const [tagsText, setTagsText] = useState("#ad");
  const [disclaimer, setDisclaimer] = useState("");
  useEffect(() => {
    api
      .disclosurePolicies()
      .then((r) => {
        setPolicies(r.policies);
        setDisclaimer(r.disclaimer);
      })
      .catch(() => setPolicies([]));
  }, []);
  const [busy, setBusy] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = <K extends keyof CampaignInput>(key: K, value: CampaignInput[K]) => setForm((f) => ({ ...f, [key]: value }));
  const setScope = <K extends keyof ContentScope>(key: K, value: ContentScope[K]) => setForm((f) => ({ ...f, scope: { ...f.scope, [key]: value } }));
  const toggleJurisdiction = (j: string) =>
    set("jurisdictions", form.jurisdictions.includes(j) ? form.jurisdictions.filter((x) => x !== j) : [...form.jurisdictions, j]);
  const tagsOk = (p: DisclosurePolicy) => form.required_tags.some((t) => p.approved_labels.some((l) => l.toLowerCase() === t.toLowerCase()));
  const disclosureProblems = form.jurisdictions.length
    ? policies.filter((p) => form.jurisdictions.includes(p.jurisdiction) && !tagsOk(p)).map((p) => `No tag approved for ${p.jurisdiction}`)
    : ["Choose at least one jurisdiction"];
  const model = form.compensation_model;
  const lengthInvalid = form.max_duration_seconds > 0 && form.min_duration_seconds > form.max_duration_seconds;
  const money = (v: string) => (v === "" ? "" : Number(v).toFixed(2));

  async function draft() {
    setDrafting(true);
    setError(null);
    try {
      const d = await api.parseBrief(form.brief);
      setForm((f) => ({
        ...f,
        ...d,
        advertiser: f.advertiser || d.advertiser,
        brand_handle: f.brand_handle.length > 1 ? f.brand_handle : d.brand_handle,
        requirements: f.requirements || d.requirements, // never overwrite a document the advertiser wrote
      }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDrafting(false);
    }
  }

  function setDeliverable(d: Deliverable) {
    setForm((f) =>
      d === "youtube_short"
        ? {
            ...f,
            deliverable: d,
            min_duration_seconds: Math.min(f.min_duration_seconds, SHORTS_MAX_SECONDS),
            max_duration_seconds: Math.min(f.max_duration_seconds || SHORTS_MAX_SECONDS, SHORTS_MAX_SECONDS),
          }
        : { ...f, deliverable: d },
    );
  }

  async function importFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    const text = (await file.text()).replace(/\r\n/g, "\n").trim();
    if (text.length > MAX_REQUIREMENTS_CHARS) {
      setError(`${file.name} has ${text.length.toLocaleString()} characters; the limit is ${MAX_REQUIREMENTS_CHARS.toLocaleString()} (about 5 pages).`);
      return;
    }
    setError(null);
    set("requirements", text);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (lengthInvalid) {
      setError("The minimum video length is longer than the maximum.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const body = {
        ...form,
        budget: money(form.budget),
        max_fixed: model === "cpm" ? "0.00" : money(form.max_fixed),
        max_cpm: model === "fixed" ? "0.00" : money(form.max_cpm),
        cap_per_creator: money(form.cap_per_creator),
      };
      const created = await api.createCampaign(body);
      navigate(`/advertiser/campaigns/${created.spec.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  const maxEscrow = Number(form.cap_per_creator || 0) * form.max_creators;
  const lengthCap = form.deliverable === "youtube_short" ? SHORTS_MAX_SECONDS : MAX_VIDEO_SECONDS;
  const doc = docSize(form.requirements);

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

        <Card title="Content requirements">
          <div className="grid-3 form">
            <label>
              Format
              <select value={form.deliverable} onChange={(e) => setDeliverable(e.target.value as Deliverable)}>
                <option value="youtube_short">YouTube Short (up to 3 min)</option>
                <option value="youtube_video">YouTube video</option>
              </select>
            </label>
            <Duration label="Minimum length" value={form.min_duration_seconds} max={lengthCap} onChange={(v) => set("min_duration_seconds", v)} />
            <Duration label="Maximum length" value={form.max_duration_seconds} max={lengthCap} onChange={(v) => set("max_duration_seconds", v)} />
          </div>
          <p className={`small ${lengthInvalid ? "error" : "muted"}`}>
            {lengthInvalid
              ? "The minimum is longer than the maximum."
              : form.max_duration_seconds
                ? `The verifier reads the real length from YouTube and rejects videos outside ${clock(form.min_duration_seconds)} – ${clock(form.max_duration_seconds)}.`
                : "A maximum of 0:00 accepts any length."}
          </p>

          <div className="form">
            <div className="row spread">
              <strong className="small">Requirements document</strong>
              <span className="row">
                <label className="btn btn-ghost file-btn">
                  Import .txt / .md
                  <input type="file" accept=".txt,.md,.markdown,text/plain,text/markdown" onChange={importFile} hidden />
                </label>
                {!form.requirements && (
                  <button type="button" className="btn btn-ghost" onClick={() => set("requirements", OUTLINE)}>
                    Start from an outline
                  </button>
                )}
              </span>
            </div>
            <textarea
              className="doc-editor"
              rows={18}
              aria-label="Requirements document"
              value={form.requirements}
              onChange={(e) => set("requirements", e.target.value)}
              maxLength={MAX_REQUIREMENTS_CHARS}
              placeholder="Everything the creator must do: key messages, scenes, things to avoid, tone, disclosure. Usually 1 to 3 pages."
            />
            <div className="row spread small muted">
              <span>
                {doc.words.toLocaleString()} words · ~{doc.pages.toFixed(1)} pages
              </span>
              <span className={form.requirements.length > MAX_REQUIREMENTS_CHARS * 0.9 ? "error" : ""}>
                {form.requirements.length.toLocaleString()} / {MAX_REQUIREMENTS_CHARS.toLocaleString()} characters
              </span>
            </div>
          </div>
          <Notice>
            Each influencer agent receives this document and signs its hash in the deal terms, so neither side can change it after the deal.
          </Notice>
        </Card>

        <Card title="Deal structure and rights">
          <div className="grid-3 form">
            <label>
              Compensation model
              <select value={model} onChange={(e) => set("compensation_model", e.target.value as CompensationModel)}>
                {(["hybrid", "fixed", "cpm"] as const).map((m) => (
                  <option key={m} value={m}>
                    {MODEL_LABEL[m]}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Usage rights
              <select value={form.scope.usage_rights} onChange={(e) => setScope("usage_rights", e.target.value as ContentScope["usage_rights"])}>
                <option value="organic_only">Organic post only</option>
                <option value="brand_repost">Brand may repost</option>
                <option value="paid_ads">Brand may run it as an ad</option>
              </select>
            </label>
            <label>
              License (days)
              <input type="number" min={0} max={730} value={form.scope.license_days} onChange={(e) => setScope("license_days", Number(e.target.value))} />
            </label>
            <label className="check">
              <input type="checkbox" checked={form.scope.paid_amplification} onChange={(e) => setScope("paid_amplification", e.target.checked)} />
              Paid amplification (boosting)
            </label>
            <label>
              Exclusivity (days)
              <input type="number" min={0} max={365} value={form.scope.exclusivity_days} onChange={(e) => setScope("exclusivity_days", Number(e.target.value))} />
            </label>
            <label>
              Exclusivity category
              <input value={form.scope.exclusivity_category} maxLength={60} onChange={(e) => setScope("exclusivity_category", e.target.value)} placeholder="e.g. protein snacks" />
            </label>
            <label>
              Revision rounds
              <input type="number" min={0} max={5} value={form.scope.revisions} onChange={(e) => setScope("revisions", Number(e.target.value))} />
            </label>
            <label>
              Brand category
              <input value={form.category} maxLength={60} onChange={(e) => set("category", e.target.value)} placeholder="e.g. food & beverage" />
              <small>Creators can refuse categories they never promote.</small>
            </label>
            <label>
              Negotiation rounds
              <input type="number" min={1} max={8} value={form.max_rounds} onChange={(e) => set("max_rounds", Number(e.target.value))} />
              <small>No deal is a valid outcome.</small>
            </label>
          </div>
          <Notice>
            Rights, exclusivity and revisions are part of the signed terms. Creators' agents may decline or ask for more when the scope goes beyond one organic
            post; one verifiable post per deal for now.
          </Notice>
        </Card>

        <Card title="Sponsorship disclosure">
          <div className="form">
            <div className="row">
              {policies.map((p) => (
                <label key={p.jurisdiction} className="check">
                  <input type="checkbox" checked={form.jurisdictions.includes(p.jurisdiction)} onChange={() => toggleJurisdiction(p.jurisdiction)} />
                  {p.jurisdiction} <span className="muted small">({p.id})</span>
                </label>
              ))}
            </div>
            <label>
              Required tags (comma separated)
              <input
                value={tagsText}
                onChange={(e) => {
                  setTagsText(e.target.value);
                  set(
                    "required_tags",
                    e.target.value
                      .split(",")
                      .map((t) => t.trim())
                      .filter(Boolean),
                  );
                }}
              />
            </label>
            <label className="check">
              <input type="checkbox" checked={form.review_required} onChange={(e) => set("review_required", e.target.checked)} />
              I review each post's disclosure before the fixed fee is released
            </label>
          </div>
          {policies
            .filter((p) => form.jurisdictions.includes(p.jurisdiction))
            .map((p) => (
              <p key={p.id} className="small muted">
                <strong>{p.jurisdiction}</strong>: approved labels {p.approved_labels.join(", ")}; label in the title or first {p.placement_chars} characters
                {p.in_video_required ? "; creator confirms an in-video disclosure" : ""}.
              </p>
            ))}
          {disclosureProblems.length > 0 && <Notice tone="warn">{disclosureProblems.join(". ")}.</Notice>}
          {disclaimer && <p className="small muted">{disclaimer}</p>}
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
            {model !== "cpm" && (
              <label>
                Max fixed fee (USD)
                <input type="number" min="0.01" step="0.01" value={form.max_fixed} onChange={(e) => set("max_fixed", e.target.value)} required />
                {model === "fixed" && <small>The escrow locks exactly the agreed fee.</small>}
              </label>
            )}
            {model !== "fixed" && (
              <label>
                Max per 1,000 views (USD)
                <input type="number" min="0.01" step="0.01" value={form.max_cpm} onChange={(e) => set("max_cpm", e.target.value)} required />
              </label>
            )}
          </div>
          <Notice>
            Up to {usd(maxEscrow)} can be locked in escrow ({form.max_creators} × {usd(form.cap_per_creator)}). You sign each Payment Mandate yourself
            with wallet {user.wallet.slice(0, 8)}…, and unspent money is refunded to that wallet.
          </Notice>
        </Card>

        <div className="row">
          <button className="btn btn-primary" disabled={busy || lengthInvalid || disclosureProblems.length > 0}>
            {busy ? "Launching agent…" : "Launch advertiser agent"}
          </button>
          <ErrorText error={error} />
        </div>
      </form>
    </div>
  );
}
