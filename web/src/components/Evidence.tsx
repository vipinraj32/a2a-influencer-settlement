import { SOURCE_LABEL, humanize, num, when } from "../format";
import type { EvidenceItem } from "../types";

function show(v: unknown): string {
  if (v === null || v === undefined) return "Unknown";
  if (typeof v === "number") return num(v);
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}

/** Creator metrics with their source. Unknown is shown as unknown, never estimated. */
export function EvidenceTable({ items }: { items: EvidenceItem[] }) {
  return (
    <table className="table compact">
      <thead>
        <tr>
          <th>Metric</th>
          <th>Value</th>
          <th>Source</th>
          <th>Observed</th>
        </tr>
      </thead>
      <tbody>
        {items.map((i) => (
          <tr key={i.metric} title={i.limitations}>
            <td>{humanize(i.metric)}</td>
            <td className={i.value === null ? "muted" : ""}>{show(i.value)}</td>
            <td>
              <span className={`chip ${i.verified ? "good" : i.source === "unavailable" ? "" : "warn"}`}>{SOURCE_LABEL[i.source] ?? i.source}</span>
            </td>
            <td className="small muted">{i.observed_at ? when(i.observed_at) : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
