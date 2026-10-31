import { AgentLessons, AgentPerformance } from "../../components/AgentPerformance";
import { AgentPolicyBuilder } from "../../components/AgentPolicyBuilder";
import { Card } from "../../components/ui";

export function AgentProfile() {
  return (
    <div className="stack narrow">
      <h1>My advertiser agent</h1>
      <Card title="How your agent decides">
        <ul className="bullets">
          <li>Each campaign's money ceilings (max fixed fee, max CPM, escrow cap, budget) are hard limits the agent cannot cross.</li>
          <li>This profile adds evidence requirements, an opening strategy, a concession speed and when to hand a counter-offer to you.</li>
          <li>A new campaign uses the latest saved version; campaigns already running keep the version they started with.</li>
          <li>The AI may suggest a move. Code checks it against these rules, and you still sign every payment in your own wallet.</li>
        </ul>
      </Card>
      <AgentPerformance />
      <AgentLessons />
      <AgentPolicyBuilder />
    </div>
  );
}
