import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";
import { api } from "../../api";
import { DealDetail } from "../../components/DealDetail";
import { Timeline } from "../../components/Timeline";
import { ActionButton, Avatar, CampaignBadge, Card, Empty, ErrorText, Notice, Spinner, StateBadge, Stat } from "../../components/ui";
import { usd } from "../../format";
import { useLiveEvents, useResource } from "../../live";
import { useMe, useSession } from "../../session";
import type { CampaignDetail, Deal, DirectoryEntry } from "../../types";
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
  const [progress, setProgress] = useState<string | null>(null);

  async function signAll() {
    const account = await wallet.connect();
    if (!wallet.sameAddress(account, pending.payer)) {
      throw new Error(`Switch MetaMask to the campaign's payer wallet ${pending.payer} (currently ${account}).`);
    }
    if (status) await wallet.ensureChain(status.chain);
    const signatures: Record<string, string> = {};
    for (const [i, r] of campaign.signing_requests.entries()) {
      if (!r.typed_data) continue;
      setProgress(`Signing ${i + 1} of ${campaign.signing_requests.length}: ${names.get(r.influencer_id) ?? r.influencer_id}…`);
      signatures[r.deal_id] = await wallet.signTypedData(account, r.typed_data);
    }
    setProgress("Submitting signed mandates to HSP…");
    try {
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

  return (
    <div className="signing card tone-warn">
      <h2>Your signature is needed: {usd(pending.total)} into escrow</h2>
      <p>
        Your agent agreed terms with {campaign.signing_requests.length} creator{campaign.signing_requests.length > 1 ? "s" : ""}, and every Cart Mandate passed the policy
        checks. {pending.signing === "browser"
          ? "Each payment is an EIP-3009 transfer to the escrow contract, so your wallet signs one EIP-712 Payment Mandate per creator."
          : "Approve, then pay each deal in HSP's hosted checkout."}
      </p>
      <table className="table compact">
        <tbody>
          {campaign.signing_requests.map((r) => (
            <tr key={r.deal_id}>
              <td>{names.get(r.influencer_id) ?? r.influencer_id}</td>
              <td>{usd(r.amount)}</td>
              <td className="muted small">
                to escrow {r.typed_data ? String(r.typed_data.message.to).slice(0, 10) + "…" : ""} · valid until{" "}
                {r.typed_data ? new Date(Number(r.typed_data.message.validBefore) * 1000).toLocaleTimeString() : "checkout"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row">
        {pending.signing === "browser" ? (
          <ActionButton onClick={signAll} disabled={!wallet.hasWallet()}>
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
      <p className="muted small">Payer wallet {pending.payer}. Nothing moves until you sign. Mandates expire one hour after they are drafted.</p>
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
            {c.spec.niche} · up to {c.spec.max_creators} creators · max {usd(c.spec.max_fixed)} fixed + {usd(c.spec.max_cpm)} CPM · cap {usd(c.spec.cap_per_creator)} each ·{" "}
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
                        {names.get(d.influencer_id) ?? d.influencer_id}
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
            title={names.get(deal.influencer_id) ?? deal.influencer_id}
            actions={
              <>
                {deal.state === "AWAITING_CHECKOUT" && deal.payment_url && (
                  <a className="btn btn-primary" href={deal.payment_url} target="_blank" rel="noreferrer">
                    Pay in HSP checkout
                  </a>
                )}
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

      <Assistant />
    </div>
  );
}
