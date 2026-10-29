import { useState } from "react";
import { DELIVERABLE_LABEL, docSize, videoLength } from "../format";

/** Read-only view of a campaign's content rules: format, video length and the full brief document. */
export function Requirements({
  deliverable,
  min,
  max,
  text,
  hash,
}: {
  deliverable: string;
  min: number;
  max: number;
  text: string;
  hash?: string;
}) {
  const [open, setOpen] = useState(false);
  const { words, pages } = docSize(text);
  const long = text.length > 600;
  return (
    <div className="requirements">
      <dl className="kv">
        <dt>Format</dt>
        <dd>{DELIVERABLE_LABEL[deliverable] ?? deliverable}</dd>
        <dt>Video length</dt>
        <dd>{videoLength(min, max)}</dd>
        <dt>Requirements</dt>
        <dd>{text ? `${words.toLocaleString()} words · ~${Math.max(pages, 0.1).toFixed(1)} pages` : "None"}</dd>
        {hash && (
          <>
            <dt>Signed hash</dt>
            <dd className="mono" title={hash}>
              {hash.slice(0, 10)}…{hash.slice(-6)}
            </dd>
          </>
        )}
      </dl>
      {text && (
        <>
          <div className={`doc ${long && !open ? "doc-clipped" : ""}`}>{text}</div>
          {long && (
            <button type="button" className="btn btn-ghost" onClick={() => setOpen((o) => !o)}>
              {open ? "Show less" : "Read full requirements"}
            </button>
          )}
        </>
      )}
    </div>
  );
}
