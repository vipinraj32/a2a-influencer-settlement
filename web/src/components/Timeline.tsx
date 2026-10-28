import { ACTOR_LABEL, humanize, when } from "../format";
import type { AuditEvent } from "../types";
import { Empty } from "./ui";

const HIDDEN = new Set(["signature", "attestation"]);

function summarize(detail: Record<string, unknown>): string {
  return Object.entries(detail)
    .filter(([k, v]) => !HIDDEN.has(k) && v !== null && v !== "" && !(Array.isArray(v) && v.length === 0))
    .map(([k, v]) => `${humanize(k)}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(" · ");
}

export function Timeline({ events, limit }: { events: AuditEvent[]; limit?: number }) {
  if (!events.length) return <Empty>Nothing has happened yet.</Empty>;
  const shown = [...events].reverse().slice(0, limit);
  return (
    <ol className="timeline">
      {shown.map((e, i) => (
        <li key={`${e.at}-${i}`} className={`actor-${e.actor.split(":")[0]}`}>
          <div className="tl-head">
            <span className="tl-actor">{ACTOR_LABEL(e.actor)}</span>
            <span className="tl-kind">{humanize(e.kind)}</span>
            <time className="tl-time">{when(e.at)}</time>
          </div>
          {Object.keys(e.detail).length > 0 && <div className="tl-detail">{summarize(e.detail)}</div>}
        </li>
      ))}
    </ol>
  );
}
