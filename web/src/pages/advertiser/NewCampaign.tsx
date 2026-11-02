import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { api } from "../../api";
import { ContentChecksEditor } from "../../components/ContentChecks";
import { Card, ErrorText, Notice } from "../../components/ui";
import { MODEL_LABEL, usd } from "../../format";
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
  const [niches, setNiches] = useState<string[]>([]);
  useEffect(() => {
    api.niches().then((r) => setNiches(r.niches), () => setNiches([]));
  }, []);
  const [form, setForm] = useState<CampaignInput>({
    name: "",
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
    content_checks: [],
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
  const [existingNames, setExistingNames] = useState<Set<string>>(new Set());
  useEffect(() => {
    api.campaigns().then(
      (cs) => setExistingNames(new Set(cs.filter((c) => c.spec.name).map((c) => c.spec.name.split(/\s+/).filter(Boolean).join(" ").toLowerCase()))),
      () => setExistingNames(new Set()),
    );
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
  // the server refuses a name the advertiser already used; say so while they type
  const tidyName = (v: string) => v.split(/\s+/).filter(Boolean).join(" ").toLowerCase();
  const nameTaken = form.name.trim() !== "" && existingNames.has(tidyName(form.name));

  async function draft() {
    setDrafting(true);
    setError(null);
    try {
      const d = await api.parseBrief(form.brief);
      setForm((f) => ({
        ...f,
        ...d,
        name: f.name.trim() ? f.name : d.name, // the AI only suggests a name when none was typed
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

  const submitted = useRef<{ body: string; key: string } | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (nameTaken) {
      setError("You already have a campaign with this name. Pick another one.");
      return;
    }
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
      // the same form submitted again (double-click, retry after a dropped response) reuses its key: one campaign only
      const sent = JSON.stringify(body);
      if (submitted.current?.body !== sent) submitted.current = { body: sent, key: newKey() };
      const created = await api.createCampaign(body, submitted.current.key);
      navigate(`/advertiser/campaigns/${created.spec.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  const maxEscrow = Number(form.cap_per_creator || 0) * form.max_creators;
  const lengthCap = form.deliverable === "youtube_short" ? SHORTS_MAX_SECONDS : MAX_VIDEO_SECONDS;

  return (
    <div className="stack narrow">
      <h1>New campaign</h1>
      <Card title="Campaign">
        <label className="form">
          Campaign name
          <input
            form="new-campaign"
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
            required
            maxLength={80}
            placeholder="e.g. Diwali protein launch"
            aria-invalid={nameTaken || undefined}
          />
        </label>
        {nameTaken && <p className="error small">You already have a campaign with this name.</p>}
        <label className="form">
          Brief
          <textarea
            rows={3}
            value={form.brief}
            onChange={(e) => set("brief", e.target.value)}
            maxLength={2000}
            placeholder="e.g. 3 fitness creators, 30s Short with #ad, about $120"
          />
        </label>
        <div className="row">
          <button type="button" className="btn btn-secondary" disabled={drafting || form.brief.length < 10 || !status?.llm} onClick={draft}>
            {drafting ? "Drafting…" : "Fill the fields with AI"}
          </button>
          {!status?.llm && <span className="muted small">AI is not set up on the server.</span>}
        </div>
      </Card>

      <form id="new-campaign" onSubmit={submit} className="stack">
        <Card title="Brand and creators">
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
              <input value={form.niche} onChange={(e) => set("niche", e.target.value)} required minLength={2} list="niche-options" />
              <datalist id="niche-options">
                {niches.map((n) => (
                  <option key={n} value={n} />
                ))}
              </datalist>
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

        <Card title="Content">
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
          {lengthInvalid && <p className="small error">The minimum is longer than the maximum.</p>}

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
              placeholder="Key messages, scenes, tone, things to avoid"
            />
            {form.requirements.length > MAX_REQUIREMENTS_CHARS * 0.9 && (
              <p className="small error">
                {form.requirements.length.toLocaleString()} / {MAX_REQUIREMENTS_CHARS.toLocaleString()} characters
              </p>
            )}
          </div>
          <ContentChecksEditor value={form.content_checks} onChange={(v) => set("content_checks", v)} requirements={form.requirements} brand={form.advertiser} />
        </Card>

        <Card title="Deal terms">
          <div className="grid-3 form">
            <label>
              Payment type
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
            </label>
            <label>
              Negotiation rounds
              <input type="number" min={1} max={8} value={form.max_rounds} onChange={(e) => set("max_rounds", Number(e.target.value))} />
            </label>
          </div>
        </Card>

        <Card title="Disclosure">
          <div className="form">
            <div className="row">
              {policies.map((p) => (
                <label key={p.jurisdiction} className="check">
                  <input type="checkbox" checked={form.jurisdictions.includes(p.jurisdiction)} onChange={() => toggleJurisdiction(p.jurisdiction)} />
                  {p.jurisdiction}
                </label>
              ))}
            </div>
            <label>
              Required tags
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
              I review each post before paying
            </label>
          </div>
          {disclosureProblems.length > 0 && <Notice tone="warn">{disclosureProblems.join(". ")}.</Notice>}
          {form.jurisdictions.length > 0 && (
            <details className="small muted">
              <summary>Rules</summary>
              {policies
                .filter((p) => form.jurisdictions.includes(p.jurisdiction))
                .map((p) => (
                  <p key={p.id}>
                    <strong>{p.jurisdiction}</strong>: approved labels {p.approved_labels.join(", ")}; label in the title or first {p.placement_chars} characters
                    {p.in_video_required ? "; creator confirms an in-video disclosure" : ""}.
                  </p>
                ))}
              {disclaimer && <p>{disclaimer}</p>}
            </details>
          )}
        </Card>

        <Card title="Budget">
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
              Max per creator (USD)
              <input type="number" min="0.01" step="0.01" value={form.cap_per_creator} onChange={(e) => set("cap_per_creator", e.target.value)} required />
            </label>
            {model !== "cpm" && (
              <label>
                Max fixed fee (USD)
                <input type="number" min="0.01" step="0.01" value={form.max_fixed} onChange={(e) => set("max_fixed", e.target.value)} required />
              </label>
            )}
            {model !== "fixed" && (
              <label>
                Max per 1,000 views (USD)
                <input type="number" min="0.01" step="0.01" value={form.max_cpm} onChange={(e) => set("max_cpm", e.target.value)} required />
              </label>
            )}
          </div>
          <p className="small muted">Up to {usd(maxEscrow)} in escrow. You sign each payment; unspent money returns to your wallet.</p>
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

// randomUUID needs a secure context (https or localhost); a LAN address over plain http falls back
const newKey = () => (typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);
