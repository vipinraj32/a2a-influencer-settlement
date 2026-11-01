import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router";
import { api } from "../../api";
import { ComplianceCard, ReviewForm } from "../../components/Compliance";
import { needsReview } from "../../components/ContentChecks";
import { ActivityChart, DevClock } from "../../components/CampaignPanels";
import { LiveNegotiation } from "../../components/LiveNegotiation";
import { NegotiationQueue, NegotiationSummary, useNegotiationQueue } from "../../components/NegotiationQueue";
import { DealDetail } from "../../components/DealDetail";
import { Requirements } from "../../components/Requirements";
import { Timeline } from "../../components/Timeline";
import { Breadcrumbs } from "../../components/Breadcrumbs";
import { CreatorLink } from "../../components/CreatorLink";
import { ActionButton, Address, Avatar, CampaignBadge, Card, Empty, ErrorText, Notice, Spinner, StateBadge, Stat } from "../../components/ui";
import { MODEL_LABEL, humanize, short, usd } from "../../format";
import { useLiveEvents, useResource } from "../../live";
import { useMe, useSession } from "../../session";
import type { CampaignDetail, CompensationModel, Deal, DirectoryEntry, NegotiationSession, WalletBalance } from "../../types";
import * as wallet from "../../wallet";
import "./campaign.css";
import "./dashboard.css";

function spend(deals: Deal[]) {
  const locked = deals.filter((d) => d.funding_tx && d.terms).reduce((s, d) => s + Number(d.terms!.cap), 0);
  const paid = deals.flatMap((d) => d.payouts).filter((p) => p.kind !== "refund").reduce((s, p) => s + Number(p.amount), 0);
  const refunded = deals.flatMap((d) => d.payouts).filter((p) => p.kind === "refund").reduce((s, p) => s + Number(p.amount), 0);
  return { locked, paid, refunded, inEscrow: locked - paid - refunded };
}

