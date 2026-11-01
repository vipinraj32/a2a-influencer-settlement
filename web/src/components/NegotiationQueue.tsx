// The campaign's creator queue: who is being negotiated with now, who is next, and how each one ended.
// The backend runs creators one after another; this view only reads that order and never starts anything.

import { useEffect, useState } from "react";
import { api } from "../api";
import { termsText, usd } from "../format";
import { subscribeLive } from "../live";
import type { CompensationModel, NegotiationProgress, NegotiationSession, SessionStatus } from "../types";
import { Avatar } from "./ui";

export const SESSION_LABEL: Record<SessionStatus, string> = {
  QUEUED: "Queued",
  RUNNING: "Negotiating",
  AGREED: "Agreed",
  REJECTED: "No deal",
  TIMED_OUT: "Timed out",
  FAILED: "Failed",
  CANCELLED: "Cancelled",
};

const TERMINAL: SessionStatus[] = ["AGREED", "REJECTED", "TIMED_OUT", "FAILED", "CANCELLED"];

/** The queue as stored, kept current by the progress events the backend pushes after every session change. */
export function useNegotiationQueue(campaignId: string) {
  const [progress, setProgress] = useState<NegotiationProgress | null>(null);
  const [sessions, setSessions] = useState<NegotiationSession[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stopped = false;
    let loading = false;
    let again = false;
    const load = () => {
      if (loading) {
        again = true; // a change arrived mid-request: fetch once more when it returns
        return;
      }
      loading = true;
      api
        .campaignNegotiations(campaignId)
        .then((q) => {
          if (stopped) return;
          setProgress(q.progress);
          setSessions(q.sessions);
          setError(null);
        })
        .catch((e) => !stopped && setError(e instanceof Error ? e.message : String(e)))
        .finally(() => {
          loading = false;
          if (again && !stopped) {
            again = false;
            load();
          }
        });
    };
    const unsubscribe = subscribeLive((e) => {
      if (stopped || e.campaign_id !== campaignId || e.type !== "negotiation_progress") return;
      if (e.progress) setProgress(e.progress); // counts update at once; the rows follow from the store
      load();
    }, load);
    load();
    return () => {
      stopped = true;
      unsubscribe();
    };
  }, [campaignId]);

  return { progress, sessions, error };
}

function offerLine(s: NegotiationSession, model: CompensationModel): string {
  if (s.status === "AGREED" && s.final_offer) return termsText(s.final_offer, model);
  if (s.latest_offer) return `${s.latest_offer.proposer === "influencer" ? "Ask" : "Offer"}: ${termsText(s.latest_offer, model)}`;
  return s.status === "QUEUED" ? "Waiting for its turn" : "";
}

