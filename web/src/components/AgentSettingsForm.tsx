import { useState, type FormEvent } from "react";
import { api } from "../api";
import { useSession } from "../session";
import { NichePicker } from "./NichePicker";
import { ErrorText } from "./ui";

/** The influencer's rate card and negotiation style. Floors are private: only the creator's own agent sees them. */
export function AgentSettingsForm({ onSaved, submitLabel = "Save agent settings" }: { onSaved?: () => void; submitLabel?: string }) {
  const { me, setMe } = useSession();
  const u = me!.user;
  const [niches, setNiches] = useState<string[]>(u.niches?.length ? u.niches : u.niche ? [u.niche] : []);
  const [fixedMin, setFixedMin] = useState(u.rate_card?.fixed_min ?? "");
  const [cpmMin, setCpmMin] = useState(u.rate_card?.cpm_min ?? "");
  const [preferred, setPreferred] = useState(u.rate_card?.preferred_fixed ?? "");
  const [persona, setPersona] = useState(u.persona);
  const [active, setActive] = useState(u.rate_card ? u.agent_active : true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(false);
    if (niches.length === 0) {
      setError("Choose at least one niche (analyze your channel or add one).");
      setBusy(false);
      return;
    }
    try {
      setMe(await api.saveInfluencer({ niches, fixed_min: fixedMin, cpm_min: cpmMin, preferred_fixed: preferred, persona, agent_active: active }));
      setSaved(true);
      onSaved?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="form" onSubmit={submit}>
      <NichePicker value={niches} onChange={setNiches} initial={u.niche_analysis} autoAnalyze={!u.rate_card} />
      <div className="grid-2">
        <label>
          Opening ask (fixed fee, USD)
          <input type="number" min="0.01" step="0.01" value={preferred} onChange={(e) => setPreferred(e.target.value)} required />
          <small>Your agent counters from here.</small>
        </label>
        <label>
          Floor: minimum fixed fee (USD)
          <input type="number" min="0.01" step="0.01" value={fixedMin} onChange={(e) => setFixedMin(e.target.value)} required />
          <small>Your agent never agrees below this, whatever the LLM suggests.</small>
        </label>
        <label>
          Floor: minimum per 1,000 views (USD)
          <input type="number" min="0" step="0.01" value={cpmMin} onChange={(e) => setCpmMin(e.target.value)} required />
        </label>
      </div>
      <label>
        Negotiation style (optional)
        <textarea value={persona} onChange={(e) => setPersona(e.target.value)} maxLength={500} rows={2} placeholder="friendly, flexible on CPM, firm on fixed fee" />
      </label>
      <label className="check">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
        Agent is live: advertisers' agents can discover and negotiate with it
      </label>
      <div className="row">
        <button className="btn btn-primary" disabled={busy}>
          {busy ? "Saving…" : submitLabel}
        </button>
        {saved && <span className="muted">Saved.</span>}
      </div>
      <ErrorText error={error} />
    </form>
  );
}
