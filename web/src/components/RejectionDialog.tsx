import { useEffect, useRef, useState } from "react";
import { humanize, when } from "../format";
import type { ProofReview } from "../types";

const seenKey = (dealId: string, review: ProofReview) => `zx-rejection-seen:${dealId}:${review.at}`;

function seen(key: string): boolean {
  try {
    return localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

/** Pops up once per rejection (live, as soon as the verifier or the advertiser decides): why, and how to fix it. */
export function RejectionDialog({ dealId, review, onFix }: { dealId: string; review: ProofReview; onFix: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const key = seenKey(dealId, review);
  const [open, setOpen] = useState(() => !seen(key));

  useEffect(() => {
    setOpen(!seen(key));
  }, [key]);
  useEffect(() => {
    const d = ref.current;
    if (open && d && !d.open) d.showModal();
  }, [open]);

  const close = () => {
    try {
      localStorage.setItem(key, "1");
    } catch {
      // private mode: it simply shows again next visit
    }
    ref.current?.close();
    setOpen(false);
  };
  if (!open) return null;
  const who = review.decided_by === "advertiser" ? "The brand sent your video back" : "Your video wasn't accepted";
  return (
    <dialog ref={ref} className="reject-dialog" onClose={close} aria-labelledby="reject-title">
      <h2 id="reject-title">{who}</h2>
      <p>{review.summary}</p>
      <ol className="reject-issues">
        {review.issues.map((i) => (
          <li key={i.check}>
            <strong>{humanize(i.check.split(":")[0])}</strong>
            <div className="muted small">{i.problem}</div>
            <div className="small">
              <span className="muted">Fix: </span>
              {i.fix}
            </div>
          </li>
        ))}
      </ol>
      <p className="small">
        {review.can_resubmit && review.resubmit_by ? (
          <>
            Resubmit by <strong>{when(review.resubmit_by)}</strong>. The money stays in escrow until then.
          </>
        ) : (
          "This can't be fixed before the deadline; the escrow refunds the brand when it passes."
        )}
      </p>
      <div className="row reject-actions">
        {review.can_resubmit && (
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => {
              close();
              onFix();
            }}
          >
            Fix and resubmit
          </button>
        )}
        <button type="button" className="btn btn-ghost" onClick={close}>
          Close
        </button>
      </div>
    </dialog>
  );
}
