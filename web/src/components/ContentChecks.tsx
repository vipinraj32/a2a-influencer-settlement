import { useState } from "react";
import { api } from "../api";
import { when } from "../format";
import type { ContentCheck, ContentReport } from "../types";
import { ErrorText } from "./ui";

export function checkText(c: ContentCheck): string {
  if (c.kind === "spoken_mention") {
    const range = c.max_count && c.max_count > c.min_count ? `${c.min_count}–${c.max_count}` : `at least ${c.min_count}`;
    return `Say “${c.phrase}” out loud ${range} times${c.aliases.length ? ` (also counts: ${c.aliases.join(", ")})` : ""}`;
  }
  return `Show the ${c.phrase} logo clearly on screen`;
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 30) || "check";

/** Advertiser: the checks the content agent runs inside the posted video. Suggested from the brief, edited here. */
export function ContentChecksEditor({ value, onChange, requirements, brand }: { value: ContentCheck[]; onChange: (v: ContentCheck[]) => void; requirements: string; brand: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");

  async function suggest() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.suggestChecks(requirements, brand || "the brand");
      onChange(r.checks);
      setNote(
        r.checks.length
          ? `${r.method === "llm" ? "The agent" : "Keyword rules"} found ${r.checks.length} check(s) in your brief.${r.vision ? "" : " No vision model is configured: logo checks will go to your review."}`
          : "Nothing checkable in the video was found in the brief. Add a check below.",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const update = (i: number, patch: Partial<ContentCheck>) => onChange(value.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  const add = (kind: ContentCheck["kind"]) => {
    const base = kind === "spoken_mention" ? `say_${slug(brand)}` : `logo_${slug(brand)}`;
    const id = value.some((c) => c.id === base) ? `${base}_${value.length + 1}`.slice(0, 30) : base;
    onChange([...value, { id, kind, phrase: brand, aliases: [], min_count: kind === "spoken_mention" ? 3 : 1, max_count: null, description: "" }]);
  };

  return (
    <div className="form content-checks">
      <div className="row spread">
        <strong className="small">In-video checks</strong>
        <span className="row">
          <button type="button" className="btn btn-ghost btn-sm" onClick={suggest} disabled={busy || !requirements.trim()}>
            {busy ? "Reading your brief…" : "Suggest from the brief"}
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => add("spoken_mention")}>
            + Spoken mention
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => add("logo_visible")}>
            + Logo on screen
          </button>
        </span>
      </div>
      <ErrorText error={error} />
      {note && <p className="small muted">{note}</p>}
      {value.map((c, i) => (
        <div key={c.id + i} className="check-row">
          <span className="chip">{c.kind === "spoken_mention" ? "Spoken" : "Logo"}</span>
          <label>
            {c.kind === "spoken_mention" ? "Name to hear" : "Brand whose logo to see"}
            <input value={c.phrase} onChange={(e) => update(i, { phrase: e.target.value })} minLength={2} maxLength={60} required />
          </label>
          {c.kind === "spoken_mention" && (
            <>
              <label>
                At least
                <input type="number" min={1} max={50} value={c.min_count} onChange={(e) => update(i, { min_count: Math.max(1, Number(e.target.value)) })} />
              </label>
              <label>
                Up to (optional)
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={c.max_count ?? ""}
                  onChange={(e) => update(i, { max_count: e.target.value ? Number(e.target.value) : null })}
                />
              </label>
              <label>
                Other spellings
                <input
                  value={c.aliases.join(", ")}
                  onChange={(e) => update(i, { aliases: e.target.value.split(",").map((a) => a.trim()).filter(Boolean).slice(0, 5) })}
                  placeholder="fit fuel"
                />
              </label>
            </>
          )}
          <button type="button" className="link" onClick={() => onChange(value.filter((_, j) => j !== i))} aria-label="Remove check">
            Remove
          </button>
        </div>
      ))}
      {value.some((c) => c.kind === "logo_visible") && <p className="small muted">Logo checks only advise: you confirm them in your review.</p>}
    </div>
  );
}

/** Creator: what the content agent will check in the video. */
export function ContentChecksList({ checks }: { checks: ContentCheck[] }) {
  if (!checks?.length) return null;
  return (
    <div className="small">
      <strong>The content agent checks in your video:</strong>
      <ul className="bullets">
        {checks.map((c) => (
          <li key={c.id}>
            {checkText(c)}
            {c.kind === "spoken_mention" ? " — counted from your captions; add your own captions if the automatic ones mishear the name." : " — AI looks at frames YouTube publishes; the advertiser confirms."}
          </li>
        ))}
      </ul>
    </div>
  );
}

const STATUS = { pass: "✓", fail: "✗", unverified: "?" } as const;

/** Both sides: what the content agent extracted from the video and how each check came out. */
export function ContentReportView({ report }: { report: ContentReport }) {
  const transcript =
    report.transcript === "unavailable"
      ? `no captions (${report.transcript_note || "unavailable"})`
      : `${report.transcript === "manual_captions" ? "creator's captions" : "YouTube automatic captions"} (${report.transcript_language})`;
  return (
    <div className="content-report">
      <h4>Content agent</h4>
      <p className="small muted">
        Listened via {transcript} · {report.frames.length ? `${report.frames.length} frames ${report.vision === "llm" ? "checked by AI" : "not checked (no vision model)"}` : "no frames"} ·{" "}
        {when(report.analyzed_at)}
      </p>
      <ul className="checks">
        {report.results.map((r) => (
          <li key={r.check_id} className={r.status === "pass" ? "pass" : r.status === "fail" ? "fail" : "unsure"}>
            <span aria-hidden>{STATUS[r.status]}</span> {r.requirement}
            <div className="small">
              {r.detail}
              {!r.decisive && <span className="chip warn">advisory: advertiser confirms</span>}
            </div>
            {r.evidence.length > 0 && (
              <details className="small">
                <summary>Evidence ({r.evidence.length})</summary>
                <ul>
                  {r.evidence.map((e) => (
                    <li key={e}>{e.startsWith("https://") ? <a href={e} target="_blank" rel="noreferrer">{e.split("/").pop()}</a> : e}</li>
                  ))}
                </ul>
              </details>
            )}
          </li>
        ))}
      </ul>
      {report.frames.length > 0 && (
        <div className="frames">
          {report.frames.map((f) => (
            <img key={f} src={f} alt="" width={120} height={90} referrerPolicy="no-referrer" />
          ))}
        </div>
      )}
    </div>
  );
}

export const needsReview = (report: ContentReport | null | undefined) => !!report && report.results.some((r) => r.status === "unverified" || !r.decisive);
