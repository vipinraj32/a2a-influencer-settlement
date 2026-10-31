import { Link } from "react-router";
import { api } from "../../api";
import { Avatar, CampaignBadge, ErrorText, Spinner, StateBadge } from "../../components/ui";
import { num, usd, when } from "../../format";
import { useLiveEvents, useResource } from "../../live";
import { Icon } from "../landing/parts";
import { useMe, useSession } from "../../session";
import type { Campaign, Deal, DealState } from "../../types";
import "./dashboard.css";

/** Deals whose cap is (or was) locked in escrow. */
const ESCROWED: DealState[] = ["FUNDED", "PROOF_SUBMITTED", "PROOF_REJECTED", "VERIFIED", "FIXED_RELEASED", "DISPUTED"];
/** Deals that wait on the advertiser or need a look. */
const ATTENTION: DealState[] = ["AWAITING_APPROVAL", "AWAITING_CHECKOUT", "ESCALATED", "PAYMENT_UNKNOWN", "PAYMENT_FAILED", "DISPUTED", "BLOCKED"];

const PIPELINE: { label: string; states: DealState[]; tone: string }[] = [
  { label: "Negotiating", states: ["NEGOTIATING", "AGREED", "CART_CREATED"], tone: "blue" },
  { label: "Awaiting you", states: ["AWAITING_APPROVAL", "AWAITING_CHECKOUT", "ESCALATED", "BLOCKED"], tone: "orange" },
  { label: "Paying", states: ["SIGNED", "FUNDING", "PAYMENT_UNKNOWN"], tone: "blue-soft" },
  { label: "Live in escrow", states: ESCROWED, tone: "ink" },
  { label: "Completed", states: ["COMPLETED"], tone: "blue" },
  { label: "Closed", states: ["REJECTED", "PAYMENT_FAILED", "PAYMENT_EXPIRED", "EXPIRED_REFUNDED"], tone: "muted" },
];

const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
const paidOut = (d: Deal, kinds: string[]) => sum(d.payouts.filter((p) => kinds.includes(p.kind)).map((p) => Number(p.amount)));

