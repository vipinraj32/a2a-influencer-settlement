import { useState } from "react";
import { api } from "../api";
import { humanize, when } from "../format";
import { useResource } from "../live";
import type { AgentLesson } from "../types";
import { Card, Empty, ErrorText, Spinner, Stat } from "./ui";
import "./negotiation.css";

const pct = (v: number | null) => (v === null ? "—" : `${Math.round(v * 100)}%`);

/** Negotiation results of the user's own agent, computed by the server from stored negotiations and feedback. */
export function AgentPerformance() {
  const perf = useResource(api.agentPerformance, "agent-performance");
  if (perf.error) return <ErrorText error={perf.error} />;
  if (!perf.data) return <Spinner />;
  const p = perf.data;
  const maxWeek = Math.max(1, ...p.trend.map((t) => t.negotiations));

  return (
    <Card title="Negotiation performance">
      {p.total === 0 && p.feedback_count === 0 ? (
        <Empty>No finished negotiations yet. Results and ratings appear here once your agent has negotiated.</Empty>
      ) : (
        <>
          <div className="perf-kpis">
            <Stat label="Negotiations" value={p.total} />
            <Stat label="Agreement rate" value={pct(p.agreement_rate)} />
            <Stat label="Rejection rate" value={pct(p.rejection_rate)} hint="No deal or round limit reached" />
            <Stat label="Avg. rounds" value={p.avg_rounds ?? "—"} />
            <Stat
              label={p.role === "advertiser" ? "Avg. saving" : "Avg. gain"}
              value={pct(p.avg_price_move)}
              hint={`${p.price_move_basis}, over ${p.price_move_samples} agreed deal(s)`}
            />
            <Stat label="Your rating" value={p.satisfaction === null ? "—" : `${p.satisfaction} / 5`} hint={`${p.feedback_count} rating(s)`} />
          </div>
          <div className="perf-grid">
            <div>
              <h3>By week</h3>
              {p.trend.length === 0 ? (
                <Empty>No dated results yet.</Empty>
              ) : (
                <div className="perf-bars">
                  {p.trend.map((t) => (
                    <div key={t.week} className="perf-bar" title={`${t.negotiations} negotiation(s), ${t.feedback} rating(s)`}>
                      <span>{t.week}</span>
                      <div>
                        <span
                          style={{
                            width: `${(t.negotiations / maxWeek) * 100}%`,
                          }}
                        />
                      </div>
                      <span className="muted">
                        {pct(t.agreement_rate)} · {t.avg_rating === null ? "no rating" : `${t.avg_rating}★`}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div>
              <h3>Most common mistakes</h3>
              {p.common_mistakes.length === 0 ? (
                <Empty>None reported in your feedback.</Empty>
              ) : (
                <ul className="perf-mistakes">
                  {p.common_mistakes.map((m) => (
                    <li key={m.category}>
                      <span>{m.label}</span>
                      <strong>{m.count}</strong>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </>
      )}
    </Card>
  );
}

/** Lessons drafted from the user's feedback. Nothing reaches the agent until it is approved here. */
export function AgentLessons() {
  const lessons = useResource(api.lessons, "agent-lessons");
  if (lessons.error) return <ErrorText error={lessons.error} />;
  if (!lessons.data) return <Spinner />;
  const list = lessons.data.filter((l) => l.status !== "dismissed");
  return (
    <Card title="Lessons for your agent">
      <p className="muted small">
        Each rating drafts a lesson. Approved lessons are added to your agent's AI prompt as strategy guidance for later negotiations. They never change your
        hard limits: the server still refuses any offer outside them.
      </p>
      {list.length === 0 ? <Empty>No lessons yet. Rate a finished negotiation to draft one.</Empty> : null}
      <div className="lessons">
        {list.map((l) => (
          <LessonRow key={l.id} lesson={l} onChange={lessons.reload} />
        ))}
      </div>
    </Card>
  );
}

function LessonRow({ lesson, onChange }: { lesson: AgentLesson; onChange: () => Promise<void> }) {
  const [text, setText] = useState(lesson.guidance);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function review(status: "approved" | "dismissed") {
    setBusy(true);
    setError(null);
    try {
      await api.reviewLesson(lesson.id, status, text !== lesson.guidance ? text : undefined);
      await onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className={`lesson lesson-${lesson.status}`}>
      <div className="lesson-head">
        <strong>{humanize(lesson.category)}</strong>
        <span className="muted small">
          {lesson.status === "approved" ? "Approved" : "Waiting for your review"} · {when(lesson.created_at)} · {lesson.context.niche}
        </span>
      </div>
      <textarea rows={2} maxLength={600} value={text} onChange={(e) => setText(e.target.value)} aria-label="Lesson text" />
      {error && <p className="error small">{error}</p>}
      <div className="ln-actions">
        <button type="button" className="btn btn-primary btn-sm" disabled={busy || text.trim().length < 5} onClick={() => review("approved")}>
          {lesson.status === "approved" ? "Save" : "Approve"}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => review("dismissed")}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
