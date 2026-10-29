import { useEffect, useState } from "react";
import { api } from "../../api";
import { AgentPolicyBuilder } from "../../components/AgentPolicyBuilder";
import { AgentSettingsForm } from "../../components/AgentSettingsForm";
import { EvidenceTable } from "../../components/Evidence";
import { Address, Card, Notice } from "../../components/ui";
import { useMe } from "../../session";
import type { EvidenceItem } from "../../types";

export function AgentPage() {
  const me = useMe();
  const u = me.user;
  const cardPath = me.agent_url ? new URL(".well-known/agent-card.json", me.agent_url).pathname : null;
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  useEffect(() => {
    api.myEvidence().then(setEvidence).catch(() => setEvidence([]));
  }, []);
  return (
    <div className="stack narrow">
      <h1>My negotiation agent</h1>
      <Card title="How your agent works">
        <ul className="bullets">
          <li>Advertiser agents find it through its public A2A Agent Card and send offers.</li>
          <li>
            It counters from your opening ask and never accepts below your floors. That rule is plain code, not the LLM, so it holds even when the AI
            suggests otherwise.
          </li>
          <li>
            When it accepts, it signs the deal terms with its agent key <Address value={u.agent_signer} />. Your wallet <Address value={u.wallet} />{" "}
            authorized that key with the signature you gave at setup, and payouts go only to that wallet.
          </li>
          <li>Offers from other agents are treated as data. Instructions hidden inside them are ignored.</li>
        </ul>
        {cardPath && (
          <p>
            Agent Card:{" "}
            <a href={cardPath} target="_blank" rel="noreferrer" className="mono">
              {me.agent_url}.well-known/agent-card.json
            </a>
          </p>
        )}
        {!me.agent_listed && <Notice tone="warn">Your agent is paused or not fully set up, so advertisers can't discover it.</Notice>}
      </Card>
      <Card title="Rates and style">
        <AgentSettingsForm />
      </Card>
      <Card title="What advertisers see about your channel">
        <p className="small muted">Every metric shows where it comes from. ZyroXQ has no YouTube Analytics access, so demographics and engagement stay unknown.</p>
        <EvidenceTable items={evidence} />
      </Card>
      <h2>Agent profile</h2>
      <AgentPolicyBuilder />
    </div>
  );
}
