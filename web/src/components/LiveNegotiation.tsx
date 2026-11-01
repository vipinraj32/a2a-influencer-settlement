import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { DELIVERABLE_LABEL, MODEL_LABEL, termsText, usd } from "../format";
import { summarize, useNegotiationStream } from "../negotiation";
import type { CompensationModel, Deal, FeedbackCategory, FeedbackInput, FeedbackRatings, NegotiationEvent, NegotiationFeedback } from "../types";
import { A2AChat } from "./CampaignPanels";
import { Avatar, Empty, Spinner } from "./ui";
import "./negotiation.css";

type Party = "advertiser" | "influencer";

const OUTCOME_LABEL: Record<string, string> = {
  agreed: "Agreed",
  rejected: "No deal",
  timeout: "Round limit reached",
  no_reply: "No reply in time",
  walked_away: "No deal",
  screened_out: "Not contacted",
  escalated: "Needs a human",
  cancelled: "Cancelled",
  error: "Error",
};

const REPLY_LABEL: Record<string, string> = {
  counter: "Counteroffer",
  accept: "Accepted",
  reject: "Declined",
  clarify: "Question",
  escalate: "Escalated",
};

const time = (iso: string) => new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });

function offerLabel(m: NegotiationEvent): string {
  if (m.reply) return REPLY_LABEL[m.reply] ?? m.reply;
  if (/^accept/i.test(m.content)) return "Accepts";
  return m.round === 1 ? "Opening offer" : "Counteroffer";
}

// Template notes ("Offer.", "Counter-offer: $2.00 fixed + …") only repeat the offer line under them
const sayMore = (m: NegotiationEvent) => {
  const text = m.content.trim();
  if (!m.offer) return text !== "";
  return !/^(offer|counter-offer|accepted)\.?$/i.test(text) && !(/^(counter-offer|offer)[.:]/i.test(text) && /\$\d/.test(text));
};

export interface LiveNegotiationProps {
  deal: Deal;
  viewer: Party;
  creator: string;
  avatar?: string;
  brand: string;
  /** Advertiser view only: the campaign's own limits, never sent to the creator's browser. */
  limits?: { budget: string; max_fixed: string; max_cpm: string; cap: string };
  deliverable?: string;
  /** Advertiser view only: your agent's reason for its message in each round (round → text). Never shown to the creator. */
  notes?: Record<number, string>;
  /** Stacked with the campaign's other conversations: a creator header instead of the agent cards, no feedback form. */
  compact?: boolean;
  onOpen?: () => void;
}

