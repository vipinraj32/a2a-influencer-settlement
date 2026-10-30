import { humanize, when } from "../format";
import type { Proof, ProofReview } from "../types";
import { Notice } from "./ui";

function left(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "deadline passed";
  const h = Math.floor(ms / 3_600_000);
  return h >= 48 ? `${Math.floor(h / 24)} days left` : h >= 1 ? `${h} h left` : `${Math.max(1, Math.floor(ms / 60_000))} min left`;
}

/** Why a submission was not accepted: same view for the creator (with what to fix) and the advertiser. */
export function ProofReviewCard({ review, viewer }: { review: ProofReview; viewer: "creator" | "advertiser" }) {
  const who = review.decided_by === "advertiser" ? (viewer === "advertiser" ? "You sent this post back" : "The advertiser sent this post back") : "The verifier rejected this post";
  return (
    <div className="proof-review">
      <Notice tone={review.can_resubmit ? "warn" : "bad"}>
        <strong>{who}</strong> · {when(review.at)}
        <p>{review.summary}</p>
        {review.can_resubmit && review.resubmit_by ? (
          <p className="small">
            {viewer === "creator" ? "You can resubmit" : "The creator can resubmit"} until <strong>{when(review.resubmit_by)}</strong> ({left(review.resubmit_by)}).
            Unpaid funds stay in escrow until then; after the deadline they are refunded.
          </p>
        ) : (
          <p className="small">A resubmission cannot fix this before the deadline; the escrow refunds the advertiser when it passes.</p>
        )}
      </Notice>
      <ol className="issues">
        {review.issues.map((i) => (
          <li key={i.check}>
            <strong>{humanize(i.check.split(":")[0])}</strong>
            {i.check.includes(":") && <span className="chip">{i.check.split(":")[1]}</span>}
            <div>{i.problem}</div>
            <div className="small">
              <span className="muted">{viewer === "creator" ? "How to fix: " : "Creator's fix: "}</span>
              {i.fix}
            </div>
          </li>
        ))}
      </ol>
      <p className="small muted">
        The checks decide; {review.explained_by === "llm" ? "the agent wrote the summary from them" : "the explanation is generated from them"}.
      </p>
    </div>
  );
}

/** Earlier submissions, newest first. */
export function ProofHistory({ history }: { history: Proof[] }) {
  if (history.length === 0) return null;
  return (
    <details className="proof-history">
      <summary>
        {history.length} earlier submission{history.length > 1 ? "s" : ""}
      </summary>
      <ul className="small">
        {[...history].reverse().map((p) => (
          <li key={`${p.attempt}-${p.submitted_at}`}>
            #{p.attempt} ·{" "}
            <a href={`https://youtube.com/watch?v=${p.video_id}`} target="_blank" rel="noreferrer">
              {p.video_id}
            </a>{" "}
            · {when(p.submitted_at)} · {p.review ? p.review.summary : `${p.checks.filter((c) => !c.passed).length} failed checks`}
          </li>
        ))}
      </ul>
    </details>
  );
}
