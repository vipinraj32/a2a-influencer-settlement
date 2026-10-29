import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { when } from "../format";
import type { NicheAnalysis } from "../types";
import { ErrorText } from "./ui";

const pct = (v: number) => `${Math.round(v * 100)}%`;

/** The creator's niches: suggested from their YouTube uploads, confirmed or edited by the creator. Primary first. */
export function NichePicker({
  value,
  onChange,
  initial,
  autoAnalyze,
}: {
  value: string[];
  onChange: (niches: string[]) => void;
  initial: NicheAnalysis | null;
  autoAnalyze: boolean;
}) {
  const [analysis, setAnalysis] = useState<NicheAnalysis | null>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [custom, setCustom] = useState("");
  const [taxonomy, setTaxonomy] = useState<string[]>([]);
  const started = useRef(false);
  const max = 5;

  useEffect(() => {
    api.niches().then((r) => setTaxonomy(r.niches), () => setTaxonomy([]));
  }, []);

  async function analyze() {
    setBusy(true);
    setError(null);
    try {
      const a = await api.analyzeNiches();
      setAnalysis(a);
      if (value.length === 0 && a.suggestions.length) onChange(a.suggestions.slice(0, 3).map((s) => s.niche));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  // First visit without niches: read the channel straight away so the creator starts from a suggestion
  useEffect(() => {
    if (autoAnalyze && !started.current && !initial && value.length === 0) {
      started.current = true;
      void analyze();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoAnalyze]);

  const toggle = (n: string) => onChange(value.includes(n) ? value.filter((v) => v !== n) : value.length < max ? [...value, n] : value);
  const makePrimary = (n: string) => onChange([n, ...value.filter((v) => v !== n)]);
  function addCustom() {
    const n = custom.trim().toLowerCase();
    if (n.length >= 2 && n.length <= 40 && !value.includes(n) && value.length < max) onChange([...value, n]);
    setCustom("");
  }

  const suggested = new Set(analysis?.suggestions.map((s) => s.niche));
  const others = taxonomy.filter((n) => !suggested.has(n));

  return (
    <div className="niche-picker">
      <div className="row spread">
        <strong>Your niches</strong>
        <button type="button" className="btn btn-ghost btn-sm" onClick={analyze} disabled={busy}>
          {busy ? "Analyzing your channel…" : analysis ? "Re-analyze my channel" : "Analyze my channel"}
        </button>
      </div>
      <ErrorText error={error} />

      {value.length > 0 ? (
        <div className="chips">
          {value.map((n, i) => (
            <span key={n} className={`chip ${i === 0 ? "good" : ""}`}>
              {n}
              {i === 0 ? " · primary" : (
                <button type="button" className="link" onClick={() => makePrimary(n)} title="Make primary">
                  ↑
                </button>
              )}
              <button type="button" className="link" onClick={() => toggle(n)} aria-label={`Remove ${n}`}>
                ×
              </button>
            </span>
          ))}
        </div>
      ) : (
        <p className="small muted">No niche chosen yet.</p>
      )}

      {analysis && (
        <div className="niche-suggestions">
          <p className="small muted">
            Suggested from your latest {analysis.videos_analyzed} uploads{analysis.method === "llm+rules" ? " (AI-ranked, keyword-checked)" : " (keyword match)"} ·{" "}
            {when(analysis.analyzed_at)}. {analysis.note}
          </p>
          {analysis.suggestions.map((s) => (
            <label key={s.niche} className="check niche-row" title={s.reason || undefined}>
              <input type="checkbox" checked={value.includes(s.niche)} onChange={() => toggle(s.niche)} />
              <span>
                <strong>{s.niche}</strong>{" "}
                <span className="muted small">
                  {s.matched_videos} of {analysis.videos_analyzed} uploads ({pct(s.video_share)}){s.channel_signal ? " · channel topic" : ""}
                  {s.llm_confidence !== null ? ` · AI confidence ${pct(s.llm_confidence)}` : ""}
                </span>
                {s.keywords.length > 0 && <div className="small muted">keywords: {s.keywords.join(", ")}</div>}
                {s.examples.length > 0 && <div className="small muted">e.g. “{s.examples[0]}”</div>}
              </span>
            </label>
          ))}
        </div>
      )}

      <div className="row">
        <select value="" onChange={(e) => e.target.value && toggle(e.target.value)} aria-label="Add a niche">
          <option value="">Add a niche…</option>
          {others
            .filter((n) => !value.includes(n))
            .map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
        </select>
        <input
          className="grow"
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addCustom();
            }
          }}
          placeholder="or type your own"
          maxLength={40}
        />
        <button type="button" className="btn btn-ghost btn-sm" onClick={addCustom}>
          Add
        </button>
      </div>
      <small>
        Up to {max}. Advertiser agents contact you for campaigns in any of these niches; the primary one counts as the strongest fit. The analysis only
        suggests: nothing changes until you save.
      </small>
    </div>
  );
}