/** The A2A conversation of one deal, live: each agent message appears as soon as the agent produced it. */
export function LiveNegotiation({ deal, viewer, creator, avatar, brand, limits, deliverable, notes, compact, onOpen }: LiveNegotiationProps) {
  const { events, status } = useNegotiationStream(deal.id, deal.state);
  const s = summarize(events);
  const end = useRef<HTMLDivElement>(null);
  const [cancelling, setCancelling] = useState<string | null>(null);
  useEffect(() => {
    if (compact) return; // several conversations share the panel: don't make them fight over the scroll position
    end.current?.scrollIntoView({ block: "nearest" }); // newer browsers return a Promise here: never hand it to React as a cleanup
  }, [events.length, compact]);

  // stacked conversations line up in the order they started (the parent is a flex column)
  const started = events.length ? Math.floor(Date.parse(events[0].timestamp) / 1000) : undefined;
  const header = compact && (
    <div className="ln-head">
      <Avatar src={avatar} name={creator} size={26} />
      <strong>{creator}</strong>
      {onOpen && (
        <button type="button" className="btn btn-ghost btn-sm" onClick={onOpen}>
          Open
        </button>
      )}
    </div>
  );
  const wrap = (body: ReactNode) =>
    compact ? (
      <section className="ln-stacked" style={started ? { order: started } : undefined} aria-label={`Conversation with ${creator}`}>
        {header}
        {body}
      </section>
    ) : (
      body
    );

  if (status === "legacy") return wrap(<A2AChat deal={deal} creator={creator} avatar={avatar} />); // ended before live transcripts existed
  if (status === "error") return wrap(<p className="error small">Could not open this conversation. Sign in again or reload the page.</p>);
  if (status === "connecting" && events.length === 0) return wrap(<Spinner label="Connecting to the negotiation…" />);

  const model = (s.model ?? "hybrid") as CompensationModel;
  const live = !s.done;
  const outcome = s.ending?.outcome;
  const progress = s.maxRounds ? Math.min(1, s.round / s.maxRounds) : 0;
  const adName = s.advertiserName ?? `${brand} agent`;
  const crName = s.creatorName ?? `${creator}'s agent`;

  async function cancel() {
    setCancelling("Stopping after the current message…");
    try {
      await api.cancelNegotiation(deal.id);
    } catch (e) {
      setCancelling(e instanceof Error ? e.message : String(e));
    }
  }

  return wrap(
    <div className="ln">
      <div className="ln-status">
        {live ? (
          <span className="ln-pill ln-live">
            <span className="live-dot on" /> Live
          </span>
        ) : (
          <span className={`ln-pill ln-${outcome ?? "done"}`}>{OUTCOME_LABEL[outcome ?? ""] ?? "Finished"}</span>
        )}
        <span className="muted small">
          {MODEL_LABEL[model]} · round {s.round || 1}
          {s.maxRounds ? ` of ${s.maxRounds}` : ""}
        </span>
      </div>
      <div
        className="ln-progress"
        role="progressbar"
        aria-label="Negotiation rounds used"
        aria-valuemin={0}
        aria-valuemax={s.maxRounds ?? 0}
        aria-valuenow={s.round}
      >
        <span style={{ width: `${progress * 100}%` }} className={live ? "" : `ln-bar-${outcome}`} />
      </div>

      {!compact && (
        <div className="ln-agents">
          <AgentCard
            role="Advertiser"
            name={adName}
            thinking={s.thinking?.agent_role === "advertiser"}
            label="Current offer"
            offer={s.advertiserOffer}
            model={model}
            extra={limits ? `Budget ${usd(limits.budget)} · max ${usd(limits.max_fixed)} fixed, ${usd(limits.max_cpm)} CPM` : undefined}
          />
          <AgentCard
            role="Influencer"
            name={crName}
            avatar={avatar}
            thinking={s.thinking?.agent_role === "influencer"}
            label="Current ask"
            offer={s.creatorAsk}
            model={model}
            extra={deliverable ? `Deliverable: ${DELIVERABLE_LABEL[deliverable] ?? deliverable}` : undefined}
          />
        </div>
      )}

      {s.current && (
        <p className="ln-current">
          On the table: <strong>{termsText(s.current, model)}</strong>
        </p>
      )}

      <div className="ln-log" aria-live="polite">
        {s.messages.length === 0 && !s.thinking && <Empty>No messages yet.</Empty>}
        {s.messages.map((m) => (
          <Message
            key={m.event_id}
            m={m}
            model={model}
            avatar={m.agent_role === "influencer" ? avatar : undefined}
            note={viewer === "advertiser" && m.agent_role === "advertiser" && m.round ? notes?.[m.round] : undefined}
          />
        ))}
        {s.thinking && (
          <div className={`ln-msg ln-${s.thinking.agent_role}`}>
            <div className="ln-bubble ln-typing" aria-label={`${s.thinking.agent_name} is preparing a response`}>
              <small>{s.thinking.agent_name} is preparing a response…</small>
              <span className="ln-dots">
                <i />
                <i />
                <i />
              </span>
            </div>
          </div>
        )}
        {s.ending && (
          <div className={`ln-outcome ln-outcome-${outcome}`}>
            <strong>{OUTCOME_LABEL[outcome ?? ""] ?? "Finished"}</strong>
            <span>{s.ending.content}</span>
            {s.ending.terms && (
              <span className="mono small">
                {termsText(s.ending.terms, model)} · terms {s.ending.terms_hash?.slice(0, 12)}…
              </span>
            )}
          </div>
        )}
        <div ref={end} />
      </div>

      {live && viewer === "advertiser" && (
        <div className="ln-actions">
          <button type="button" className="btn btn-ghost btn-sm" onClick={cancel} disabled={cancelling !== null}>
            Stop negotiation
          </button>
          {cancelling && <span className="muted small">{cancelling}</span>}
        </div>
      )}

      {s.done && !compact && <FeedbackPanel dealId={deal.id} party={viewer} messages={s.messages.filter((m) => m.agent_role === viewer)} model={model} />}
    </div>,
  );
}

function AgentCard(p: {
  role: string;
  name: string;
  avatar?: string;
  thinking: boolean;
  label: string;
  offer: { fixed_fee: string; cpm: string; cap: string } | null;
  model: CompensationModel;
  extra?: string;
}) {
  return (
    <div className={`ln-agent ${p.thinking ? "is-thinking" : ""}`}>
      <div className="ln-agent-top">
        <Avatar src={p.avatar} name={p.name} size={28} />
        <div>
          <strong>{p.name}</strong>
          <small>{p.role}</small>
        </div>
        {p.thinking && <span className="ln-mini-dots" aria-hidden="true" />}
      </div>
      <div className="ln-agent-offer">
        <small>{p.label}</small>
        <span>{p.offer ? termsText(p.offer, p.model) : "—"}</span>
      </div>
      {p.extra && <small className="muted">{p.extra}</small>}
    </div>
  );
}

