import { api } from "../../api";
import { Avatar, Card, Empty, ErrorText, Spinner } from "../../components/ui";
import { num, short } from "../../format";
import { useResource } from "../../live";

export function Creators() {
  const directory = useResource(api.directory, "directory");
  return (
    <div className="stack">
      <div className="page-head">
        <h1>Creator agents</h1>
      </div>
      <p className="muted">
        Every creator here signed in with Google, proved their YouTube channel and authorized their agent with their wallet. Your advertiser agent
        discovers them through their A2A Agent Cards. Rate floors stay private to each creator's agent.
      </p>
      <Card>
        <ErrorText error={directory.error} />
        {!directory.data ? (
          <Spinner />
        ) : directory.data.length === 0 ? (
          <Empty>No creator agents are live yet. Invite creators to sign in with “Continue as an influencer”.</Empty>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Creator</th>
                <th>Niche</th>
                <th>Subscribers</th>
                <th>Payout wallet</th>
                <th>Agent Card</th>
              </tr>
            </thead>
            <tbody>
              {directory.data.map((d) => (
                <tr key={d.id}>
                  <td>
                    <span className="who-cell">
                      <Avatar src={d.avatar_url} name={d.name} size={28} />
                      <span>
                        {d.name}
                        <div className="muted small mono">{d.channel_id}</div>
                      </span>
                    </span>
                  </td>
                  <td>{d.niche}</td>
                  <td>{num(d.subscribers)}</td>
                  <td className="mono">{short(d.wallet)}</td>
                  <td>
                    <a href={new URL(".well-known/agent-card.json", d.agent_url).pathname} target="_blank" rel="noreferrer">
                      agent-card.json
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
