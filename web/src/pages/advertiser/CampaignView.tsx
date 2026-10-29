import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router";
import { api } from "../../api";
import { ComplianceCard, ReviewForm } from "../../components/Compliance";
import { DealDetail } from "../../components/DealDetail";
import { Requirements } from "../../components/Requirements";
import { Timeline } from "../../components/Timeline";
import { CreatorLink } from "../../components/CreatorLink";
import { ActionButton, Address, Avatar, CampaignBadge, Card, Empty, ErrorText, Notice, Spinner, StateBadge, Stat } from "../../components/ui";
import { MODEL_LABEL, short, usd } from "../../format";
import { useLiveEvents, useResource } from "../../live";
import { useMe, useSession } from "../../session";
import type { CampaignDetail, Deal, DirectoryEntry, WalletBalance } from "../../types";
import * as wallet from "../../wallet";

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

  return (
    <div className="signing card tone-warn">
      <h2>
        Authorize {requests.length} payment{requests.length > 1 ? "s" : ""}: {usd(total)} into escrow
      </h2>
      {simulated && (
        <Notice tone="warn">
          <strong>Simulated settlement.</strong> HSP and the escrow run in-process: no real USDC moves, and payouts and refunds are ledger entries
          only. Your wallet still signs real EIP-712 data.
        </Notice>
      )}
      <p>
        {pending.signing === "browser" ? (
          <>
            Your wallet signs one EIP-3009 <code>TransferWithAuthorization</code> per creator: permission for HSP to move exactly that amount of{" "}
            {status?.token.coin ?? "USDC"} from your wallet to the escrow contract before the expiry. <strong>Signing does not move money</strong>{" "}
            and does not guarantee funding. A deal is funded only after the settlement transaction is verified
            {simulated ? " (in the simulated ledger)" : ` on chain (${status?.settlement_min_confirmations ?? 1}+ confirmations)`}.
          </>
        ) : (
          "Approve, then pay each deal in HSP's hosted checkout. A deal is funded only after its settlement transaction is verified on chain."
        )}
      </p>

      <div className="stats compact-stats">
        <Stat label="Campaign budget" value={usd(campaign.funding.budget)} hint="A spending limit, not money held" />
        <Stat label="Reserved (incl. these)" value={usd(campaign.funding.reserved)} hint="Caps of deals being paid or already paid" />
        <Stat label="Funded" value={usd(campaign.funding.funded)} hint="Verified settlements locked in escrow" />
        <Stat label="Remaining budget" value={usd(campaign.funding.remaining)} />
      </div>

      <dl className="kv">
        <dt>Payer wallet</dt>
        <dd>
          <Address value={pending.payer} explorer={explorer} />
          {wrongAccount && <span className="chip warn">MetaMask is on {short(account)}</span>}
        </dd>
        <dt>Network</dt>
        <dd>
          {status?.chain.name} ({status?.chain.id})
          {wrongChain && <span className="chip warn">wallet is on chain {chainId}: it will be asked to switch</span>}
        </dd>
        <dt>{status?.token.coin ?? "USDC"} balance</dt>
        <dd>
          {balance === null ? (
            <span className="muted">unknown</span>
          ) : balance.balance === null ? (
            <span className="muted">{balance.error}</span>
          ) : (
            <>
              {usd(balance.balance)} <span className="muted small">checked {new Date(balance.checked_at).toLocaleTimeString()}</span>
            </>
          )}{" "}
          <button type="button" className="link" onClick={() => void refresh()}>
            refresh
          </button>
        </dd>
      </dl>
      {shortfall &&
        (simulated ? (
          <Notice tone="info">
            Your wallet holds {usd(held)} of the {usd(total)} these deals would need on a real network. Simulated mode does not use it.
          </Notice>
        ) : (
          <Notice tone="bad">
            <strong>Insufficient balance:</strong> your wallet holds {usd(held)} {status?.token.coin} but these deals need {usd(total)}. Each deal
            settles on its own: as things stand about {covered} of {requests.length} can be funded, and the others will fail with nothing locked.
            Balances can change before settlement, so this is a warning, not a guarantee.
          </Notice>
        ))}

      <table className="table compact">
        <thead>
          <tr>
            <th>Creator</th>
            <th>Amount</th>
            <th>Recipient (escrow)</th>
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
                {usd(r.amount)} {status?.token.coin}
              </td>
              <td>
                <Address value={r.typed_data ? String(r.typed_data.message.to) : status?.escrow.address} explorer={explorer} />
              </td>
              <td className={r.expired ? "error" : "muted"}>
                {r.valid_before ? new Date(r.valid_before * 1000).toLocaleTimeString() : "checkout"}
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
          Decline: sign nothing
        </ActionButton>
        {progress && <span className="muted">{progress}</span>}
      </div>
      <p className="muted small">
        Token {status?.token.coin} at <span className="mono">{short(status?.token.address)}</span> on chain {status?.chain.id}. Rejecting in your wallet
        submits nothing. Never share your seed phrase or private key: ZyroXQ only ever asks your wallet for signatures.
      </p>
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

export function CampaignView() {
  const { id = "" } = useParams();
  useMe();
  const campaign = useResource(() => api.campaign(id), `c:${id}`);
  const deals = useResource(() => api.campaignDeals(id), `d:${id}`);
  const events = useResource(() => api.campaignEvents(id), `e:${id}`);
  const directory = useResource(api.directory, "directory");
  const [selected, setSelected] = useState<string | null>(null);

  const reloadAll = () => {
    void campaign.reload();
    void deals.reload();
    void events.reload();
  };
  useLiveEvents((e) => e.campaign_id === id, reloadAll);

  const names = useMemo(() => new Map((directory.data ?? []).map((d: DirectoryEntry) => [d.id, d.name])), [directory.data]);
  const avatars = useMemo(() => new Map((directory.data ?? []).map((d: DirectoryEntry) => [d.id, d.avatar_url])), [directory.data]);

  if (campaign.error) return <ErrorText error={campaign.error} />;
  if (!campaign.data) return <Spinner />;
  const c = campaign.data;
  const list = deals.data ?? [];
  const money = spend(list);
  const deal = list.find((d) => d.id === selected) ?? null;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <Link to="/advertiser" className="muted small">
            ← Campaigns
          </Link>
          <h1>
            {c.spec.advertiser} <span className="muted">{c.spec.brand_handle}</span>
          </h1>
          <p className="muted small">
            {c.spec.niche} · {MODEL_LABEL[c.spec.compensation_model] ?? c.spec.compensation_model} · up to {c.spec.max_creators} creators · max {usd(c.spec.max_fixed)}{" "}
            fixed + {usd(c.spec.max_cpm)} CPM · cap {usd(c.spec.cap_per_creator)} each · {c.spec.max_rounds} rounds · agent policy{" "}
            {c.spec.agent_policy_version ? `v${c.spec.agent_policy_version}` : "defaults"} · {c.spec.disclosure_policies.join(", ")} ·{" "}
            <span className="mono">{c.spec.id}</span>
          </p>
        </div>
        <CampaignBadge status={c.status} />
      </div>
      {c.error && <Notice tone="bad">{c.error}</Notice>}
      {c.status === "planning" || c.status === "negotiating" ? (
        <Notice>Your advertiser agent is discovering creator agents and negotiating over A2A. This page updates live.</Notice>
      ) : null}

      {c.pending_approval && <SigningPanel campaign={c} names={names} onDone={reloadAll} />}

      <div className="stats">
        <Stat label="Budget" value={usd(c.spec.budget)} />
        <Stat label="Locked via HSP" value={usd(money.locked)} />
        <Stat label="Paid to creators" value={usd(money.paid)} />
        <Stat label="Refunded" value={usd(money.refunded)} />
        <Stat label="Still in escrow" value={usd(money.inEscrow)} />
      </div>

      <div className="split">
        <Card title={`Deals (${list.length})`}>
          {list.length === 0 ? (
            <Empty>No deals yet.</Empty>
          ) : (
            <table className="table selectable">
              <thead>
                <tr>
                  <th>Creator</th>
                  <th>State</th>
                  <th>Fixed</th>
                  <th>CPM</th>
                  <th>Rounds</th>
                  <th>Paid</th>
                </tr>
              </thead>
              <tbody>
                {list.map((d) => (
                  <tr key={d.id} className={d.id === selected ? "selected" : ""} onClick={() => setSelected(d.id)}>
                    <td>
                      <span className="who-cell">
                        <Avatar src={avatars.get(d.influencer_id)} name={names.get(d.influencer_id) ?? d.influencer_id} size={24} />
                        <CreatorLink id={d.influencer_id}>{names.get(d.influencer_id) ?? d.influencer_id}</CreatorLink>
                      </span>
                    </td>
                    <td>
                      <StateBadge state={d.state} />
                    </td>
                    <td>{usd(d.terms?.fixed_fee)}</td>
                    <td>{usd(d.terms?.cpm)}</td>
                    <td>{d.rounds.length}</td>
                    <td>{usd(d.payouts.filter((p) => p.kind !== "refund").reduce((s, p) => s + Number(p.amount), 0))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        {deal ? (
          <DealDetail
            deal={deal}
            title={
              <span>
                <CreatorLink id={deal.influencer_id}>{names.get(deal.influencer_id) ?? deal.influencer_id}</CreatorLink>{" "}
                <span className="muted small mono">{deal.influencer_id}</span>
              </span>
            }
            actions={
              <>
                {deal.state === "AWAITING_CHECKOUT" && deal.payment_url && (
                  <a className="btn btn-primary" href={deal.payment_url} target="_blank" rel="noreferrer">
                    Pay in HSP checkout
                  </a>
                )}
                {deal.state === "VERIFIED" && c.spec.review_required && <ReviewForm dealId={deal.id} onDone={reloadAll} />}
                {deal.state === "VERIFIED" && <DisputeForm dealId={deal.id} />}
              </>
            }
          />
        ) : (
          <Card title="Audit trail">
            <p className="muted small">Select a deal for its negotiation, signed terms, payment, proof and payouts.</p>
            {events.data ? <Timeline events={events.data} limit={40} /> : <Spinner />}
          </Card>
        )}
      </div>

      <ComplianceCard campaignId={c.spec.id} names={names} />

      <Card title="Content requirements">
        <Requirements
          deliverable={c.spec.deliverable}
          min={c.spec.min_duration_seconds}
          max={c.spec.max_duration_seconds}
          text={c.spec.requirements}
        />
      </Card>

      <Assistant />
    </div>
  );
}