function Message({ m, model, avatar, note }: { m: NegotiationEvent; model: CompensationModel; avatar?: string; note?: string }) {
  const side = m.agent_role === "advertiser" ? "ln-advertiser" : "ln-influencer";
  return (
    <div className={`ln-msg ${side}`}>
      {m.agent_role === "influencer" && <Avatar src={avatar} name={m.agent_name} size={26} />}
      <div className={`ln-bubble ${m.reply ? `reply-${m.reply}` : ""}`}>
        <small title={m.agent_role === "advertiser" ? "Advertiser agent" : "Creator agent"}>
          {m.agent_name} · {time(m.timestamp)}
        </small>
        {sayMore(m) && <span>{m.content}</span>}
        {m.offer && (
          <span className="ln-offer">
            <b>{offerLabel(m)}</b> {termsText(m.offer, model)}
          </span>
        )}
        {!m.offer && m.reply && <b className="ln-reply">{REPLY_LABEL[m.reply] ?? m.reply}</b>}
        {note && (
          <span className="ln-note" title="Your agent's reasoning. The creator never sees this.">
            <b>Why</b> {note}
          </span>
        )}
      </div>
    </div>
  );
}

// ---- feedback ---------------------------------------------------------------------

const CATEGORIES: { value: FeedbackCategory; label: string }[] = [
  { value: "good_agreement", label: "Agent reached a good agreement" },
  { value: "accepted_too_quickly", label: "Agent accepted too quickly" },
  {
    value: "rejected_reasonable_offer",
    label: "Agent rejected a reasonable offer",
  },
  { value: "poor_negotiation", label: "Agent did not negotiate properly" },
  {
    value: "ignored_requirements",
    label: "Agent ignored campaign requirements",
  },
  { value: "exceeded_budget", label: "Agent exceeded the permitted budget" },
  {
    value: "unrealistic_counteroffer",
    label: "Agent made an unrealistic counteroffer",
  },
  {
    value: "misunderstood_other_party",
    label: "Agent failed to understand the other party",
  },
  { value: "other", label: "Other" },
];

const DIMENSIONS: { key: keyof FeedbackRatings; label: string }[] = [
  { key: "effectiveness", label: "Negotiation effectiveness" },
  { key: "offer_quality", label: "Offer quality" },
  { key: "communication", label: "Agent communication" },
  { key: "respected_requirements", label: "Respected the requirements" },
  { key: "outcome", label: "Final outcome" },
];

const EMPTY_RATINGS: FeedbackRatings = {
  effectiveness: null,
  offer_quality: null,
  communication: null,
  respected_requirements: null,
  outcome: null,
};
const skipKey = (id: string) => `zx-feedback-skip:${id}`;

function Stars({ value, onChange, label, small }: { value: number | null; onChange: (v: number) => void; label: string; small?: boolean }) {
  return (
    <span className={`stars ${small ? "stars-sm" : ""}`} role="radiogroup" aria-label={label}>
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          role="radio"
          aria-checked={value === n}
          aria-label={`${n} of 5`}
          className={value && n <= value ? "on" : ""}
          onClick={() => onChange(n)}
        >
          ★
        </button>
      ))}
    </span>
  );
}

