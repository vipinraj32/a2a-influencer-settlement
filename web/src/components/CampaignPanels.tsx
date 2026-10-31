import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { MODEL_LABEL, humanize, termsText } from "../format";
import { useLiveEvents, useResource } from "../live";
import { useSession } from "../session";
import type { AuditEvent, Deal } from "../types";
import { Avatar, Empty, Spinner } from "./ui";

/** The A2A negotiation of one deal as a conversation: your agent's offers on the right, the creator agent's replies on the left. */
export function A2AChat({ deal, creator, avatar }: { deal: Deal; creator: string; avatar?: string }) {
  const view = useResource(() => api.negotiation(deal.id), `neg:${deal.id}`);
  useLiveEvents((e) => e.deal_id === deal.id, view.reload);
  const end = useRef<HTMLDivElement>(null);
  const v = view.data;
  const count = v?.rounds.length ?? 0;
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" }); // newer browsers return a Promise here: never hand it to React as a cleanup
  }, [count]);

  if (view.error) return <p className="error small">{view.error}</p>;
  if (!v) return <Spinner label="Loading conversation…" />;
  const model = v.compensation_model;

  return (
    <div className="chat">
      <div className="chat-meta">
        <span className={`chat-status chat-${v.status}`}>{humanize(v.status)}</span>
        <span>
          {MODEL_LABEL[model]} · round {v.rounds_used} of {v.max_rounds}
        </span>
      </div>
      <div className="chat-log" aria-live="polite">
        {v.rounds.length === 0 && <Empty>No messages yet. Your agent is preparing an offer.</Empty>}
        {v.rounds.map((r) => (
          <div key={r.round} className="chat-round">
            <p className="chat-day">Round {r.round}</p>
            {r.advertiser_offer && (
              <div className="msg msg-out">
                <div className="bubble">
                  <small>Your agent · offer</small>
                  <strong>{termsText(r.advertiser_offer, model)}</strong>
                </div>
              </div>
            )}
            <div className="msg msg-in">
              <Avatar src={avatar} name={creator} size={28} />
              <div className={`bubble reply-${r.reply}`}>
                <small>
                  {creator}’s agent · {humanize(r.reply)}
                </small>
                {r.counter && <strong>{termsText(r.counter, model)}</strong>}
                {r.explanation && <span>{r.explanation}</span>}
              </div>
            </div>
          </div>
        ))}
        {v.outcome && <p className="chat-outcome">{v.outcome}</p>}
        {v.agreement && (
          <p className="chat-outcome good">
            Terms signed by the creator agent · hash <span className="mono">{v.agreement.terms_hash.slice(0, 12)}…</span>
          </p>
        )}
        <div ref={end} />
      </div>
    </div>
  );
}

const DAY = 86_400_000;

type Metric = { key: string; label: string; total: string; points: number[] };

/** Daily counts from the campaign's own audit log, from its first event to today. */
export function ActivityChart({ events, deals }: { events: AuditEvent[]; deals: Deal[] }) {
  const [metric, setMetric] = useState("events");
  const [hover, setHover] = useState<number | null>(null);
  if (events.length === 0) return <Empty>No activity yet. The chart fills in as the agents work.</Empty>;

  const start = new Date(events[0].at);
  start.setHours(0, 0, 0, 0);
  const days = Math.max(2, Math.ceil((Date.now() - start.getTime()) / DAY) + 1);
  const bucket = (at: string) => Math.min(days - 1, Math.max(0, Math.floor((new Date(at).getTime() - start.getTime()) / DAY)));
  const count = (pick: (e: AuditEvent) => boolean) => {
    const out = new Array<number>(days).fill(0);
    for (const e of events) if (pick(e)) out[bucket(e.at)] += 1;
    return out;
  };
  const paid = new Array<number>(days).fill(0);
  for (const p of deals.flatMap((d) => d.payouts)) if (p.kind !== "refund") paid[bucket(p.at)] += Number(p.amount);
  const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);

  const metrics: Metric[] = [
    { key: "events", label: "Activity", total: String(events.length), points: count(() => true) },
    {
      key: "offers",
      label: "Negotiation",
      total: String(sum(count((e) => e.kind.startsWith("negotiation")))),
      points: count((e) => e.kind.startsWith("negotiation")),
    },
    { key: "paid", label: "Paid out", total: `$${sum(paid).toFixed(2)}`, points: paid },
  ];
  const m = metrics.find((x) => x.key === metric) ?? metrics[0];
  const max = Math.max(1, ...m.points);
  const W = 600;
  const H = 160;
  const x = (i: number) => (i / (days - 1)) * W;
  const y = (v: number) => H - (v / max) * (H - 8);
  const line = m.points.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const label = (i: number) => new Date(start.getTime() + i * DAY).toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const fmt = (v: number) => (m.key === "paid" ? `$${v.toFixed(2)}` : String(v));
  const h = hover ?? days - 1;

  return (
    <div className="act">
      <div className="act-tabs" role="tablist" aria-label="Chart metric">
        {metrics.map((x) => (
          <button key={x.key} type="button" role="tab" aria-selected={x.key === metric} className={x.key === metric ? "on" : ""} onClick={() => setMetric(x.key)}>
            {x.label}
          </button>
        ))}
      </div>
      <div className="act-body">
        <div className="act-kpi">
          <small>{m.label}</small>
          <strong>{m.total}</strong>
          <span className="muted small">
            {label(h)}: {fmt(m.points[h])}
          </span>
        </div>
        <div className="act-plot">
          <svg
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            onMouseMove={(e) => {
              const r = e.currentTarget.getBoundingClientRect();
              setHover(Math.round(((e.clientX - r.left) / r.width) * (days - 1)));
            }}
            onMouseLeave={() => setHover(null)}
            role="img"
            aria-label={`${m.label} per day`}
          >
            {[0.25, 0.5, 0.75].map((f) => (
              <line key={f} x1="0" x2={W} y1={H * f} y2={H * f} className="act-grid" />
            ))}
            <polygon points={`0,${H} ${line} ${W},${H}`} className="act-area" />
            <polyline points={line} className="act-line" />
            <line x1={x(h)} x2={x(h)} y1="0" y2={H} className="act-cursor" />
          </svg>
          <div className="act-x">
            <span>{label(0)}</span>
            {days > 2 && <span>{label(Math.floor((days - 1) / 2))}</span>}
            <span>{label(days - 1)}</span>
          </div>
        </div>
        <div className="act-y">
          <span>{fmt(max)}</span>
          <span>0</span>
        </div>
      </div>
    </div>
  );
}

/** Simulated escrow only: moves the platform clock so releases and refunds can be tested. */
export function DevClock() {
  const { status, refresh } = useSession();
  const [msg, setMsg] = useState<string | null>(null);
  if (!status?.escrow.simulated) return null;
  async function advance(hours: number) {
    setMsg(null);
    try {
      const r = await api.advance(hours);
      setMsg(`+${r.clock_offset_hours.toFixed(0)} h · ${r.actions} escrow action(s)`);
      await refresh();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  }
  return (
    <div className="devclock" title="Simulated escrow only: moves the platform clock to trigger releases and refunds">
      <span>Dev clock {status.clock_offset_hours ? `+${status.clock_offset_hours.toFixed(0)} h` : "now"}</span>
      <button type="button" onClick={() => advance(49)}>
        +49 h
      </button>
      <button type="button" onClick={() => advance(24 * 7)}>
        +7 days
      </button>
      {msg && <span>{msg}</span>}
    </div>
  );
}