function SigningPanel({ campaign, names, onDone }: { campaign: CampaignDetail; names: Map<string, string>; onDone: () => void }) {
  const { status } = useSession();
  const pending = campaign.pending_approval!;
  const requests = campaign.signing_requests;
  const [progress, setProgress] = useState<string | null>(null);
  const [balance, setBalance] = useState<WalletBalance | null>(null);
  const [account, setAccount] = useState<string | null>(null);
  const [chainId, setChainId] = useState<number | null>(null);
  const explorer = status?.escrow.simulated ? undefined : status?.chain.explorer_url;
  const simulated = status?.settlement_simulated ?? true;

  async function refresh(): Promise<WalletBalance | null> {
    setAccount(await wallet.currentAccount());
    setChainId(await wallet.currentChainId());
    try {
      const b = await api.walletBalance();
      setBalance(b);
      return b;
    } catch {
      setBalance(null);
      return null;
    }
  }

  useEffect(() => {
    void refresh();
    return wallet.onAccountsChanged(() => void refresh());
  }, [campaign.spec.id]);

  const total = requests.reduce((s, r) => s + Number(r.amount ?? 0), 0);
  const held = balance?.balance != null ? Number(balance.balance) : null;
  const shortfall = held !== null && held < total;
  // Deals settle independently, in this order: how many the current balance could cover
  let covered = 0;
  for (let i = 0, left = held ?? 0; i < requests.length && left >= Number(requests[i].amount ?? 0); i++) {
    left -= Number(requests[i].amount ?? 0);
    covered++;
  }
  const expired = requests.some((r) => r.expired);
  const blocked = requests.filter((r) => r.problems.length > 0 && !r.expired);
  const wrongAccount = account !== null && !wallet.sameAddress(account, pending.payer);
  const wrongChain = chainId !== null && status !== null && chainId !== status.chain.id;

  async function signAll() {
    const connected = await wallet.connect();
    if (!wallet.sameAddress(connected, pending.payer)) {
      throw new Error(`Switch MetaMask to the campaign's payer wallet ${pending.payer} (currently ${connected}).`);
    }
    if (status) await wallet.ensureChain(status.chain);
    setProgress("Checking your balance…");
    await refresh(); // advisory only: it can still change before settlement
    const signatures: Record<string, string> = {};
    try {
      for (const [i, r] of requests.entries()) {
        if (!r.typed_data) continue;
        setProgress(`Signing ${i + 1} of ${requests.length}: ${names.get(r.influencer_id) ?? r.influencer_id}…`);
        signatures[r.deal_id] = await wallet.signTypedData(connected, r.typed_data);
      }
      setProgress("Submitting the signed authorizations…");
      await api.decide(campaign.spec.id, true, signatures);
    } finally {
      setProgress(null);
    }
    onDone();
  }

  async function approveHosted() {
    await api.decide(campaign.spec.id, true);
    onDone();
  }

  async function decline() {
    await api.decide(campaign.spec.id, false);
    onDone();
  }

  async function renew() {
    await api.redraft(campaign.spec.id);
    onDone();
  }

  const coin = status?.token.coin ?? "USDC";
  return (
    <div className="signing card tone-warn">
      <div className="signing-head">
        <h2>
          Authorize {requests.length} payment{requests.length > 1 ? "s" : ""} · {usd(total)}
        </h2>
        {simulated && (
          <span className="chip warn" title="HSP and the escrow run in-process: no real USDC moves. Your wallet still signs real EIP-712 data.">
            Simulated
          </span>
        )}
      </div>

      <div className="signing-grid">
        <section className="signing-block" aria-label="Budget">
          <h3>Budget</h3>
          <div className="stats compact-stats">
            <Stat label="Campaign budget" value={usd(campaign.funding.budget)} hint="A spending limit, not money held" />
            <Stat label="Reserved" value={usd(campaign.funding.reserved)} hint="Caps of deals being paid or already paid, including these" />
            <Stat label="Funded" value={usd(campaign.funding.funded)} hint="Verified settlements locked in escrow" />
            <Stat label="Remaining" value={usd(campaign.funding.remaining)} />
          </div>
        </section>

        <section className="signing-block" aria-label="Your wallet">
          <h3>Your wallet</h3>
          <dl className="kv">
            <dt>Payer</dt>
            <dd>
              <Address value={pending.payer} explorer={explorer} />
              {wrongAccount && <span className="chip warn">MetaMask is on {short(account)}</span>}
            </dd>
            <dt>Network</dt>
            <dd>
              {status?.chain.name}
              {wrongChain && (
                <span className="chip warn" title={`Your wallet is on chain ${chainId}`}>
                  will switch
                </span>
              )}
            </dd>
            <dt>Balance</dt>
            <dd>
              {balance === null || balance.balance === null ? (
                <span className="muted">unknown</span>
              ) : (
                <span title={`checked ${new Date(balance.checked_at).toLocaleTimeString()}`}>
                  {usd(balance.balance)} {coin}
                </span>
              )}{" "}
              <button type="button" className="link" onClick={() => void refresh()}>
                refresh
              </button>
            </dd>
          </dl>
        </section>
      </div>

      {shortfall && !simulated && (
        <Notice tone="bad">
          Your wallet holds {usd(held)} {coin} but these payments need {usd(total)}: only {covered} of {requests.length} can be funded right now.
        </Notice>
      )}

      <table className="table compact signing-table">
        <thead>
          <tr>
            <th>Creator</th>
            <th>Amount</th>
            <th>Escrow</th>
            <th>Expires</th>
          </tr>
        </thead>
        <tbody>
          {requests.map((r) => (
            <tr key={r.deal_id}>
              <td>
                <CreatorLink id={r.influencer_id}>{names.get(r.influencer_id) ?? r.influencer_id}</CreatorLink>
              </td>
              <td>
                <strong>{usd(r.amount)}</strong>
              </td>
              <td>
                <Address value={r.typed_data ? String(r.typed_data.message.to) : status?.escrow.address} explorer={explorer} />
              </td>
              <td className={r.expired ? "error" : "muted"}>
                {r.valid_before ? new Date(r.valid_before * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "checkout"}
                {r.expired && " · expired"}
                {r.problems
                  .filter((p) => !r.expired || !p.includes("expire"))
                  .map((p) => (
                    <div key={p} className="error small">
                      {p}
                    </div>
                  ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="row">
        {expired ? (
          <ActionButton onClick={renew}>Renew expired authorizations</ActionButton>
        ) : pending.signing === "browser" ? (
          <ActionButton onClick={signAll} disabled={!wallet.hasWallet() || blocked.length > 0}>
            Review &amp; sign in wallet
          </ActionButton>
        ) : (
          <ActionButton onClick={approveHosted}>Approve &amp; open checkout</ActionButton>
        )}
        <ActionButton variant="ghost" onClick={decline}>
          Decline
        </ActionButton>
        {progress && <span className="muted">{progress}</span>}
      </div>

      <details className="signing-more">
        <summary>What am I signing?</summary>
        <p className="small">
          {pending.signing === "browser" ? (
            <>
              One permission per creator for HSP to move exactly that amount of {coin} from your wallet into the escrow contract before it expires.
              Signing does not move money; a deal is funded only after the transfer is verified
              {simulated ? " (in the simulated ledger: no real USDC moves)" : ` on chain (${status?.settlement_min_confirmations ?? 1}+ confirmations)`}.
            </>
          ) : (
            "Approve, then pay each deal in HSP's hosted checkout. A deal is funded only after its settlement transaction is verified on chain."
          )}
        </p>
        <p className="small muted">
          {coin} at <span className="mono">{short(status?.token.address)}</span> on chain {status?.chain.id}. Rejecting in your wallet submits nothing.
          Never share your seed phrase: ZyroXQ only ever asks your wallet for signatures.
        </p>
      </details>
    </div>
  );
}

function DisputeForm({ dealId }: { dealId: string }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  if (!open)
    return (
      <button type="button" className="btn btn-danger" onClick={() => setOpen(true)}>
        Dispute before release
      </button>
    );
  return (
    <div className="row">
      <input className="grow" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="What's wrong with the post?" maxLength={500} />
      <ActionButton variant="danger" disabled={reason.trim().length < 3} onClick={() => api.dispute(dealId, reason)}>
        Freeze payout
      </ActionButton>
      <button type="button" className="btn btn-ghost" onClick={() => setOpen(false)}>
        Cancel
      </button>
    </div>
  );
}

function Assistant() {
  const { status } = useSession();
  const [q, setQ] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  if (!status?.llm) return null;
  return (
    <Card title="Ask the campaign assistant">
      <p className="muted small">It reads your campaigns through ZyroXQ's read-only MCP tools. It cannot move money.</p>
      <div className="row">
        <input className="grow" value={q} onChange={(e) => setQ(e.target.value)} placeholder="How much is still in escrow? Why was Raj rejected?" maxLength={1000} />
        <ActionButton
          disabled={q.trim().length < 2}
          onClick={async () => {
            setAnswer((await api.ask(q)).answer);
          }}
        >
          Ask
        </ActionButton>
      </div>
      {answer && <div className="answer">{answer}</div>}
    </Card>
  );
}

// A deal the list has not reloaded yet (its negotiation just started): enough for the live conversation to open
const pendingDeal = (s: NegotiationSession) =>
  ({ id: s.negotiation_id, campaign_id: s.campaign_id, influencer_id: s.creator_id, state: "NEGOTIATING" }) as Deal;

export function CampaignView() {
  const { id = "" } = useParams();
  useMe();
  const campaign = useResource(() => api.campaign(id), `c:${id}`);
  const deals = useResource(() => api.campaignDeals(id), `d:${id}`);
  const events = useResource(() => api.campaignEvents(id), `e:${id}`);
  const directory = useResource(api.directory, "directory");
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<"chat" | "audit">("chat");
  const [allChats, setAllChats] = useState(false); // every creator's conversation stacked, in the order they started
  const [pinned, setPinned] = useState<string | null>(null); // a conversation the advertiser opened; null follows the live one
  const queue = useNegotiationQueue(id);
  const [scope, setScope] = useState<"deal" | "campaign">("deal");
  const [query, setQuery] = useState("");

  const reloadAll = () => {
    void campaign.reload();
    void deals.reload();
    void events.reload();
  };
  useLiveEvents((e) => e.campaign_id === id, reloadAll);

  const names = useMemo(() => new Map((directory.data ?? []).map((d: DirectoryEntry) => [d.id, d.name])), [directory.data]);
  const avatars = useMemo(() => new Map((directory.data ?? []).map((d: DirectoryEntry) => [d.id, d.avatar_url])), [directory.data]);
  // Your agent's reason for each of its moves (private audit events), shown under its message in your view of the chat
  const agentNotes = useMemo(() => {
    const byDeal = new Map<string, Record<number, string>>();
    const moves = (events.data ?? []).filter((e) => e.kind === "negotiation_move" && e.deal_id && typeof e.detail.reason === "string" && e.detail.reason);
    for (const e of [...moves].sort((a, b) => a.at.localeCompare(b.at))) {
      const notes = byDeal.get(e.deal_id!) ?? {};
      // older events carry no round: the n-th move answers round n and opens round n + 1
      const round = typeof e.detail.round === "number" ? e.detail.round : Object.keys(notes).length + 2;
      notes[round] = String(e.detail.reason);
      byDeal.set(e.deal_id!, notes);
    }
    return byDeal;
  }, [events.data]);
  const list = deals.data ?? [];
  // pick the first deal so the chat is never empty when there is something to show
  useEffect(() => {
    if (!selected && list.length) setSelected(list[0].id);
  }, [selected, list]);

  if (campaign.error) return <ErrorText error={campaign.error} />;
  if (!campaign.data) return <Spinner />;
  const c = campaign.data;
  const money = spend(list);
  const deal = list.find((d) => d.id === selected) ?? null;
  const sessions = queue.sessions;
  const started = sessions.filter((s) => s.started_at);
  const model = c.spec.compensation_model as CompensationModel;
  // The conversation on screen: the one the advertiser picked, else whichever creator the backend is talking to now
  const focusId = pinned ?? queue.progress?.current_negotiation_id ?? started.at(-1)?.negotiation_id ?? null;
  const focusSession = sessions.find((s) => s.negotiation_id === focusId) ?? null;
  const focusDeal = focusSession ? (list.find((d) => d.id === focusSession.negotiation_id) ?? pendingDeal(focusSession)) : null;
  const creatorName = (creatorId: string) => names.get(creatorId) ?? creatorId;
  const notesFor = (dealId: string) => agentNotes.get(dealId);
  const name = (d: Deal) => names.get(d.influencer_id) ?? d.influencer_id;
  const q = query.trim().toLowerCase();
  const rows = list.filter((d) => !q || name(d).toLowerCase().includes(q) || d.state.toLowerCase().includes(q));
  const trail = (events.data ?? []).filter((e) => scope === "campaign" || !deal || e.deal_id === deal.id);
  const working = c.status === "planning" || c.status === "negotiating";

  return (
    <div className="report">
      <header className="report-head">
        <div className="report-title">
          <Breadcrumbs items={[{ label: "Dashboard", to: "/advertiser" }, { label: "All campaigns", to: "/advertiser/campaigns" }, { label: c.spec.advertiser }]} />
          <div className="row">
            <h1>
              {c.spec.advertiser} <span className="muted">{c.spec.brand_handle}</span>
            </h1>
            <CampaignBadge status={c.status} />
            {working && (
              <span className="live-chip">
                <span className="live-dot on" /> Live
              </span>
            )}
          </div>
          <ul className="report-meta" aria-label="Campaign terms">
            <li>{c.spec.niche}</li>
            <li>{MODEL_LABEL[c.spec.compensation_model] ?? c.spec.compensation_model}</li>
            <li>Up to {c.spec.max_creators} creators</li>
            <li>
              Max {usd(c.spec.max_fixed)} fixed + {usd(c.spec.max_cpm)} CPM
            </li>
            <li>Cap {usd(c.spec.cap_per_creator)} each</li>
            <li>{c.spec.max_rounds} rounds</li>
          </ul>
        </div>
        <DevClock />
      </header>

      {c.error && <Notice tone="bad">{c.error}</Notice>}
      {c.pending_approval && <SigningPanel campaign={c} names={names} onDone={reloadAll} />}
      {!c.pending_approval && queue.progress && queue.progress.agreed_count > 0 && (working || queue.progress.workflow_status === "running") && (
        // Payments are authorized once, for every agreed creator together, after the whole creator queue has finished
        <Notice>
          {queue.progress.workflow_status === "running" ? (
            <>
              <strong>
                {queue.progress.agreed_count} creator{queue.progress.agreed_count > 1 ? "s" : ""} agreed so far.
              </strong>{" "}
              Nothing to sign yet: the payment authorization is prepared once, for all agreed creators together, after the remaining negotiations finish (
              {queue.progress.completed_count} of {queue.progress.total_creators} done).
            </>
          ) : (
            <>
              <strong>All negotiations finished.</strong> Preparing the payment authorization for {queue.progress.agreed_count} agreed creator
              {queue.progress.agreed_count > 1 ? "s" : ""}…
            </>
          )}
        </Notice>
      )}

      <div className="report-grid">
        <div className="report-main">
          <section className="report-kpis" aria-label="Money">
            <Stat label="Budget" value={usd(c.spec.budget)} />
            <Stat label="Locked via HSP" value={usd(money.locked)} />
            <Stat label="Paid to creators" value={usd(money.paid)} />
            <Stat label="Refunded" value={usd(money.refunded)} />
            <Stat label="Still in escrow" value={usd(money.inEscrow)} />
          </section>

          <section className="report-card">{events.data ? <ActivityChart events={events.data} deals={list} /> : <Spinner />}</section>

          <section className="report-card">
            <div className="report-toolbar">
              <h2>Deals ({list.length})</h2>
              <label className="camp-search">
                <input type="search" placeholder="Search creator or state…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search deals" />
              </label>
            </div>
            {list.length === 0 ? (
              <Empty>{working ? "Your agent is finding creator agents. Deals appear here as soon as talks start." : "No deals for this campaign."}</Empty>
            ) : (
              <div className="report-table">
                <table className="table selectable">
                  <thead>
                    <tr>
                      <th>Creator</th>
                      <th>State</th>
                      <th className="num">Fixed</th>
                      <th className="num">CPM</th>
                      <th className="num">Cap</th>
                      <th className="num">Rounds</th>
                      <th className="num">Paid</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((d) => (
                      <tr key={d.id} className={d.id === selected ? "selected" : ""} onClick={() => setSelected(d.id)}>
                        <td>
                          <span className="who-cell">
                            <Avatar src={avatars.get(d.influencer_id)} name={name(d)} size={24} />
                            <CreatorLink id={d.influencer_id}>{name(d)}</CreatorLink>
                          </span>
                        </td>
                        <td>
                          <StateBadge state={d.state} />
                        </td>
                        <td className="num">{usd(d.terms?.fixed_fee)}</td>
                        <td className="num">{usd(d.terms?.cpm)}</td>
                        <td className="num">{usd(d.terms?.cap)}</td>
                        <td className="num">{d.rounds.length}</td>
                        <td className="num">{usd(d.payouts.filter((p) => p.kind !== "refund").reduce((s, p) => s + Number(p.amount), 0))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {deal && (
            <DealDetail
              deal={deal}
              title={
                <span>
                  <CreatorLink id={deal.influencer_id}>{name(deal)}</CreatorLink> <span className="muted small mono">{deal.influencer_id}</span>
                </span>
              }
              actions={
                <>
                  {deal.state === "AWAITING_CHECKOUT" && deal.payment_url && (
                    <a className="btn btn-primary" href={deal.payment_url} target="_blank" rel="noreferrer">
                      Pay in HSP checkout
                    </a>
                  )}
                  {deal.state === "VERIFIED" && (c.spec.review_required || needsReview(deal.proof?.content)) && <ReviewForm dealId={deal.id} onDone={reloadAll} />}
                  {deal.state === "VERIFIED" && <DisputeForm dealId={deal.id} />}
                </>
              }
            />
          )}

          <ComplianceCard campaignId={c.spec.id} names={names} />

          <Card title="Content requirements">
            <Requirements deliverable={c.spec.deliverable} min={c.spec.min_duration_seconds} max={c.spec.max_duration_seconds} text={c.spec.requirements} />
          </Card>

          <Assistant />
        </div>

        <aside className="report-side" aria-label="Agent conversation and audit trail">
          <div className="side-tabs" role="tablist">
            <button type="button" role="tab" aria-selected={tab === "chat"} className={tab === "chat" ? "on" : ""} onClick={() => setTab("chat")}>
              A2A chat
            </button>
            <button type="button" role="tab" aria-selected={tab === "audit"} className={tab === "audit" ? "on" : ""} onClick={() => setTab("audit")}>
              Audit trail <span>{trail.length}</span>
            </button>
          </div>

          {list.length > 0 && (tab === "audit" || sessions.length === 0) && (
            <label className="side-pick">
              <span className="sr-only">Deal</span>
              <select value={selected ?? ""} onChange={(e) => setSelected(e.target.value)}>
                {list.map((d) => (
                  <option key={d.id} value={d.id}>
                    {name(d)} · {humanize(d.state)}
                  </option>
                ))}
              </select>
            </label>
          )}

          <div className="side-body">
            {tab === "chat" ? (
              sessions.length === 0 && list.length > 0 ? (
                // campaigns from before the creator queue existed
                deal && (
                  <LiveNegotiation
                    key={deal.id}
                    deal={deal}
                    viewer="advertiser"
                    notes={notesFor(deal.id)}
                    creator={name(deal)}
                    avatar={avatars.get(deal.influencer_id)}
                    brand={c.spec.advertiser}
                    limits={{ budget: c.spec.budget, max_fixed: c.spec.max_fixed, max_cpm: c.spec.max_cpm, cap: c.spec.cap_per_creator }}
                    deliverable={c.spec.deliverable}
                  />
                )
              ) : (
                <>
                  {working || sessions.length > 0 ? (
                    <NegotiationQueue
                      progress={queue.progress}
                      sessions={sessions}
                      name={creatorName}
                      avatar={(i) => avatars.get(i)}
                      model={model}
                      focus={focusId}
                      following={pinned === null}
                      all={allChats}
                      onFocus={(negotiationId) => {
                        setPinned(negotiationId);
                        setSelected(negotiationId);
                        setAllChats(false);
                      }}
                      onFollow={() => setPinned(null)}
                      onAll={() => setAllChats((v) => !v)}
                      canCancel
                    />
                  ) : null}
                  {queue.error && <p className="error small">{queue.error}</p>}
                  {queue.progress && <NegotiationSummary progress={queue.progress} sessions={sessions} name={creatorName} model={model} />}
                  {allChats ? (
                    <div className="ln-stack">
                      {started.map((s) => {
                        const d = list.find((x) => x.id === s.negotiation_id) ?? pendingDeal(s);
                        return (
                          <LiveNegotiation
                            key={s.negotiation_id}
                            compact
                            deal={d}
                            viewer="advertiser"
                            notes={notesFor(d.id)}
                            creator={creatorName(s.creator_id)}
                            avatar={avatars.get(s.creator_id)}
                            brand={c.spec.advertiser}
                            onOpen={() => {
                              setPinned(s.negotiation_id);
                              setSelected(s.negotiation_id);
                              setAllChats(false);
                            }}
                          />
                        );
                      })}
                    </div>
                  ) : focusDeal && focusSession ? (
                    <LiveNegotiation
                      key={focusDeal.id}
                      deal={focusDeal}
                      viewer="advertiser"
                      notes={notesFor(focusDeal.id)}
                      creator={creatorName(focusSession.creator_id)}
                      avatar={avatars.get(focusSession.creator_id)}
                      brand={c.spec.advertiser}
                      limits={{ budget: c.spec.budget, max_fixed: c.spec.max_fixed, max_cpm: c.spec.max_cpm, cap: c.spec.cap_per_creator }}
                      deliverable={c.spec.deliverable}
                    />
                  ) : (
                    <Empty>{working ? "Advertiser agent is preparing the first offer…" : "No conversations for this campaign."}</Empty>
                  )}
                </>
              )
            ) : (
              <>
                <div className="seg side-scope">
                  <button type="button" className={scope === "deal" ? "on" : ""} onClick={() => setScope("deal")} disabled={!deal}>
                    This deal
                  </button>
                  <button type="button" className={scope === "campaign" ? "on" : ""} onClick={() => setScope("campaign")}>
                    Whole campaign
                  </button>
                </div>
                {events.data ? <Timeline events={trail} limit={80} /> : <Spinner />}
              </>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