async function loadDeals(): Promise<Deal[]> {
  const campaigns = await api.campaigns();
  const lists = await Promise.all(campaigns.map((c) => api.campaignDeals(c.spec.id)));
  return lists.flat();
}

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export function AdvertiserHome() {
  const { user } = useMe();
  const { status } = useSession();
  const campaigns = useResource(api.campaigns, "campaigns");
  const deals = useResource(loadDeals, "advertiser-deals");
  const directory = useResource(api.directory, "directory");
  const wallet = useResource(() => (user.wallet ? api.walletBalance() : Promise.resolve(null)), `wallet-${user.wallet}`);
  useLiveEvents((e) => e.type === "campaign" || e.type === "deal", () => {
    campaigns.reload();
    deals.reload();
  });

  const list = campaigns.data ?? [];
  const allDeals = deals.data ?? [];
  const byCampaign = new Map<string, Deal[]>();
  for (const d of allDeals) byCampaign.set(d.campaign_id, [...(byCampaign.get(d.campaign_id) ?? []), d]);

  const budget = sum(list.map((c) => Number(c.spec.budget)));
  const paid = sum(allDeals.map((d) => paidOut(d, ["fixed", "performance"])));
  const refunded = sum(allDeals.map((d) => paidOut(d, ["refund"])));
  const locked = sum(allDeals.filter((d) => ESCROWED.includes(d.state)).map((d) => Number(d.terms?.cap ?? 0) - paidOut(d, ["fixed", "performance", "refund"])));
  const agreed = allDeals.filter((d) => d.terms && d.state !== "REJECTED").length;
  const inProgress = list.filter((c) => !["done", "failed"].includes(c.status)).length;
  const attention = [
    ...list.filter((c) => c.status === "awaiting_approval").map((c) => ({ key: `c-${c.spec.id}`, campaign: c, deal: null as Deal | null })),
    ...allDeals
      .filter((d) => ATTENTION.includes(d.state) && !(d.state === "AWAITING_APPROVAL" && list.find((c) => c.spec.id === d.campaign_id)?.status === "awaiting_approval"))
      .map((d) => ({ key: d.id, campaign: list.find((c) => c.spec.id === d.campaign_id), deal: d })),
  ];
  const creators = [...(directory.data ?? [])].sort((a, b) => b.subscribers - a.subscribers).slice(0, 6);
  const simulated = status?.settlement_simulated ?? true;
  const first = user.name.split(" ")[0] || "there";
  const loading = campaigns.loading && !campaigns.data;

  return (
    <div className="dash">
      <section className="dash-hero">
        <div>
          <p className="dash-eyebrow">{user.company || "Advertiser"} · dashboard</p>
          <h1>
            {greeting()}, {first}
          </h1>
          <p className="dash-sub">Your agents negotiate. Your rules decide. Here’s where every campaign stands.</p>
        </div>
        <div className="dash-hero-actions">
          <Link to="/advertiser/creators" className="btn btn-secondary">
            <Icon name="users" size={18} /> Browse creators
          </Link>
          <Link to="/advertiser/new" className="btn btn-primary">
            <Icon name="plus" size={18} /> New campaign
          </Link>
        </div>
      </section>

      <ErrorText error={campaigns.error ?? deals.error} />

      <section className="kpis" aria-label="Summary">
        <Kpi icon="ledger" label="Total budget" value={usd(budget)} foot={`${list.length} campaign${list.length === 1 ? "" : "s"} · ${inProgress} in progress`} />
        <Kpi icon="shield" label="Locked in escrow" value={usd(locked)} foot={simulated ? "Testnet · simulated settlement" : "On-chain escrow"} tone="blue" />
        <Kpi icon="check" label="Paid to creators" value={usd(paid)} foot={`${usd(refunded)} refunded to you`} tone="orange" />
        <Kpi icon="agents" label="Deals agreed" value={num(agreed)} foot={`${num(directory.data?.length ?? 0)} creator agents live`} />
      </section>

      <div className="dash-grid">
        <section className="panel dash-campaigns">
          <header className="panel-head">
            <h2>Campaigns</h2>
            <Link to="/advertiser/new" className="panel-link">
              New <Icon name="arrow" size={16} />
            </Link>
          </header>
          {loading ? (
            <Spinner />
          ) : list.length === 0 ? (
            <GetStarted hasWallet={!!user.wallet} />
          ) : (
            <ul className="camp-list">
              {list.map((c) => (
                <CampaignRow key={c.spec.id} campaign={c} deals={byCampaign.get(c.spec.id) ?? []} />
              ))}
            </ul>
          )}
        </section>

        <div className="dash-side">
          <section className="panel">
            <header className="panel-head">
              <h2>Needs your attention</h2>
              {attention.length > 0 && <span className="count">{attention.length}</span>}
            </header>
            {attention.length === 0 ? (
              <p className="panel-empty">
                <Icon name="check" size={18} /> All clear — nothing is waiting on you.
              </p>
            ) : (
              <ul className="todo">
                {attention.slice(0, 6).map((a) => (
                  <li key={a.key}>
                    <Link to={`/advertiser/campaigns/${a.campaign?.spec.id ?? a.deal?.campaign_id}`}>
                      <span className="todo-ic">
                        <Icon name={a.deal ? "alert" : "key"} size={18} />
                      </span>
                      <span className="todo-body">
                        <strong>{a.deal ? (a.campaign?.spec.advertiser ?? "Campaign") : "Sign the Payment Mandates"}</strong>
                        <span>{a.deal ? <StateBadge state={a.deal.state} /> : `${a.campaign?.spec.advertiser} · agreed terms are ready`}</span>
                      </span>
                      <Icon name="arrow" size={16} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel">
            <header className="panel-head">
              <h2>Deal pipeline</h2>
              <span className="muted small">{num(allDeals.length)} deals</span>
            </header>
            {allDeals.length === 0 ? (
              <p className="panel-empty">Deals appear here once your agent starts negotiating.</p>
            ) : (
              <ul className="pipeline">
                {PIPELINE.map((p) => {
                  const n = allDeals.filter((d) => p.states.includes(d.state)).length;
                  return (
                    <li key={p.label}>
                      <span className="pipeline-label">{p.label}</span>
                      <span className="pipeline-bar">
                        <span className={`fill fill-${p.tone}`} style={{ width: `${(n / allDeals.length) * 100}%` }} />
                      </span>
                      <strong>{n}</strong>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section className="panel wallet-panel">
            <header className="panel-head">
              <h2>Wallet</h2>
              <Link to="/account" className="panel-link">
                Manage <Icon name="arrow" size={16} />
              </Link>
            </header>
            {!user.wallet ? (
              <p className="panel-empty">
                Link a browser wallet to sign Payment Mandates. <Link to="/account">Link wallet</Link>
              </p>
            ) : (
              <div className="wallet-body">
                <span className="wallet-ic">
                  <Icon name="wallet" size={22} />
                </span>
                <div>
                  <strong>{wallet.data?.balance != null ? `${Number(wallet.data.balance).toLocaleString("en-US", { maximumFractionDigits: 2 })} ${wallet.data.token}` : wallet.loading ? "…" : "—"}</strong>
                  <span className="muted small">{wallet.data?.error ? "Balance unavailable" : `${status?.token.network ?? "testnet"} balance`}</span>
                </div>
              </div>
            )}
          </section>
        </div>
      </div>

      <section className="panel">
        <header className="panel-head">
          <h2>Creator agents</h2>
          <Link to="/advertiser/creators" className="panel-link">
            See all <Icon name="arrow" size={16} />
          </Link>
        </header>
        {directory.loading && !directory.data ? (
          <Spinner />
        ) : creators.length === 0 ? (
          <p className="panel-empty">No creator agents are live yet. They appear as soon as creators finish onboarding.</p>
        ) : (
          <ul className="creator-grid">
            {creators.map((c) => (
              <li key={c.id}>
                <Link to={`/advertiser/creators/${c.id}`} className="creator-card">
                  <Avatar src={c.avatar_url} name={c.name} size={44} />
                  <span>
                    <strong>{c.name}</strong>
                    <span className="muted small">
                      {num(c.subscribers)} subscribers · {c.niches[0] ?? c.niche}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function Kpi({ icon, label, value, foot, tone = "" }: { icon: string; label: string; value: string; foot: string; tone?: string }) {
  return (
    <div className={`kpi ${tone ? `kpi-${tone}` : ""}`}>
      <span className="kpi-ic">
        <Icon name={icon} size={20} />
      </span>
      <span className="kpi-label">{label}</span>
      <strong className="kpi-value">{value}</strong>
      <span className="kpi-foot">{foot}</span>
    </div>
  );
}

function CampaignRow({ campaign: c, deals }: { campaign: Campaign; deals: Deal[] }) {
  const budget = Number(c.spec.budget) || 0;
  const paid = sum(deals.map((d) => paidOut(d, ["fixed", "performance"])));
  const committed = sum(deals.filter((d) => ESCROWED.includes(d.state)).map((d) => Number(d.terms?.cap ?? 0) - paidOut(d, ["fixed", "performance", "refund"])));
  const agreed = deals.filter((d) => d.terms && d.state !== "REJECTED").length;
  const pct = (v: number) => `${budget ? Math.min(100, (v / budget) * 100) : 0}%`;
  return (
    <li>
      <Link to={`/advertiser/campaigns/${c.spec.id}`} className="camp">
        <span className="camp-top">
          <span className="camp-name">
            <strong>{c.spec.advertiser}</strong>
            <span className="muted small">
              {c.spec.niche} · {c.spec.deliverable || "video"} · {when(c.created_at)}
            </span>
          </span>
          <CampaignBadge status={c.status} />
        </span>
        <span className="camp-bar" title={`Paid ${usd(paid)} · in escrow ${usd(committed)} of ${usd(budget)}`}>
          <span className="fill fill-orange" style={{ width: pct(paid) }} />
          <span className="fill fill-blue" style={{ width: pct(committed) }} />
        </span>
        <span className="camp-meta">
          <span>
            <b>{usd(budget)}</b> budget
          </span>
          <span>
            <b>{agreed}</b>/{c.spec.max_creators} creators
          </span>
          <span>
            <b>{usd(paid)}</b> paid
          </span>
        </span>
        {c.error && <span className="error small">{c.error}</span>}
      </Link>
    </li>
  );
}

function GetStarted({ hasWallet }: { hasWallet: boolean }) {
  const steps = [
    { done: hasWallet, title: "Link your wallet", text: "It signs your Payment Mandates.", to: "/account" },
    { done: false, title: "Set up your agent", text: "Budgets, ceilings and how it negotiates.", to: "/advertiser/agent" },
    { done: false, title: "Launch a campaign", text: "Your agent contacts matching creators.", to: "/advertiser/new" },
  ];
  return (
    <ol className="start">
      {steps.map((s, k) => (
        <li key={s.title} className={s.done ? "done" : ""}>
          <Link to={s.to}>
            <span className="start-n">{s.done ? <Icon name="check" size={16} /> : k + 1}</span>
            <span>
              <strong>{s.title}</strong>
              <span className="muted small">{s.text}</span>
            </span>
            <Icon name="arrow" size={16} />
          </Link>
        </li>
      ))}
    </ol>
  );
}
