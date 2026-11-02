// The creator's deals: every collaboration their agent negotiated, filterable by stage and searchable by brand.
// Read-only: the only things a creator does here are open a deal and, from its page, submit the video.

import { Link, useSearchParams } from "react-router";
import { api } from "../../api";
import { Breadcrumbs } from "../../components/Breadcrumbs";
import { StateBadge } from "../../components/ui";
import { DEAL_GROUPS, NEEDS_PROOF, dealGroup, dealPay, dealSearchText, type DealGroup } from "../../creatorDeals";
import { DELIVERABLE_LABEL, termsText, videoLength, when } from "../../format";
import { useLiveEvents, useResource } from "../../live";
import { Icon } from "../landing/parts";
import type { InfluencerDeal } from "../../types";
import "../advertiser/dashboard.css";
import "./deals.css";

const FILTERS: { key: "all" | DealGroup; label: string }[] = [
  { key: "all", label: "All deals" },
  ...(["pending", "negotiating", "accepted", "active", "completed", "rejected"] as const).map((k) => ({
    key: k,
    label: DEAL_GROUPS.find((g) => g.key === k)!.label,
  })),
];

export function MyDeals() {
  const deals = useResource(api.influencerDeals, "my-deals");
  useLiveEvents(() => true, deals.reload); // the stream only carries this creator's own deal events
  // filter and search live in the URL so a refresh or a shared link keeps them
  const [params, setParams] = useSearchParams();
  const filter = (FILTERS.find((f) => f.key === params.get("status"))?.key ?? "all") as "all" | DealGroup;
  const query = params.get("q") ?? "";
  const update = (key: string, value: string) =>
    setParams(
      (p) => {
        const next = new URLSearchParams(p);
        if (value && value !== "all") next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );

  const list = deals.data ?? [];
  const count = (k: "all" | DealGroup) => (k === "all" ? list.length : list.filter((d) => dealGroup(d.state) === k).length);
  const q = query.trim().toLowerCase();
  const shown = list.filter((d) => (filter === "all" || dealGroup(d.state) === filter) && (!q || dealSearchText(d).includes(q)));

  return (
    <div className="stack dash my-deals">
      <Breadcrumbs items={[{ label: "Overview", to: "/influencer" }, { label: "My deals" }]} />
      <header className="md-head">
        <h1>My Deals</h1>
        <p className="muted">Manage your brand collaborations, review campaign offers, and track your active deals.</p>
      </header>

      <div className="kpis">
        <Kpi icon="ledger" label="Total deals" value={deals.data ? list.length : null} />
        <Kpi icon="alert" label="Pending offers" value={deals.data ? count("pending") : null} tone="orange" />
        <Kpi icon="chart" label="Active deals" value={deals.data ? count("accepted") + count("active") : null} tone="blue" />
        <Kpi icon="check" label="Completed deals" value={deals.data ? count("completed") : null} />
      </div>

      <section className="card">
        <div className="camp-tools">
          <div className="seg" role="tablist" aria-label="Filter deals">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                role="tab"
                aria-selected={filter === f.key}
                className={filter === f.key ? "on" : ""}
                onClick={() => update("status", f.key)}
                title={DEAL_GROUPS.find((g) => g.key === f.key)?.hint}
              >
                {f.label} <span>{deals.data ? count(f.key) : "·"}</span>
              </button>
            ))}
          </div>
          <label className="camp-search">
            <Icon name="search" size={16} />
            <input type="search" placeholder="Search brand or campaign…" value={query} onChange={(e) => update("q", e.target.value)} aria-label="Search deals" />
          </label>
        </div>

        {deals.error && !deals.data ? (
          <div className="md-state" role="alert">
            <strong>Could not load your deals</strong>
            <span className="muted">{deals.error}</span>
            <button type="button" className="btn btn-secondary" onClick={() => void deals.reload()}>
              Retry
            </button>
          </div>
        ) : !deals.data ? (
          <ul className="md-list" aria-busy="true" aria-label="Loading deals">
            {[0, 1, 2].map((i) => (
              <li key={i} className="md-card md-skeleton">
                <span />
                <span />
                <span />
              </li>
            ))}
          </ul>
        ) : list.length === 0 ? (
          <div className="md-state">
            <strong>No deals yet</strong>
            <span className="muted">Your brand collaboration offers will appear here.</span>
            <Link to="/influencer/agent" className="btn btn-secondary">
              Check your agent settings
            </Link>
          </div>
        ) : shown.length === 0 ? (
          <div className="md-state">
            <strong>No deals match</strong>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => setParams(new URLSearchParams(), { replace: true })}
            >
              Clear filters
            </button>
          </div>
        ) : (
          <ul className="md-list">
            {shown.map((d) => (
              <DealCard key={d.id} deal={d} />
            ))}
          </ul>
        )}
        {deals.error && deals.data && <p className="error small">Could not refresh: {deals.error}</p>}
      </section>
    </div>
  );
}

function Kpi({ icon, label, value, tone }: { icon: string; label: string; value: number | null; tone?: "blue" | "orange" }) {
  return (
    <div className={`kpi ${tone ? `kpi-${tone}` : ""}`}>
      <span className="kpi-ic">
        <Icon name={icon} size={18} />
      </span>
      <span className="kpi-label">{label}</span>
      <strong className="kpi-value">{value === null ? <span className="md-dash">–</span> : value}</strong>
    </div>
  );
}

export function DealCard({ deal: d }: { deal: InfluencerDeal }) {
  const group = dealGroup(d.state);
  const pay = dealPay(d);
  const c = d.campaign;
  const yourTurn = NEEDS_PROOF.has(d.state);
  const deadline = d.terms?.publish_by
    ? when(d.terms.publish_by)
    : d.rounds.at(-1)
      ? `${d.rounds.at(-1)!.offer.publish_within_days} days after payment`
      : "—";
  return (
    <li>
      <Link to={`/influencer/deals/${encodeURIComponent(d.id)}`} className={`md-card md-${group} ${yourTurn ? "md-turn" : ""}`}>
        <div className="md-top">
          <div className="md-brand">
            <strong>{c.advertiser}</strong>
            <span className="muted small">
              {c.brand_handle} · {c.niche}
            </span>
          </div>
          <div className="md-status">
            {yourTurn && <span className="chip warn">Your turn</span>}
            <StateBadge state={d.state} />
          </div>
        </div>
        {c.brief && <p className="md-brief">{c.brief}</p>}
        <dl className="md-facts">
          <div>
            <dt>{pay?.final ? "Agreed pay" : "Proposed pay"}</dt>
            <dd>{pay ? termsText(pay, pay.model) : "No offer yet"}</dd>
          </div>
          <div>
            <dt>Deliverable</dt>
            <dd>
              {DELIVERABLE_LABEL[c.deliverable] ?? c.deliverable} · {videoLength(c.min_duration_seconds, c.max_duration_seconds)}
            </dd>
          </div>
          <div>
            <dt>Platform</dt>
            <dd>YouTube</dd>
          </div>
          <div>
            <dt>Publish by</dt>
            <dd>{deadline}</dd>
          </div>
        </dl>
        <div className="md-foot">
          <span className="muted small">Updated {when(d.updated_at)}</span>
          <span className="md-view">
            View details <Icon name="arrow" size={14} />
          </span>
        </div>
      </Link>
    </li>
  );
}
