import { useState } from "react";
import { api } from "../api";
import { useSession } from "../session";
import { DELIVERABLE_LABEL, videoLength, when } from "../format";
import type { InfluencerDeal } from "../types";
import { ContentChecksList } from "./ContentChecks";
import { ActionButton, Notice } from "./ui";

/** The creator posts the video, then submits its link; the verifier decides from YouTube's data. */
export function ProofForm({ deal, onDone }: { deal: InfluencerDeal; onDone: () => void }) {
  const { status } = useSession();
  const rejected = deal.state === "PROOF_REJECTED" ? deal.proof : null;
  const [url, setUrl] = useState(rejected ? `https://youtube.com/watch?v=${rejected.video_id}` : "");
  const [inVideo, setInVideo] = useState(false);
  const [confirming, setConfirming] = useState(false); // submitting starts verification: ask once before it does
  const deadline = deal.terms?.publish_by;
  const late = deadline ? new Date(deadline).getTime() < Date.now() : false;
  if (late) return <Notice tone="bad">The publish deadline ({when(deadline)}) has passed: no more submissions; the escrow refunds the advertiser.</Notice>;
  const tags = deal.terms?.required_tags.join(" ") ?? "#ad";
  const rules = deal.terms?.disclosure_policies ?? [];
  return (
    <div className="proof-form">
      <ul className="proof-musts">
        <li>
          <span className="muted">{rejected ? `Submission #${rejected.attempt + 1} by` : "Post by"}</span> <strong>{when(deadline)}</strong>
        </li>
        <li>
          <span className="muted">Format</span> {DELIVERABLE_LABEL[deal.campaign.deliverable] ?? deal.campaign.deliverable}
          {deal.campaign.max_duration_seconds > 0 && `, ${videoLength(deal.campaign.min_duration_seconds, deal.campaign.max_duration_seconds)}`}
        </li>
        <li>
          <span className="muted">Description</span> <code>{deal.campaign.brand_handle}</code> <code>{tags}</code>
        </li>
        {rules.length > 0 && (
          <li title="Put the label in the title or at the very start of the description, and show or say it in the video">
            <span className="muted">Disclosure</span> {rules.join(", ")} · label at the start + in the video
          </li>
        )}
      </ul>
      <ContentChecksList checks={deal.campaign.content_checks ?? []} />
      <label className="check" title="YouTube's API cannot check this, so it is recorded as your statement">
        <input type="checkbox" checked={inVideo} onChange={(e) => setInVideo(e.target.checked)} />
        Disclosure is shown or said in the video (your statement)
      </label>
      {!status?.verifier_online && <Notice tone="warn">The verifier is offline: the server needs YOUTUBE_API_KEY.</Notice>}
      <div className="row">
        <input
          className="grow"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            setConfirming(false);
          }}
          placeholder="https://youtube.com/shorts/…"
          maxLength={300}
          aria-label="Video link"
        />
        {!confirming ? (
          <button type="button" className="btn btn-primary" disabled={!url.trim()} onClick={() => setConfirming(true)}>
            {rejected ? "Resubmit for verification" : "Submit for verification"}
          </button>
        ) : (
          <>
            <ActionButton
              onClick={async () => {
                await api.submitProof(deal.id, url.trim(), inVideo ? { in_video_disclosure: true } : {});
                setUrl("");
                setConfirming(false);
                onDone();
              }}
            >
              Confirm and submit
            </ActionButton>
            <button type="button" className="btn btn-ghost" onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </>
        )}
      </div>
      {confirming && <p className="small muted">The verifier checks this video against the signed terms. You can resubmit only if it is rejected.</p>}
    </div>
  );
}