function FeedbackPanel({ dealId, party, messages, model }: { dealId: string; party: Party; messages: NegotiationEvent[]; model: CompensationModel }) {
  const [existing, setExisting] = useState<NegotiationFeedback | null | undefined>(undefined);
  const [editing, setEditing] = useState(false);
  const [skipped, setSkipped] = useState(() => {
    try {
      return sessionStorage.getItem(skipKey(dealId)) === "1";
    } catch {
      return false;
    }
  });
  const [form, setForm] = useState<FeedbackInput>({
    rating: 0,
    category: "good_agreement",
    ratings: EMPTY_RATINGS,
    what_went_wrong: "",
    do_differently: "",
    suggested_response: "",
    suggested_offer: null,
    comments: "",
    message_id: null,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lessonDrafted, setLessonDrafted] = useState(false);

  useEffect(() => {
    api.myFeedback(dealId).then(setExisting, () => setExisting(null));
  }, [dealId]);

  if (existing === undefined) return null;
  if (skipped && !existing) {
    return (
      <button type="button" className="ln-feedback-reopen" onClick={() => setSkipped(false)}>
        Rate this negotiation
      </button>
    );
  }
  if (existing && !editing) {
    return (
      <div className="ln-feedback ln-feedback-done">
        <strong>Thanks for the feedback</strong>
        <span>
          {"★".repeat(existing.rating)}
          {"☆".repeat(5 - existing.rating)} · {existing.category_label}
        </span>
        {lessonDrafted && <span className="muted small">A lesson was drafted from it. Approve it on the My agent page before it guides your agent.</span>}
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => (
            setForm({
              ...existing,
              ratings: { ...EMPTY_RATINGS, ...existing.ratings },
            }),
            setEditing(true)
          )}
        >
          Edit feedback
        </button>
      </div>
    );
  }

  const set = <K extends keyof FeedbackInput>(k: K, v: FeedbackInput[K]) => setForm((f) => ({ ...f, [k]: v }));
  const negative = form.category !== "good_agreement";

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.sendFeedback(dealId, form);
      setExisting(res.feedback);
      setLessonDrafted(res.lesson !== null);
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function skip() {
    try {
      sessionStorage.setItem(skipKey(dealId), "1");
    } catch {
      /* storage unavailable: skip for this view only */
    }
    setSkipped(true);
    setEditing(false);
  }

  return (
    <form
      className="ln-feedback"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <h4>How did the agents perform?</h4>
      <p className="muted small">
        You are rating your own {party === "advertiser" ? "advertiser" : "creator"} agent. Feedback becomes a lesson only after you approve it.
      </p>

      <div className="ln-field">
        <span>Overall rating</span>
        <Stars value={form.rating || null} onChange={(v) => set("rating", v)} label="Overall rating" />
      </div>
      <div className="ln-dims">
        {DIMENSIONS.map((d) => (
          <div key={d.key} className="ln-dim">
            <span>{d.label}</span>
            <Stars small value={form.ratings[d.key]} onChange={(v) => set("ratings", { ...form.ratings, [d.key]: v })} label={d.label} />
          </div>
        ))}
      </div>

      <label className="ln-field">
        <span>Feedback category</span>
        <select value={form.category} onChange={(e) => set("category", e.target.value as FeedbackCategory)}>
          {CATEGORIES.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
      </label>
      {messages.length > 0 && (
        <label className="ln-field">
          <span>Which message is this about? (optional)</span>
          <select value={form.message_id ?? ""} onChange={(e) => set("message_id", e.target.value || null)}>
            <option value="">The agent's last message</option>
            {messages.map((m) => (
              <option key={m.message_id} value={m.message_id ?? ""}>
                Round {m.round}: {m.offer ? termsText(m.offer, model) : m.content.slice(0, 50)}
              </option>
            ))}
          </select>
        </label>
      )}
      {negative && (
        <label className="ln-field">
          <span>What went wrong?</span>
          <textarea rows={2} maxLength={2000} value={form.what_went_wrong} onChange={(e) => set("what_went_wrong", e.target.value)} />
        </label>
      )}
      <label className="ln-field">
        <span>{negative ? "What should the agent have done differently?" : "What should the agent keep doing?"}</span>
        <textarea rows={2} maxLength={2000} value={form.do_differently} onChange={(e) => set("do_differently", e.target.value)} />
      </label>
      <details className="ln-more">
        <summary>Suggest a better response or counteroffer</summary>
        <label className="ln-field">
          <span>Suggested response (optional)</span>
          <textarea rows={2} maxLength={2000} value={form.suggested_response} onChange={(e) => set("suggested_response", e.target.value)} />
        </label>
        <div className="ln-offer-inputs">
          <label className="ln-field">
            <span>Fixed fee (USD)</span>
            <input
              inputMode="decimal"
              value={form.suggested_offer?.fixed_fee ?? ""}
              onChange={(e) =>
                set(
                  "suggested_offer",
                  e.target.value || form.suggested_offer?.cpm
                    ? {
                        fixed_fee: e.target.value || "0",
                        cpm: form.suggested_offer?.cpm ?? "0",
                      }
                    : null,
                )
              }
            />
          </label>
          <label className="ln-field">
            <span>Per 1,000 views (USD)</span>
            <input
              inputMode="decimal"
              value={form.suggested_offer?.cpm ?? ""}
              onChange={(e) =>
                set(
                  "suggested_offer",
                  e.target.value || form.suggested_offer?.fixed_fee
                    ? {
                        fixed_fee: form.suggested_offer?.fixed_fee ?? "0",
                        cpm: e.target.value || "0",
                      }
                    : null,
                )
              }
            />
          </label>
        </div>
        <p className="muted small">An example outside the hard limits is stored as feedback only, never used as an example for the agent.</p>
      </details>
      <label className="ln-field">
        <span>Comments</span>
        <textarea rows={2} maxLength={4000} value={form.comments} onChange={(e) => set("comments", e.target.value)} />
      </label>

      {error && <p className="error small">{error}</p>}
      <div className="ln-actions">
        <button type="submit" className="btn btn-primary btn-sm" disabled={busy || form.rating === 0}>
          {busy ? "Saving…" : "Submit feedback"}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={skip}>
          Skip for now
        </button>
      </div>
    </form>
  );
}