export function NegotiationQueue(p: {
  progress: NegotiationProgress | null;
  sessions: NegotiationSession[];
  name: (creatorId: string) => string;
  avatar: (creatorId: string) => string | undefined;
  model: CompensationModel;
  focus: string | null;
  following: boolean;
  all: boolean;
  onFocus: (negotiationId: string) => void;
  onFollow: () => void;
  onAll: () => void;
  canCancel: boolean;
}) {
  const [cancelling, setCancelling] = useState<string | null>(null);
  const { progress, sessions } = p;
  if (!progress || (sessions.length === 0 && progress.workflow_status === "starting")) {
    return (
      <div className="nq">
        <p className="nq-status">
          <span className="live-dot on" /> Starting campaign negotiations…
        </p>
      </div>
    );
  }
  if (sessions.length === 0) return null;
  const running = progress.workflow_status === "running";
  const done = progress.completed_count;

  async function cancel() {
    setCancelling("Stopping…");
    try {
      await api.cancelCampaignNegotiations(progress!.campaign_id);
      setCancelling(null);
    } catch (e) {
      setCancelling(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="nq">
      <div className="nq-head">
        <span className="nq-status">
          {running && <span className="live-dot on" />} {done} of {progress.total_creators} negotiations completed
        </span>
        {sessions.length > 1 && (
          <button type="button" className={`nq-toggle ${p.all ? "on" : ""}`} onClick={p.onAll} aria-pressed={p.all}>
            All
          </button>
        )}
      </div>
      <div
        className="nq-bar"
        role="progressbar"
        aria-label="Negotiations completed"
        aria-valuemin={0}
        aria-valuemax={progress.total_creators}
        aria-valuenow={done}
      >
        <span style={{ width: `${progress.total_creators ? (done / progress.total_creators) * 100 : 0}%` }} />
      </div>
      <ol className="nq-list">
        {sessions.map((s) => (
          <li key={s.negotiation_id}>
            <button
              type="button"
              className={`nq-row nq-${s.status.toLowerCase()} ${!p.all && p.focus === s.negotiation_id ? "is-focus" : ""}`}
              onClick={() => p.onFocus(s.negotiation_id)}
              disabled={!s.started_at}
              aria-current={s.status === "RUNNING" ? "step" : undefined}
            >
              <Avatar src={p.avatar(s.creator_id)} name={p.name(s.creator_id)} size={24} />
              <span className="nq-who">
                <strong>{p.name(s.creator_id)}</strong>
                <small>{offerLine(s, p.model)}</small>
              </span>
              <span className="nq-meta">
                <span className={`nq-badge nq-badge-${s.status.toLowerCase()}`}>{SESSION_LABEL[s.status]}</span>
                {s.current_round > 0 && (
                  <small>
                    round {s.current_round}/{s.maximum_rounds}
                  </small>
                )}
              </span>
            </button>
          </li>
        ))}
      </ol>
      <div className="nq-actions">
        {!p.following && running && !p.all && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={p.onFollow}>
            Follow the live conversation
          </button>
        )}
        {running && p.canCancel && progress.queued_count + (progress.current_negotiation_id ? 1 : 0) > 0 && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={cancel} disabled={cancelling === "Stopping…"}>
            Stop remaining negotiations
          </button>
        )}
        {cancelling && <span className="muted small">{cancelling}</span>}
      </div>
    </div>
  );
}

/** Totals from persisted outcomes only. Agreed spend is the most the agreed deals can pay (their caps); nothing is paid
 * until the advertiser signs. Proposed spend is the last offer on the table with every creator, agreed or not. */
export function NegotiationSummary({
  progress,
  sessions,
  name,
  model,
}: {
  progress: NegotiationProgress;
  sessions: NegotiationSession[];
  name: (id: string) => string;
  model: CompensationModel;
}) {
  const agreed = sessions.filter((s) => s.status === "AGREED" && s.final_offer);
  const ended = sessions.filter((s) => TERMINAL.includes(s.status)).length;
  if (ended === 0 || progress.workflow_status === "running") return null;
  return (
    <div className="nq-summary">
      <h4>{progress.workflow_status === "cancelled" ? "Negotiations stopped" : "All negotiations completed"}</h4>
      <dl>
        <div>
          <dt>Creators contacted</dt>
          <dd>{sessions.filter((s) => s.started_at).length}</dd>
        </div>
        <div>
          <dt>Agreements</dt>
          <dd>{progress.agreed_count}</dd>
        </div>
        <div>
          <dt>No deal</dt>
          <dd>{progress.rejected_count}</dd>
        </div>
        <div>
          <dt>Failed or timed out</dt>
          <dd>{progress.failed_count}</dd>
        </div>
        {progress.cancelled_count > 0 && (
          <div>
            <dt>Cancelled</dt>
            <dd>{progress.cancelled_count}</dd>
          </div>
        )}
        <div>
          <dt>Agreed spend (max)</dt>
          <dd>{usd(progress.agreed_spend)}</dd>
        </div>
        <div>
          <dt>Proposed spend, all offers</dt>
          <dd className="muted">{usd(progress.proposed_spend)}</dd>
        </div>
      </dl>
      {agreed.length > 0 && (
        <ul className="nq-agreed">
          {agreed.map((s) => (
            <li key={s.negotiation_id}>
              <span>{name(s.creator_id)}</span>
              <span className="mono small">{termsText(s.final_offer!, model)}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="muted small">Agreed is not paid: each deal is paid only after you sign its payment in your wallet.</p>
    </div>
  );
}
